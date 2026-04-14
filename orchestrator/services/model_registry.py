"""
Model Registry — fetches and caches available models from OpenRouter.

Provides:
- Live pricing from the API (cached 24h)
- Curated "top 15" list of best models for email/text tasks
- Browsable full list sorted by price
"""

import time
import logging
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Curated list: best models for text/email classification, summarization, drafting.
# Order = display order in Telegram. IDs must match OpenRouter model IDs.
# Updated: 2026-04-14
CURATED_MODEL_IDS = [
    # --- Free ---
    "openrouter/free",                              # Auto-pick best free model
    "google/gemma-4-27b-a9b-it:free",               # Google Gemma 4 27B
    "nvidia/nemotron-3-super-120b-a12b:free",        # NVIDIA Nemotron 120B
    "qwen/qwen3-next-80b-a3b-instruct:free",        # Qwen3 80B
    # --- Budget ---
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini",
    "deepseek/deepseek-chat-v3-0324",
    "mistralai/mistral-small-3.1-24b-instruct",
    "openai/gpt-4.1-mini",
    # --- Mid-range ---
    "z-ai/glm-5-turbo",
    "anthropic/claude-3.5-haiku",
    "openai/o4-mini",
    # --- Premium ---
    "google/gemini-2.5-pro-preview",
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-3.7-sonnet",
]


def _price_per_million(price_per_token_str: str) -> float:
    """Convert OpenRouter per-token string price to per-million-tokens float."""
    try:
        return float(price_per_token_str) * 1_000_000
    except (ValueError, TypeError):
        return 0.0


class ModelInfo:
    """Parsed model information."""

    __slots__ = ("id", "name", "context_length", "prompt_price", "completion_price")

    def __init__(self, raw: dict):
        self.id: str = raw.get("id", "")
        self.name: str = raw.get("name", self.id)
        self.context_length: int = raw.get("context_length", 0)
        pricing = raw.get("pricing") or {}
        self.prompt_price = _price_per_million(pricing.get("prompt", "0"))
        self.completion_price = _price_per_million(pricing.get("completion", "0"))

    @property
    def is_free(self) -> bool:
        return self.prompt_price == 0 and self.completion_price == 0

    @property
    def avg_price(self) -> float:
        """Average of prompt + completion price (for sorting)."""
        return (self.prompt_price + self.completion_price) / 2

    def price_label(self) -> str:
        if self.is_free:
            return "GRATIS"
        return f"${self.prompt_price:.2f} / ${self.completion_price:.2f} por 1M"

    def short_label(self) -> str:
        """Short label for Telegram button."""
        if self.is_free:
            return f"{self.name} [GRATIS]"
        return f"{self.name} [${self.prompt_price:.2f}/{self.completion_price:.2f}]"


class ModelRegistry:
    """Fetches and caches OpenRouter models."""

    CACHE_TTL = 86400  # 24 hours

    def __init__(self):
        self._cache: Dict[str, ModelInfo] = {}
        self._cache_time: float = 0
        self._all_text_models: List[ModelInfo] = []

    @property
    def is_loaded(self) -> bool:
        return bool(self._cache)

    async def refresh(self) -> bool:
        """Fetch models from OpenRouter API. Returns True on success."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(OPENROUTER_MODELS_URL)
                if resp.status_code != 200:
                    logger.error(f"OpenRouter models API returned {resp.status_code}")
                    return False

                data = resp.json()
                models_raw = data.get("data", [])

                new_cache: Dict[str, ModelInfo] = {}
                text_models: List[ModelInfo] = []

                for raw in models_raw:
                    info = ModelInfo(raw)
                    if not info.id:
                        continue
                    new_cache[info.id] = info

                    # Filter: only text-capable models (skip image-only, audio-only)
                    arch = raw.get("architecture", {})
                    modality = arch.get("modality", "text->text")
                    if "text" in modality:
                        text_models.append(info)

                # Sort text models by price (free first, then cheapest)
                text_models.sort(key=lambda m: (not m.is_free, m.avg_price))

                self._cache = new_cache
                self._all_text_models = text_models
                self._cache_time = time.time()

                logger.info(f"ModelRegistry: cached {len(new_cache)} models ({len(text_models)} text)")
                return True

        except Exception as e:
            logger.error(f"ModelRegistry refresh error: {e}")
            return False

    async def _ensure_cache(self):
        """Refresh cache if expired or empty."""
        if not self._cache or (time.time() - self._cache_time) > self.CACHE_TTL:
            await self.refresh()

    async def get_model(self, model_id: str) -> Optional[ModelInfo]:
        """Get info for a specific model."""
        await self._ensure_cache()
        return self._cache.get(model_id)

    async def get_pricing(self, model_id: str) -> dict:
        """Get pricing for a model. Returns {prompt: float, completion: float} per 1M tokens."""
        info = await self.get_model(model_id)
        if info:
            return {"prompt": info.prompt_price, "completion": info.completion_price}
        # Fallback for unknown models
        return {"prompt": 0.10, "completion": 0.40}

    async def get_curated_models(self) -> List[ModelInfo]:
        """Get the curated top models for email/text, with live pricing."""
        await self._ensure_cache()
        result = []
        for model_id in CURATED_MODEL_IDS:
            info = self._cache.get(model_id)
            if info:
                result.append(info)
            else:
                logger.debug(f"Curated model {model_id} not found in OpenRouter")
        return result

    async def list_models(self, limit: int = 20) -> List[ModelInfo]:
        """List top N text models sorted by price (cheapest first)."""
        await self._ensure_cache()
        return self._all_text_models[:limit]

    async def search_models(self, query: str, limit: int = 10) -> List[ModelInfo]:
        """Search models by name or ID."""
        await self._ensure_cache()
        query_lower = query.lower()
        results = [
            m for m in self._all_text_models
            if query_lower in m.id.lower() or query_lower in m.name.lower()
        ]
        return results[:limit]
