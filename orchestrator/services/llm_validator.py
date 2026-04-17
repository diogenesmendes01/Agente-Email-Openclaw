"""
LLM output validator (PR 4).

Pipeline:
  1. Parse JSON (tolerant to preamble/suffix noise)
  2. Validate Pydantic schema (clamp/normalize safe fields)
  3. Run semantic checks (generic summary, draft missing/invented values, ...)
  4. If blocking flags or parse/schema errors -> retry once with reinforced prompt
  5. If still bad: fallback (e.g. strip invented draft) + log
  6. Always emit a ValidationMetadata record

This layer operates on the response dict, not on prompt structure, so it is
independent of future prompt refactors (PR 3). It only needs the raw prompt
string to build a reinforcement addendum for the retry.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

_VALID_PRIORITIES = {"Alta", "Media", "Média", "Baixa"}
_PRIORITY_NORMALIZE = {"Média": "Media"}
_VALID_CATEGORIES = {
    "cliente", "financeiro", "pessoal", "trabalho",
    "promocao", "newsletter", "outro",
}
_VALID_ACOES = {"notificar", "arquivar", "criar_task", "rascunho"}

_MAX_STR_LEN = 4000


def _truncate(s: Any) -> Any:
    if isinstance(s, str) and len(s) > _MAX_STR_LEN:
        return s[:_MAX_STR_LEN] + "..."
    return s


class ClassificationOut(BaseModel):
    """Classifier response schema — tolerant to extra fields."""
    model_config = ConfigDict(extra="allow")

    importante: bool = True
    prioridade: str = "Media"
    categoria: str = "outro"
    confianca: float = 0.5
    razao: str = ""
    entidades: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("prioridade", mode="before")
    @classmethod
    def _normalize_priority(cls, v):
        if v is None:
            return "Media"
        v = str(v).strip()
        v = _PRIORITY_NORMALIZE.get(v, v)
        if v not in _VALID_PRIORITIES:
            return "Media"
        return _PRIORITY_NORMALIZE.get(v, v)

    @field_validator("categoria", mode="before")
    @classmethod
    def _normalize_category(cls, v):
        if v is None:
            return "outro"
        v = str(v).strip().lower()
        return v if v in _VALID_CATEGORIES else "outro"

    @field_validator("confianca", mode="before")
    @classmethod
    def _clamp_conf(cls, v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.5
        if f < 0.0:
            return 0.0
        if f > 1.0:
            # Some models return 0..100
            if f <= 100.0:
                return f / 100.0
            return 1.0
        return f

    @field_validator("razao", mode="before")
    @classmethod
    def _truncate_razao(cls, v):
        return _truncate(v) if v is not None else ""

    @field_validator("entidades", mode="before")
    @classmethod
    def _ent_dict(cls, v):
        return v if isinstance(v, dict) else {}


class SummaryOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    resumo: str = ""
    entidades: Dict[str, Any] = Field(default_factory=dict)
    sentimento: str = "neutro"

    @field_validator("resumo", mode="before")
    @classmethod
    def _r(cls, v):
        return _truncate(v) if v is not None else ""

    @field_validator("entidades", mode="before")
    @classmethod
    def _e(cls, v):
        return v if isinstance(v, dict) else {}

    @field_validator("sentimento", mode="before")
    @classmethod
    def _s(cls, v):
        if not isinstance(v, str) or not v.strip():
            return "neutro"
        return v.strip()[:40]


class ActionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    acao: str = "notificar"
    justificativa: str = ""
    task: Optional[Dict[str, Any]] = None
    rascunho_resposta: Optional[str] = None
    acao_usuario: Optional[str] = None

    @field_validator("acao", mode="before")
    @classmethod
    def _acao(cls, v):
        if v is None:
            return "notificar"
        v = str(v).strip().lower()
        return v if v in _VALID_ACOES else "notificar"

    @field_validator("justificativa", "rascunho_resposta", "acao_usuario", mode="before")
    @classmethod
    def _trunc(cls, v):
        return _truncate(v) if v is not None else v

    @field_validator("task", mode="before")
    @classmethod
    def _task(cls, v):
        if v is None:
            return None
        return v if isinstance(v, dict) else None


_SCHEMA_BY_KIND = {
    "classification": ClassificationOut,
    "summary": SummaryOut,
    "action": ActionOut,
}


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

def _extract_json(response: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON extraction. Returns None if impossible."""
    if not response or not isinstance(response, str):
        return None
    # Try direct
    try:
        parsed = json.loads(response)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: first { ... last }
    start = response.find("{")
    end = response.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(response[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Semantic validation
# ---------------------------------------------------------------------------

# Capture monetary values like "R$ 826,92" or "R$1.234,56" or "R$ 500"
_MONEY_RE = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?|R\$\s?\d+(?:,\d{2})?", re.IGNORECASE)
# Dates DD/MM/YYYY (also DD/MM/YY)
_DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{2,4})\b")
# Chavões (generic filler phrases)
_CHAVAO_RE = re.compile(
    r"\b(email\s+importante|requer\s+(sua\s+)?aten[çc][aã]o|"
    r"por\s+favor\s+verifique|aguardando\s+retorno)\b",
    re.IGNORECASE,
)
# Proper noun heuristic: capitalized word >=3 chars not at start of sentence
_PROPER_RE = re.compile(r"(?<!^)(?<![\.\?\!]\s)\b[A-Z][a-zá-ú]{2,}\b")


def _norm_money(s: str) -> str:
    """Normalize money string for comparison (strip spaces, lower)."""
    return re.sub(r"\s+", "", s).lower()


def _norm_date(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _email_text(email: Dict[str, Any]) -> str:
    parts = [
        email.get("subject", "") or "",
        email.get("body_clean", "") or email.get("body", "") or "",
    ]
    return "\n".join(parts)


def semantic_validate(
    email: Dict[str, Any],
    classification: Optional[Dict[str, Any]] = None,
    summary: Optional[Dict[str, Any]] = None,
    action: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return list of semantic-flag strings triggered by the outputs.

    All checks are null-safe: pass None for any output you're not validating.
    """
    flags: List[str] = []
    email_text = _email_text(email)
    email_moneys = {_norm_money(m) for m in _MONEY_RE.findall(email_text)}
    email_dates = {_norm_date(d) for d in _DATE_RE.findall(email_text)}

    categoria = ""
    if classification:
        categoria = str(classification.get("categoria", "")).lower()

    # ----- summary checks -----
    if summary:
        resumo = summary.get("resumo", "") or ""
        if resumo and _CHAVAO_RE.search(resumo):
            has_number = bool(re.search(r"\d", resumo))
            has_proper = bool(_PROPER_RE.search(resumo))
            if not (has_number or has_proper):
                flags.append("resumo_generico")

    # ----- action/rascunho checks -----
    if action:
        rascunho = action.get("rascunho_resposta") or ""
        if rascunho:
            # short-draft (skip for newsletter/promocao)
            if len(rascunho.strip()) < 50 and categoria not in {"newsletter", "promocao"}:
                flags.append("rascunho_muito_curto")

            # missing-value / missing-date (only if email HAD them)
            if email_moneys:
                draft_moneys = {_norm_money(m) for m in _MONEY_RE.findall(rascunho)}
                if not draft_moneys:
                    flags.append("rascunho_sem_valor")
            if email_dates:
                draft_dates = {_norm_date(d) for d in _DATE_RE.findall(rascunho)}
                if not draft_dates:
                    flags.append("rascunho_sem_data")

            # invented-value: any money/date in draft MUST also appear in email
            draft_moneys = {_norm_money(m) for m in _MONEY_RE.findall(rascunho)}
            if draft_moneys and not draft_moneys.issubset(email_moneys):
                flags.append("rascunho_inventado")
            else:
                draft_dates = {_norm_date(d) for d in _DATE_RE.findall(rascunho)}
                if draft_dates and not draft_dates.issubset(email_dates):
                    flags.append("rascunho_inventado")

    return flags


# Flags that force a retry when present (per-kind).
_BLOCKING_FLAGS: Dict[str, set] = {
    "classification": set(),  # schema handles most classification issues
    "summary": {"resumo_generico"},
    "action": {
        "rascunho_sem_valor",
        "rascunho_sem_data",
        "rascunho_muito_curto",
        "rascunho_inventado",
    },
}


def _blocking(kind: str, flags: List[str]) -> List[str]:
    blk = _BLOCKING_FLAGS.get(kind, set())
    return [f for f in flags if f in blk]


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

@dataclass
class ValidationMetadata:
    kind: str
    retries: int = 0
    flags: List[str] = field(default_factory=list)
    json_parse_failed: bool = False
    schema_valid: bool = True
    fallback_used: bool = False
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

# call_llm_fn signature: async (prompt: str) -> Optional[dict]
#   returns the raw provider response dict (with "content", token counts, ...)
LlmCall = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]


def _reinforcement_addendum(kind: str, flags: List[str], email: Dict[str, Any]) -> str:
    """Build a short addendum to append to the prompt on retry."""
    bits = ["\n\nATENCAO - A RESPOSTA ANTERIOR NAO PASSOU NA VALIDACAO:"]
    if "json_parse_failed" in flags:
        bits.append("- JSON invalido. Responda APENAS com JSON valido, sem texto antes ou depois.")
    if "schema_invalid" in flags:
        bits.append("- Schema invalido. Use EXATAMENTE os campos e valores permitidos descritos acima.")
    if "resumo_generico" in flags:
        bits.append("- O resumo estava generico (chavoes). Cite valores, datas ou nomes concretos do email.")
    if "rascunho_muito_curto" in flags:
        bits.append("- O rascunho estava curto demais. Escreva ao menos 2-3 frases com conteudo concreto.")
    etext = _email_text(email)
    moneys = list(dict.fromkeys(_MONEY_RE.findall(etext)))[:3]
    dates = list(dict.fromkeys(_DATE_RE.findall(etext)))[:3]
    if "rascunho_sem_valor" in flags and moneys:
        bits.append(f"- O email menciona valor(es): {', '.join(moneys)}. Cite-os no rascunho.")
    if "rascunho_sem_data" in flags and dates:
        bits.append(f"- O email menciona data(s): {', '.join(dates)}. Cite-as no rascunho.")
    if "rascunho_inventado" in flags:
        bits.append(
            "- O rascunho continha valores/datas que NAO estao no email. "
            "NUNCA invente numeros. Use apenas o que aparece no email original."
        )
    bits.append("Refaca a resposta em JSON valido corrigindo esses pontos.")
    return "\n".join(bits)


async def validate_and_retry(
    kind: Literal["classification", "summary", "action"],
    prompt: str,
    call_llm_fn: LlmCall,
    email: Dict[str, Any],
    classification: Optional[Dict[str, Any]] = None,
    summary: Optional[Dict[str, Any]] = None,
    max_retries: int = 1,
    model: Optional[str] = None,
) -> Tuple[Dict[str, Any], ValidationMetadata]:
    """
    Run the LLM with validation and (optionally) one reinforced retry.

    `classification` / `summary` are the previous pipeline stages' outputs —
    only needed when kind == "action" or "summary" and they inform semantic checks.

    Returns (dict_result, ValidationMetadata). The dict always has the schema's
    fields (filled with safe defaults if necessary) plus any extras.
    """
    meta = ValidationMetadata(kind=kind, model=model)
    schema_cls = _SCHEMA_BY_KIND[kind]

    current_prompt = prompt
    last_raw: Optional[Dict[str, Any]] = None
    last_parsed: Optional[Dict[str, Any]] = None
    last_validated: Optional[BaseModel] = None
    last_flags: List[str] = []

    attempts = max_retries + 1  # e.g. max_retries=1 => up to 2 calls

    for attempt in range(attempts):
        raw = await call_llm_fn(current_prompt)
        if raw is None:
            # hard LLM failure — can't retry on same failure mode productively
            meta.retries = attempt
            break
        last_raw = raw
        meta.model = raw.get("model_used") or meta.model
        meta.prompt_tokens = raw.get("prompt_tokens", meta.prompt_tokens) or 0
        meta.completion_tokens = raw.get("completion_tokens", meta.completion_tokens) or 0

        parsed = _extract_json(raw.get("content", "") or "")
        retry_signals: List[str] = []

        if parsed is None:
            meta.json_parse_failed = True
            retry_signals.append("json_parse_failed")
        else:
            last_parsed = parsed
            try:
                validated = schema_cls.model_validate(parsed)
                last_validated = validated
                meta.schema_valid = True
            except ValidationError as ve:
                meta.schema_valid = False
                retry_signals.append("schema_invalid")
                logger.warning(f"[validator] schema invalid for {kind}: {ve}")

            if last_validated is not None:
                # run semantic checks now; kind tells us which parts matter
                result_dict = last_validated.model_dump()
                sem_flags = semantic_validate(
                    email,
                    classification=result_dict if kind == "classification" else classification,
                    summary=result_dict if kind == "summary" else summary,
                    action=result_dict if kind == "action" else None,
                )
                last_flags = sem_flags
                retry_signals.extend(_blocking(kind, sem_flags))

        # Decide whether to retry
        if attempt < max_retries and retry_signals:
            meta.retries = attempt + 1
            current_prompt = prompt + _reinforcement_addendum(kind, retry_signals, email)
            continue

        meta.retries = attempt
        break

    # Build final result dict
    if last_validated is not None:
        result: Dict[str, Any] = last_validated.model_dump()
    elif last_parsed is not None:
        # schema invalid but we have a parsed dict — fall back to defaults + partial
        try:
            forced = schema_cls(**{})
            result = forced.model_dump()
        except Exception:
            result = {}
        # keep the partial keys we can trust (extras allowed)
        for k, v in last_parsed.items():
            result.setdefault(k, v)
        meta.fallback_used = True
    else:
        # nothing parseable — full default
        try:
            result = schema_cls().model_dump()
        except Exception:
            result = {}
        meta.fallback_used = True

    # Copy token / cost fields from raw response for pipeline accounting
    if last_raw:
        for tok_key in ("prompt_tokens", "completion_tokens", "total_tokens",
                        "reasoning_tokens", "cost_usd", "model_used"):
            if tok_key in last_raw and tok_key not in result:
                result[tok_key] = last_raw[tok_key]

    # Fallback: persistent rascunho_inventado => strip the draft
    meta.flags = list(last_flags)
    if kind == "action" and "rascunho_inventado" in last_flags:
        result["rascunho_resposta"] = (
            "[Rascunho removido pela validacao - conteudo possivelmente inventado]"
        )
        meta.fallback_used = True

    return result, meta
