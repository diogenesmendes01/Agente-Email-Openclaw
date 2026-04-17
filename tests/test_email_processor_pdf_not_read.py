"""Test that when a PDF cannot be read, the email body sent downstream contains
an explicit 'ANEXO PDF NÃO LIDO' marker and NOT the filename as if it were
content. Core rule: if we didn't read it, we don't pretend we did.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    monkeypatch.setenv("PDF_PASSWORD_KEY", Fernet.generate_key().decode())


def _make_encrypted_pdf(password: str) -> bytes:
    import io
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab not installed")
    from pypdf import PdfReader, PdfWriter
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 800, "Confidential")
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
async def test_unread_pdf_injects_explicit_marker_not_filename_content():
    from orchestrator.handlers.email_processor import EmailProcessor
    from orchestrator.utils.pdf_reader import PdfReader

    pdf_bytes = _make_encrypted_pdf("whatever")

    # Mock collaborators
    db = AsyncMock()
    db.get_account.return_value = {"id": 7, "email": "u@x.com", "llm_model": None, "owner_name": ""}
    db.get_pdf_passwords_for_sender.return_value = []
    db.get_account_documents.return_value = None

    gmail = AsyncMock()
    gmail.get_attachment.return_value = pdf_bytes

    reader = PdfReader(vision_model="", openrouter_key="")

    proc = EmailProcessor(
        db=db, qdrant=MagicMock(is_connected=lambda: False),
        llm=AsyncMock(), gmail=gmail, telegram=AsyncMock(),
        pdf_reader=reader,
    )

    email = {
        "id": "mid",
        "body_clean": "Body da msg",
        "from_email": "cob@bank.com",
        "attachments": [
            {"filename": "boleto_secreto.pdf", "mimeType": "application/pdf", "attachmentId": "att1"}
        ],
    }

    await proc._process_pdf_attachments("mid", email, "u@x.com")

    body = email["body_clean"]
    # MUST contain explicit non-read marker
    assert "ANEXO PDF NÃO LIDO" in body
    assert "boleto_secreto.pdf" in body
    assert "protegido por senha" in body.lower()
    # MUST NOT inject fake PDF content (no "--- ANEXO PDF: ..." without NÃO LIDO)
    # i.e., the success-branch header must not appear
    success_header = "--- ANEXO PDF: boleto_secreto.pdf"
    assert success_header not in body
    # Per-attachment metadata preserved
    assert len(email["pdf_attachments"]) == 1
    pa = email["pdf_attachments"][0]
    assert pa["leitura_sucesso"] is False
    assert pa["tipo"] == "protegido"
    assert pa["motivo_falha"] == "senha_ausente"


@pytest.mark.asyncio
async def test_successful_pdf_is_included_in_body():
    from orchestrator.handlers.email_processor import EmailProcessor
    from orchestrator.utils.pdf_reader import PdfReader
    import io
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab not installed")

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 800, "PROPOSTA: Valor R$ 9.999,00")
    c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()

    db = AsyncMock()
    db.get_account.return_value = {"id": 1, "email": "u@x.com", "llm_model": None}
    db.get_pdf_passwords_for_sender.return_value = []
    db.get_account_documents.return_value = None
    gmail = AsyncMock()
    gmail.get_attachment.return_value = pdf_bytes

    reader = PdfReader(vision_model="", openrouter_key="")
    proc = EmailProcessor(
        db=db, qdrant=MagicMock(is_connected=lambda: False),
        llm=AsyncMock(), gmail=gmail, telegram=AsyncMock(),
        pdf_reader=reader,
    )

    email = {
        "id": "m", "body_clean": "b",
        "from_email": "x@y.com",
        "attachments": [{"filename": "p.pdf", "mimeType": "application/pdf", "attachmentId": "a"}],
    }
    await proc._process_pdf_attachments("m", email, "u@x.com")

    assert "ANEXO PDF NÃO LIDO" not in email["body_clean"]
    assert "PROPOSTA" in email["body_clean"]
    assert email["pdf_attachments"][0]["leitura_sucesso"] is True
    assert email["pdf_attachments"][0]["tipo"] == "digital"
