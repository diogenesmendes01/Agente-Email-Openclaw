"""Tests for crypto util, sender pattern matching, and rate limiter."""

import fnmatch
import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    monkeypatch.setenv("PDF_PASSWORD_KEY", Fernet.generate_key().decode())


def test_fernet_roundtrip():
    from orchestrator.utils.crypto import encrypt, decrypt
    token = encrypt("minhasenha123")
    # Token is not the plaintext
    assert token != "minhasenha123"
    assert decrypt(token) == "minhasenha123"


def test_decrypt_invalid_token_returns_none():
    from orchestrator.utils.crypto import decrypt
    assert decrypt("not-a-valid-token") is None


def test_hash_password_is_deterministic():
    from orchestrator.utils.crypto import hash_password
    h1 = hash_password("abc")
    h2 = hash_password("abc")
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert hash_password("abc") != hash_password("abd")


def test_is_configured_false_when_missing(monkeypatch):
    from orchestrator.utils import crypto
    monkeypatch.delenv("PDF_PASSWORD_KEY", raising=False)
    assert crypto.is_configured() is False


def test_sender_pattern_matches_wildcard():
    # fnmatch is what DatabaseService.get_pdf_passwords_for_sender uses
    assert fnmatch.fnmatch("cobranca@bradesco.com.br", "*@bradesco.com.br")
    assert fnmatch.fnmatch("foo@itau.com", "*@itau.com")
    assert not fnmatch.fnmatch("foo@bradesco.com.br", "*@itau.com")
    assert fnmatch.fnmatch("literal@email.com", "literal@email.com")
    assert not fnmatch.fnmatch("other@email.com", "literal@email.com")


def test_rate_limit_after_failed_attempts():
    from orchestrator.utils import pdf_ratelimit
    pdf_ratelimit.reset_all()
    assert pdf_ratelimit.is_locked(42, "*@bank.com") is False
    # 9 failures: not locked yet
    for _ in range(9):
        activated = pdf_ratelimit.record_failure(42, "*@bank.com")
        assert activated is False
    assert pdf_ratelimit.is_locked(42, "*@bank.com") is False
    # 10th triggers lockout
    activated = pdf_ratelimit.record_failure(42, "*@bank.com")
    assert activated is True
    assert pdf_ratelimit.is_locked(42, "*@bank.com") is True
    # Different account or pattern is unaffected
    assert pdf_ratelimit.is_locked(99, "*@bank.com") is False
    assert pdf_ratelimit.is_locked(42, "*@other.com") is False


def test_rate_limit_reset_on_success():
    from orchestrator.utils import pdf_ratelimit
    pdf_ratelimit.reset_all()
    for _ in range(5):
        pdf_ratelimit.record_failure(1, "x")
    pdf_ratelimit.record_success(1, "x")
    # After success, a fresh 10 failures are required to re-lock
    for i in range(9):
        assert pdf_ratelimit.record_failure(1, "x") is False
    assert pdf_ratelimit.record_failure(1, "x") is True


# ─────────────────────────────────────────────────────────────────────
# Rate-limit scoping: success/failure must touch only the pattern(s)
# actually tried against the PDF, never unrelated patterns cadastrados
# on the same account.
# ─────────────────────────────────────────────────────────────────────


def _make_plain_pdf(text: str = "Invoice content") -> bytes:
    import io
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        import pytest
        pytest.skip("reportlab not installed")
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 800, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_encrypted_pdf(password: str) -> bytes:
    import io
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        import pytest
        pytest.skip("reportlab not installed")
    from pypdf import PdfReader, PdfWriter
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 800, "Secret")
    c.showPage()
    c.save()
    buf.seek(0)
    reader = PdfReader(buf)
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    writer.encrypt(user_password=password, owner_password=password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.mark.asyncio
async def test_success_resets_only_matched_pattern():
    """2 patterns cadastrados, só o correto é resetado; o outro mantém counter intacto."""
    from unittest.mock import AsyncMock, MagicMock
    from orchestrator.handlers.email_processor import EmailProcessor
    from orchestrator.utils.pdf_reader import PdfReader
    from orchestrator.utils import pdf_ratelimit

    pdf_ratelimit.reset_all()
    # Seed failure counters on BOTH patterns
    for _ in range(3):
        pdf_ratelimit.record_failure(7, "*@bradesco.com.br")
        pdf_ratelimit.record_failure(7, "*@nubank.com.br")

    pdf_bytes = _make_encrypted_pdf("certa")

    db = AsyncMock()
    db.get_account.return_value = {"id": 7, "email": "u@x.com", "llm_model": None}
    # Only the bradesco pattern matches this sender; caller filters.
    db.get_pdf_passwords_for_sender.return_value = [
        {"id": 1, "sender_pattern": "*@bradesco.com.br",
         "password_encrypted": None, "locked_until": None},
    ]
    db.get_account_documents.return_value = None
    db.touch_pdf_password = AsyncMock()

    gmail = AsyncMock()
    gmail.get_attachment.return_value = pdf_bytes

    # Patch decrypt to return our plaintext
    import orchestrator.utils.crypto as crypto_mod
    original_decrypt = crypto_mod.decrypt
    crypto_mod.decrypt = lambda _t: "certa"
    try:
        reader = PdfReader(vision_model="", openrouter_key="")
        proc = EmailProcessor(
            db=db, qdrant=MagicMock(is_connected=lambda: False),
            llm=AsyncMock(), gmail=gmail, telegram=AsyncMock(),
            pdf_reader=reader,
        )
        email = {
            "id": "m", "body_clean": "b",
            "from_email": "cob@bradesco.com.br",
            "attachments": [{"filename": "p.pdf", "mimeType": "application/pdf", "attachmentId": "a"}],
        }
        await proc._process_pdf_attachments("m", email, "u@x.com")
    finally:
        crypto_mod.decrypt = original_decrypt

    # bradesco counter should have been reset
    assert pdf_ratelimit._failures.get((7, "*@bradesco.com.br"), []).__len__() == 0  # reset
    # nubank counter must be untouched (3 failures remain)
    assert len(pdf_ratelimit._failures.get((7, "*@nubank.com.br"), [])) == 3

    pdf_ratelimit.reset_all()


@pytest.mark.asyncio
async def test_failure_locks_only_attempted_patterns():
    """Sender bradesco; patterns cadastrados bradesco+nubank — só bradesco deve acumular failure."""
    from unittest.mock import AsyncMock, MagicMock
    from orchestrator.handlers.email_processor import EmailProcessor
    from orchestrator.utils.pdf_reader import PdfReader
    from orchestrator.utils import pdf_ratelimit

    pdf_ratelimit.reset_all()

    pdf_bytes = _make_encrypted_pdf("correta")

    db = AsyncMock()
    db.get_account.return_value = {"id": 42, "email": "u@x.com", "llm_model": None}
    # Caller (DatabaseService.get_pdf_passwords_for_sender) already filters by sender pattern —
    # so only bradesco is returned; nubank is NOT surfaced for a bradesco sender.
    db.get_pdf_passwords_for_sender.return_value = [
        {"id": 9, "sender_pattern": "*@bradesco.com.br",
         "password_encrypted": None, "locked_until": None},
    ]
    db.get_account_documents.return_value = None
    db.lock_pdf_pattern = AsyncMock()

    gmail = AsyncMock()
    gmail.get_attachment.return_value = pdf_bytes

    import orchestrator.utils.crypto as crypto_mod
    original_decrypt = crypto_mod.decrypt
    crypto_mod.decrypt = lambda _t: "errada"  # wrong password → failure on attempt
    try:
        reader = PdfReader(vision_model="", openrouter_key="")
        proc = EmailProcessor(
            db=db, qdrant=MagicMock(is_connected=lambda: False),
            llm=AsyncMock(), gmail=gmail, telegram=AsyncMock(),
            pdf_reader=reader,
        )
        email = {
            "id": "m", "body_clean": "b",
            "from_email": "x@bradesco.com.br",
            "attachments": [{"filename": "p.pdf", "mimeType": "application/pdf", "attachmentId": "a"}],
        }
        await proc._process_pdf_attachments("m", email, "u@x.com")
    finally:
        crypto_mod.decrypt = original_decrypt

    # bradesco: one failure recorded
    assert len(pdf_ratelimit._failures.get((42, "*@bradesco.com.br"), [])) == 1
    # nubank: untouched — it wasn't tried against this PDF
    assert len(pdf_ratelimit._failures.get((42, "*@nubank.com.br"), [])) == 0

    pdf_ratelimit.reset_all()


@pytest.mark.asyncio
async def test_motivo_sem_senha_cadastrada_quando_nenhum_pattern_matcha():
    from orchestrator.utils.pdf_reader import extract_pdf_attachment
    data = _make_encrypted_pdf("whatever")
    # Nenhuma senha cadastrada (ex: primeiro PDF de remetente novo)
    result = await extract_pdf_attachment(data, "boleto.pdf", passwords_cadastradas=[])
    assert result["leitura_sucesso"] is False
    assert result["tipo"] == "protegido"
    assert result["motivo_falha"] == "sem_senha_cadastrada"
    assert result["patterns_attempted"] == []


@pytest.mark.asyncio
async def test_motivo_senha_incorreta_quando_patterns_tentados_falharam():
    from orchestrator.utils.pdf_reader import extract_pdf_attachment
    data = _make_encrypted_pdf("certa")
    result = await extract_pdf_attachment(
        data, "boleto.pdf",
        passwords_cadastradas=[
            {"id": 1, "password": "errada1", "pattern": "*@bradesco.com.br"},
            {"id": 2, "password": "errada2", "pattern": "*@bradesco.com.br"},
        ],
    )
    assert result["leitura_sucesso"] is False
    assert result["motivo_falha"] == "senha_incorreta"
    assert result["patterns_attempted"] == ["*@bradesco.com.br"]
    assert result["pattern_used"] is None
