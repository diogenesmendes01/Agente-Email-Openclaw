import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestPdfReader:

    @pytest.mark.asyncio
    async def test_extract_text_from_text_pdf(self):
        """pdfplumber can extract text — should return it directly."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Invoice #123\nTotal: $500"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            from orchestrator.utils.pdf_reader import PdfReader
            reader = PdfReader(vision_model="test-model", openrouter_key="key")
            result = await reader.extract(b"fake-pdf-bytes")
            assert "Invoice #123" in result
            assert "Total: $500" in result

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_text(self):
        """pdfplumber returns empty — no vision client configured."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            from orchestrator.utils.pdf_reader import PdfReader
            reader = PdfReader(vision_model="", openrouter_key="")
            result = await reader.extract(b"fake-pdf-bytes")
            assert result == ""

    @pytest.mark.asyncio
    async def test_vision_fallback_calls_openrouter(self):
        """When pdfplumber returns empty and vision is configured, call OpenRouter."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_img = MagicMock()
        mock_img.original = MagicMock()  # PIL Image mock
        mock_page.to_image.return_value = mock_img
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_response = AsyncMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OCR extracted text"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("pdfplumber.open", return_value=mock_pdf), \
             patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            # Patch PIL Image.save to write fake PNG bytes
            with patch.object(mock_img.original, "save", side_effect=lambda buf, **kw: buf.write(b"fakepng")):
                from orchestrator.utils.pdf_reader import PdfReader
                reader = PdfReader(vision_model="google/gemini-2.5-flash", openrouter_key="sk-test")
                result = await reader.extract(b"fake-pdf-bytes")
                assert result == "OCR extracted text"
                mock_client.post.assert_called_once()

    def test_page_limit_large_pdf(self):
        """PDFs > 10 pages should select first 5 + last 2."""
        from orchestrator.utils.pdf_reader import PdfReader
        reader = PdfReader(vision_model="m", openrouter_key="k")
        pages = list(range(20))  # 20 pages
        selected = reader._select_pages(pages)
        assert selected == [0, 1, 2, 3, 4, 18, 19]

    def test_page_limit_small_pdf(self):
        """PDFs <= 10 pages should use all."""
        from orchestrator.utils.pdf_reader import PdfReader
        reader = PdfReader(vision_model="m", openrouter_key="k")
        pages = list(range(5))
        selected = reader._select_pages(pages)
        assert selected == [0, 1, 2, 3, 4]
