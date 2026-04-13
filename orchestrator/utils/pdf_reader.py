"""PDF text extraction with pdfplumber + Gemini vision fallback."""

import io
import base64
import logging
from typing import List, Any

import pdfplumber
import httpx

logger = logging.getLogger(__name__)

MAX_PAGES_FULL = 10
FIRST_PAGES = 5
LAST_PAGES = 2
MAX_CHARS = 15000


class PdfReader:
    """Extract text from PDF bytes.

    Strategy:
    1. Try pdfplumber for text extraction (free, fast)
    2. If no text found and vision model configured, convert to images
       and send to Gemini 2.5 Flash via OpenRouter for OCR
    """

    def __init__(self, vision_model: str, openrouter_key: str):
        self._vision_model = vision_model
        self._openrouter_key = openrouter_key

    async def extract(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes. Returns empty string on failure."""
        try:
            text = self._extract_with_pdfplumber(pdf_bytes)
            if text.strip():
                return text[:MAX_CHARS]

            # Fallback to vision if configured
            if self._vision_model and self._openrouter_key:
                logger.info("pdfplumber returned no text, falling back to vision OCR")
                return await self._extract_with_vision(pdf_bytes)

            return ""
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return ""

    def _extract_with_pdfplumber(self, pdf_bytes: bytes) -> str:
        """Extract text using pdfplumber."""
        pages_text = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            selected = self._select_pages(pdf.pages)
            for page in selected:
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)
        return "\n\n".join(pages_text)

    async def _extract_with_vision(self, pdf_bytes: bytes) -> str:
        """Convert PDF pages to images via pdfplumber/Pillow and send to vision LLM for OCR."""
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                selected = self._select_pages(pdf.pages)
                images_b64 = []
                for page in selected:
                    img = page.to_image(resolution=150).original  # PIL Image
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    images_b64.append(base64.b64encode(buf.getvalue()).decode())

            if not images_b64:
                return ""

            # Call OpenRouter with vision
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
                # httpx Response.json() is sync; support both sync and async (for testing)
                import inspect
                raw = response.json()
                data = await raw if inspect.isawaitable(raw) else raw
                return data["choices"][0]["message"]["content"][:MAX_CHARS]

        except Exception as e:
            logger.error(f"Vision OCR failed: {e}")
            return ""

    def _select_pages(self, pages: List[Any]) -> List[Any]:
        """Select pages to process: all if <=10, else first 5 + last 2."""
        if len(pages) <= MAX_PAGES_FULL:
            return pages
        return pages[:FIRST_PAGES] + pages[-LAST_PAGES:]
