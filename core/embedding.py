"""
core/embedding.py — Embedding Provider Abstraction v1.2.31 P1-2
支持 MiniLM / Voyage / OpenAI / Ollama
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Union
import json
import os

import numpy as np


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class EmbedConfig:
    """User configuration for embedding provider."""
    provider: str = "minilm"  # minilm | voyage | openai | ollama
    model: str = "all-MiniLM-L6-v2"
    api_key: str = ""
    base_url: str = ""
    dimension: int = 384
    timeout: float = 30.0

    @classmethod
    def from_dict(cls, d: dict) -> "EmbedConfig":
        return cls(
            provider=d.get("provider", "minilm"),
            model=d.get("model", "all-MiniLM-L6-v2"),
            api_key=d.get("api_key", ""),
            base_url=d.get("base_url", ""),
            dimension=int(d.get("dimension", 384)),
            timeout=float(d.get("timeout", 30.0)),
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "dimension": self.dimension,
            "timeout": self.timeout,
        }


# ---------------------------------------------------------------------------
# Base Provider
# ---------------------------------------------------------------------------

class EmbedProvider(ABC):
    """Abstract base for all embedding providers."""

    def __init__(self, config: EmbedConfig):
        self.config = config

    @abstractmethod
    def encode(self, texts: Union[str, list[str]]) -> np.ndarray:
        """
        Encode text(s) into embedding vector(s).
        Returns shape (dim,) for single string, (n, dim) for list.
        """

    def provider_name(self) -> str:
        return self.config.provider


# ---------------------------------------------------------------------------
# MiniLM (local, sentence-transformers)
# ---------------------------------------------------------------------------

class MiniLMProvider(EmbedProvider):
    """Local MiniLM via sentence-transformers."""

    _model = None

    def encode(self, texts: Union[str, list[str]]) -> np.ndarray:
        if MiniLMProvider._model is None:
            from sentence_transformers import SentenceTransformer
            MiniLMProvider._model = SentenceTransformer(
                self.config.model or "all-MiniLM-L6-v2",
                local_files_only=True,  # 避免网络超时，从本地缓存加载
            )
        return MiniLMProvider._model.encode(texts)


# ---------------------------------------------------------------------------
# Voyage AI
# ---------------------------------------------------------------------------

class VoyageProvider(EmbedProvider):
    """Voyage AI API."""

    DEFAULT_URL = "https://api.voyageai.com/v1/embeddings"

    def encode(self, texts: Union[str, list[str]]) -> np.ndarray:
        import httpx

        input_list = [texts] if isinstance(texts, str) else texts
        payload = {
            "input": input_list,
            "model": self.config.model or "voyage-2",
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        base = self.config.base_url or self.DEFAULT_URL
        r = httpx.post(base, json=payload, headers=headers, timeout=self.config.timeout)
        r.raise_for_status()
        data = r.json()["data"]
        vecs = [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
        result = np.array(vecs)
        return result[0] if isinstance(texts, str) else result


# ---------------------------------------------------------------------------
# OpenAI (ada-002 / text-embedding-3)
# ---------------------------------------------------------------------------

class OpenAIEmbedProvider(EmbedProvider):
    """OpenAI Embeddings API."""

    DEFAULT_URL = "https://api.openai.com/v1/embeddings"

    def encode(self, texts: Union[str, list[str]]) -> np.ndarray:
        import httpx

        input_list = [texts] if isinstance(texts, str) else texts
        payload = {
            "input": input_list,
            "model": self.config.model or "text-embedding-ada-002",
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        base = self.config.base_url or self.DEFAULT_URL
        r = httpx.post(f"{base}/embeddings", json=payload, headers=headers, timeout=self.config.timeout)
        r.raise_for_status()
        data = r.json()["data"]
        vecs = [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
        result = np.array(vecs)
        return result[0] if isinstance(texts, str) else result


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------

class OllamaEmbedProvider(EmbedProvider):
    """Ollama embeddings API (nomic-embed-text etc.)."""

    DEFAULT_URL = "http://localhost:11434"

    def encode(self, texts: Union[str, list[str]]) -> np.ndarray:
        import httpx, json

        input_list = [texts] if isinstance(texts, str) else texts
        payload = {
            "model": self.config.model or "nomic-embed-text",
            "input": input_list,
        }
        base = self.config.base_url or self.DEFAULT_URL
        r = httpx.post(f"{base}/api/embeddings", json=payload, timeout=self.config.timeout)
        r.raise_for_status()
        return np.array(r.json()["embedding"])


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "minilm": MiniLMProvider,
    "voyage": VoyageProvider,
    "openai": OpenAIEmbedProvider,
    "ollama": OllamaEmbedProvider,
}


def get_embed(config: EmbedConfig = None) -> EmbedProvider:
    """Factory: create EmbedProvider from config. Defaults to MiniLM."""
    if config is None:
        config = _load_embed_config()
    key = config.provider.lower()
    if key in ("ollama",):
        key = "ollama"
    provider_class = _PROVIDERS.get(key, MiniLMProvider)
    return provider_class(config)


def _load_embed_config() -> EmbedConfig:
    """Load embedding config from ~/.amber-hunter/config.json."""
    cfg_path = os.path.expanduser("~/.amber-hunter/config.json")
    if os.path.exists(cfg_path):
        try:
            data = json.loads(open(cfg_path).read())
            if "embedding" in data:
                return EmbedConfig.from_dict(data["embedding"])
        except Exception:
            pass
    return EmbedConfig()


def save_embed_config(config: EmbedConfig) -> None:
    """Save embedding config to ~/.amber-hunter/config.json."""
    import json
    cfg_path = os.path.expanduser("~/.amber-hunter/config.json")
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    data = {}
    if os.path.exists(cfg_path):
        try:
            data = json.loads(open(cfg_path).read())
        except Exception:
            pass
    data["embedding"] = config.to_dict()
    open(cfg_path, "w").write(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Module-level provider singleton (lazy, cached)
# ---------------------------------------------------------------------------

_embed_provider_singleton: EmbedProvider | None = None


def get_cached_embed() -> EmbedProvider:
    """Get the cached global embedding provider instance."""
    global _embed_provider_singleton
    if _embed_provider_singleton is None:
        _embed_provider_singleton = get_embed()
    return _embed_provider_singleton


def reset_embed_provider() -> None:
    """Reset the cached provider (useful after config change)."""
    global _embed_provider_singleton
    _embed_provider_singleton = None
