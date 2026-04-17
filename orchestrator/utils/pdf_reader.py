"""Robust PDF attachment reader.

Strategy:
1. Detect type: digital / escaneado / protegido / corrompido
2. Extract structured fields via regex (CPF/CNPJ masked)
3. Handle password-protected PDFs with 3 cenários:
   - cadastradas (pdf_passwords)
   - inferidas do corpo + account_documents (CPF/CNPJ/nascimento)
   - desconhecidas → devolve leitura_sucesso=False com motivo claro

Rule: if we could not read it, we do NOT pretend we did.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from typing import Any, Dict, List, Optional

import httpx
import pdfplumber

logger = logging.getLogger(__name__)

MAX_PAGES_FULL = 10
FIRST_PAGES = 5
LAST_PAGES = 2
MAX_CHARS = 15000

# ── Regex for structured extraction ──
_RE_CPF = re.compile(r"\b(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})\b")
_RE_CNPJ = re.compile(r"\b(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})\b")
_RE_VALOR = re.compile(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}|R\$\s*\d+,\d{2}")
_RE_DATA = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_RE_PROTOCOLO = re.compile(
    r"(?:protocolo|ref\.?|n[ºo°])\s*[:.]?\s*([A-Za-z0-9\-/_.]+)",
    re.IGNORECASE,
)

# ── Password / body hints ──
_HINT_CPF = re.compile(r"\b(cpf|documento)\b", re.IGNORECASE)
_HINT_CNPJ = re.compile(r"\bcnpj\b", re.IGNORECASE)
_HINT_NASC = re.compile(r"\b(nascimento|data\s+de\s+nasc)", re.IGNORECASE)


def _mask_cpf(m: re.Match) -> str:
    return f"***.***.***-{m.group(4)}"


def _mask_cnpj(m: re.Match) -> str:
    return f"**.***.***/****-{m.group(5)}"


def _extract_fields(text: str) -> Dict[str, List[str]]:
    """Run regex extraction over text. Returns dict (always, possibly empty lists).

    CPF/CNPJ are returned *masked* — we never surface full document numbers.
    """
    if not text:
        return {"valores_brl": [], "datas": [], "cpfs": [], "cnpjs": [], "protocolos": []}

    valores = sorted(set(m.group(0).replace("  ", " ") for m in _RE_VALOR.finditer(text)))
    datas = sorted(set(_RE_DATA.findall(text)))
    cpfs = sorted({_mask_cpf(m) for m in _RE_CPF.finditer(text)})
    cnpjs = sorted({_mask_cnpj(m) for m in _RE_CNPJ.finditer(text)})
    protocolos = []
    seen = set()
    for m in _RE_PROTOCOLO.finditer(text):
        val = m.group(1).strip(".,;:")
        if val and val not in seen:
            seen.add(val)
            protocolos.append(val)

    return {
        "valores_brl": valores,
        "datas": datas,
        "cpfs": cpfs,
        "cnpjs": cnpjs,
        "protocolos": protocolos,
    }


def _select_pages(pages: List[Any]) -> List[Any]:
    if len(pages) <= MAX_PAGES_FULL:
        return pages
    return pages[:FIRST_PAGES] + pages[-LAST_PAGES:]


def _is_password_error(exc: Exception) -> bool:
    """Detect password-required errors from pdfplumber/pypdf/pdfminer."""
    msg = str(exc).lower()
    if "password" in msg or "encrypt" in msg or "decrypt" in msg:
        return True
    name = type(exc).__name__.lower()
    return "password" in name or "encrypt" in name


def _is_encrypted_pdf(pdf_bytes: bytes) -> bool:
    """Quick probe via pypdf — returns True if the PDF requires a password."""
    try:
        import pypdf
        r = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return bool(r.is_encrypted)
    except Exception:
        return False


def _try_open_with_password(
    pdf_bytes: bytes, password: Optional[str],
) -> Optional[str]:
    """Attempt to open the PDF with `password` using pypdf and return full text.

    Returns extracted text on success, None on failure (wrong password or error).
    """
    try:
        import pypdf
    except ImportError:
        logger.error("pypdf not installed — cannot open password-protected PDFs")
        return None

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            if password is None:
                return None
            try:
                ok = reader.decrypt(password)
            except Exception as exc:
                logger.debug(f"pypdf decrypt raised: {exc}")
                return None
            # pypdf returns 0 for failure, 1 or 2 for success
            if not ok:
                return None
        pages = reader.pages[:MAX_PAGES_FULL] if len(reader.pages) <= MAX_PAGES_FULL \
            else list(reader.pages[:FIRST_PAGES]) + list(reader.pages[-LAST_PAGES:])
        parts = []
        for p in pages:
            try:
                t = p.extract_text() or ""
                if t.strip():
                    parts.append(t)
            except Exception:
                continue
        return "\n\n".join(parts) if parts else ""
    except Exception as exc:
        logger.debug(f"pypdf open failed: {exc}")
        return None


def _inferred_passwords_from_body(
    body_text: str, docs_plain: Dict[str, Optional[str]],
) -> List[str]:
    """Given email body and decrypted docs (cpf/cnpj/birthdate=YYYY-MM-DD),
    produce candidate passwords to try. De-duplicated, order preserved.
    """
    body = body_text or ""
    candidates: List[str] = []

    cpf = (docs_plain.get("cpf") or "").strip()
    cnpj = (docs_plain.get("cnpj") or "").strip()
    bdate = (docs_plain.get("birthdate") or "").strip()

    cpf_digits = re.sub(r"\D", "", cpf) if cpf else ""
    cnpj_digits = re.sub(r"\D", "", cnpj) if cnpj else ""

    yyyy = mm = dd = ""
    if bdate and len(bdate) >= 10 and bdate[4] == "-" and bdate[7] == "-":
        yyyy, mm, dd = bdate[0:4], bdate[5:7], bdate[8:10]

    mentions_cpf = bool(_HINT_CPF.search(body))
    mentions_cnpj = bool(_HINT_CNPJ.search(body))
    mentions_nasc = bool(_HINT_NASC.search(body))

    def _add(p: str):
        if p and p not in candidates:
            candidates.append(p)

    if mentions_cpf and cpf_digits:
        _add(cpf_digits)
    if mentions_cnpj and cnpj_digits:
        _add(cnpj_digits)
    if mentions_nasc and dd and mm and yyyy:
        _add(f"{dd}{mm}{yyyy}")
        _add(f"{dd}{mm}{yyyy[2:]}")
        _add(f"{yyyy}{mm}{dd}")
        _add(f"{dd}/{mm}/{yyyy}")
    if mentions_cpf and mentions_nasc and cpf_digits and yyyy:
        _add(f"{cpf_digits}{yyyy}")
        if dd and mm:
            _add(f"{cpf_digits}{dd}{mm}")
            _add(f"{cpf_digits}{dd}{mm}{yyyy}")
    return candidates


async def extract_pdf_attachment(
    pdf_bytes: bytes,
    filename: str,
    *,
    reader: Optional["PdfReader"] = None,
    passwords_cadastradas: Optional[List[Dict[str, Any]]] = None,
    inferred_candidates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Extract text + structured fields from a PDF attachment.

    Args:
        pdf_bytes: raw PDF bytes
        filename: original filename (for logging / result)
        reader: optional PdfReader instance (used for OCR fallback via vision)
        passwords_cadastradas: list of dicts with decrypted key 'password'
            (plaintext, decrypted by caller) and 'id' (pdf_passwords row id).
            Caller is responsible for filtering out locked rows before passing.
        inferred_candidates: list of plaintext passwords to try before failing.

    Returns normalized dict:
        {filename, tipo, texto, campos, leitura_sucesso, motivo_falha, senha_usada_hash}
    """
    from orchestrator.utils.crypto import hash_password

    result: Dict[str, Any] = {
        "filename": filename,
        "tipo": None,
        "texto": None,
        "campos": {"valores_brl": [], "datas": [], "cpfs": [], "cnpjs": [], "protocolos": []},
        "leitura_sucesso": False,
        "motivo_falha": None,
        "senha_usada_hash": None,
        "matched_password_id": None,   # caller may use to touch usage counter
        "inferred_password": None,     # plaintext of inferred password that worked (for offer-to-save flow)
    }

    if not pdf_bytes:
        result["tipo"] = "corrompido"
        result["motivo_falha"] = "corrompido"
        return result

    # ── Step 1: try pdfplumber (no password) ──
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = _select_pages(pdf.pages)
            parts = []
            has_any_page = bool(pdf.pages)
            for page in pages:
                try:
                    t = page.extract_text() or ""
                except Exception:
                    t = ""
                if t.strip():
                    parts.append(t)
            text = "\n\n".join(parts)

        if text.strip():
            result["tipo"] = "digital"
            result["texto"] = text[:MAX_CHARS]
            result["leitura_sucesso"] = True
            result["campos"] = _extract_fields(result["texto"])
            return result

        # Opened OK but no text → try OCR (scanned)
        if has_any_page and reader is not None and getattr(reader, "_vision_model", None) \
                and getattr(reader, "_openrouter_key", None):
            ocr_text = await reader._extract_with_vision(pdf_bytes)
            if ocr_text and ocr_text.strip():
                result["tipo"] = "escaneado"
                result["texto"] = ocr_text[:MAX_CHARS]
                result["leitura_sucesso"] = True
                result["campos"] = _extract_fields(result["texto"])
                return result
            result["tipo"] = "escaneado"
            result["motivo_falha"] = "ocr_falhou"
            return result

        # No text and no OCR available
        result["tipo"] = "escaneado"
        result["motivo_falha"] = "ocr_falhou"
        return result

    except Exception as exc:
        if _is_password_error(exc) or _is_encrypted_pdf(pdf_bytes):
            # fall through to password-protected branch below
            pass
        else:
            logger.warning(f"PDF {filename}: parsing failed — {type(exc).__name__}: {exc}")
            result["tipo"] = "corrompido"
            result["motivo_falha"] = "corrompido"
            return result

    # ── Step 2: password-protected ──
    result["tipo"] = "protegido"

    # 2a. cadastradas
    for entry in (passwords_cadastradas or []):
        pwd = entry.get("password")
        if not pwd:
            continue
        text = _try_open_with_password(pdf_bytes, pwd)
        if text is not None and text.strip():
            result["texto"] = text[:MAX_CHARS]
            result["leitura_sucesso"] = True
            result["senha_usada_hash"] = hash_password(pwd)
            result["matched_password_id"] = entry.get("id")
            result["campos"] = _extract_fields(result["texto"])
            return result

    # 2b. inferidas do corpo
    for pwd in (inferred_candidates or []):
        text = _try_open_with_password(pdf_bytes, pwd)
        if text is not None and text.strip():
            result["texto"] = text[:MAX_CHARS]
            result["leitura_sucesso"] = True
            result["senha_usada_hash"] = hash_password(pwd)
            result["inferred_password"] = pwd  # caller decides whether to offer saving
            result["campos"] = _extract_fields(result["texto"])
            return result

    # 2c. desconhecida — explicit failure, DO NOT fabricate content
    if passwords_cadastradas:
        result["motivo_falha"] = "senha_incorreta"
    else:
        result["motivo_falha"] = "senha_ausente"
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Legacy class kept for backwards compatibility (used by email_processor
# for OCR fallback + existing tests).
# ──────────────────────────────────────────────────────────────────────────────


class PdfReader:
    """Extract text from PDF bytes (legacy API + OCR provider)."""

    def __init__(self, vision_model: str, openrouter_key: str):
        self._vision_model = vision_model
        self._openrouter_key = openrouter_key

    async def extract(self, pdf_bytes: bytes) -> str:
        """Legacy method — returns extracted text or empty string on failure.

        Kept for existing tests and any caller that just wants text back.
        """
        try:
            text = self._extract_with_pdfplumber(pdf_bytes)
            if text.strip():
                return text[:MAX_CHARS]
            if self._vision_model and self._openrouter_key:
                logger.info("pdfplumber returned no text, falling back to vision OCR")
                return await self._extract_with_vision(pdf_bytes)
            return ""
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return ""

    def _extract_with_pdfplumber(self, pdf_bytes: bytes) -> str:
        pages_text = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            selected = self._select_pages(pdf.pages)
            for page in selected:
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)
        return "\n\n".join(pages_text)

    async def _extract_with_vision(self, pdf_bytes: bytes) -> str:
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                selected = self._select_pages(pdf.pages)
                images_b64 = []
                for page in selected:
                    img = page.to_image(resolution=150).original
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    images_b64.append(base64.b64encode(buf.getvalue()).decode())

            if not images_b64:
                return ""

            content = [{"type": "text", "text": "Extract all text from these PDF pages. Return only the raw text content, no commentary."}]
            for img in images_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img}"}
                })

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._openrouter_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._vision_model,
                        "messages": [{"role": "user", "content": content}],
                        "max_tokens": 4000,
                    },
                )
                response.raise_for_status()
                import inspect
                raw = response.json()
                data = await raw if inspect.isawaitable(raw) else raw
                return data["choices"][0]["message"]["content"][:MAX_CHARS]
        except Exception as e:
            logger.error(f"Vision OCR failed: {e}")
            return ""

    def _select_pages(self, pages: List[Any]) -> List[Any]:
        return _select_pages(pages)
