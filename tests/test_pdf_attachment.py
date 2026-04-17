"""Tests for the robust extract_pdf_attachment() API."""

import io
import os
import pytest
from unittest.mock import patch

from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    monkeypatch.setenv("PDF_PASSWORD_KEY", Fernet.generate_key().decode())


def _make_plain_pdf(text: str = "Invoice #42 R$ 1.234,56 venc. 05/02/2026\nCPF 123.456.789-00") -> bytes:
    """Build a minimal PDF with plaintext using pypdf (not encrypted)."""
    import pypdf
    from pypdf import PdfWriter
    # pypdf can't add text out-of-the-box without reportlab; use reportlab if available,
    # else fallback to a dedicated helper. We'll try reportlab first.
    try:
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        for i, line in enumerate(text.split("\n")):
            c.drawString(72, 800 - i * 16, line)
        c.showPage()
        c.save()
        return buf.getvalue()
    except ImportError:
        pytest.skip("reportlab not installed — skipping text-PDF generation test")


def _make_encrypted_pdf(password: str, text: str = "Secret contents R$ 826,92") -> bytes:
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab not installed")
    from pypdf import PdfReader, PdfWriter
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(text.split("\n")):
        c.drawString(72, 800 - i * 16, line)
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
async def test_detects_digital_pdf():
    from orchestrator.utils.pdf_reader import extract_pdf_attachment
    data = _make_plain_pdf()
    result = await extract_pdf_attachment(data, "invoice.pdf")
    assert result["leitura_sucesso"] is True
    assert result["tipo"] == "digital"
    assert result["texto"] and "Invoice" in result["texto"]
    assert result["motivo_falha"] is None


@pytest.mark.asyncio
async def test_detects_protected_pdf_without_password():
    from orchestrator.utils.pdf_reader import extract_pdf_attachment
    data = _make_encrypted_pdf("segredo123")
    result = await extract_pdf_attachment(data, "boleto.pdf")
    assert result["leitura_sucesso"] is False
    assert result["tipo"] == "protegido"
    assert result["motivo_falha"] == "senha_ausente"
    assert result["texto"] is None


@pytest.mark.asyncio
async def test_detects_protected_pdf_with_correct_password():
    from orchestrator.utils.pdf_reader import extract_pdf_attachment
    data = _make_encrypted_pdf("segredo123")
    result = await extract_pdf_attachment(
        data, "boleto.pdf",
        passwords_cadastradas=[{"id": 1, "password": "segredo123"}],
    )
    assert result["leitura_sucesso"] is True
    assert result["texto"] and "Secret" in result["texto"]
    assert result["senha_usada_hash"] and result["senha_usada_hash"].startswith("sha256:")
    assert result["matched_password_id"] == 1


@pytest.mark.asyncio
async def test_detects_protected_pdf_with_wrong_password_returns_senha_incorreta():
    from orchestrator.utils.pdf_reader import extract_pdf_attachment
    data = _make_encrypted_pdf("segredo123")
    result = await extract_pdf_attachment(
        data, "boleto.pdf",
        passwords_cadastradas=[{"id": 1, "password": "errada"}],
    )
    assert result["leitura_sucesso"] is False
    assert result["tipo"] == "protegido"
    assert result["motivo_falha"] == "senha_incorreta"


@pytest.mark.asyncio
async def test_detects_corrupted_pdf():
    from orchestrator.utils.pdf_reader import extract_pdf_attachment
    result = await extract_pdf_attachment(b"not a pdf at all", "x.pdf")
    assert result["leitura_sucesso"] is False
    assert result["tipo"] == "corrompido"
    assert result["motivo_falha"] == "corrompido"


@pytest.mark.asyncio
async def test_extracts_valores_datas_cpf_masked():
    from orchestrator.utils.pdf_reader import extract_pdf_attachment
    data = _make_plain_pdf(
        "Cobranca CPF 123.456.789-00 CNPJ 12.345.678/0001-99\n"
        "Valor: R$ 1.234,56 Vencimento 05/02/2026 Protocolo: ABC123"
    )
    result = await extract_pdf_attachment(data, "cobranca.pdf")
    assert result["leitura_sucesso"] is True
    campos = result["campos"]
    assert any("R$" in v for v in campos["valores_brl"])
    assert "05/02/2026" in campos["datas"]
    # CPF MUST be masked
    assert campos["cpfs"] == ["***.***.***-00"]
    assert "123.456.789-00" not in "".join(campos["cpfs"])
    # CNPJ MUST be masked
    assert campos["cnpjs"] == ["**.***.***/****-99"]
    assert "ABC123" in campos["protocolos"]


@pytest.mark.asyncio
async def test_inferred_password_from_cpf_hint():
    """Body mentions CPF, account has CPF → should try that as password."""
    from orchestrator.utils.pdf_reader import extract_pdf_attachment, _inferred_passwords_from_body
    data = _make_encrypted_pdf("12345678900")
    candidates = _inferred_passwords_from_body(
        "Sua senha e o CPF, so os numeros.",
        {"cpf": "123.456.789-00", "cnpj": None, "birthdate": None},
    )
    assert "12345678900" in candidates
    result = await extract_pdf_attachment(
        data, "doc.pdf", inferred_candidates=candidates,
    )
    assert result["leitura_sucesso"] is True
    assert result["inferred_password"] == "12345678900"
