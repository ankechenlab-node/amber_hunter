"""
core/llm.py — LLM Provider Abstraction Layer

Unified interface for MiniMax / OpenAI / Claude / Local models.
Users configure which provider to use; all calls go through this layer.

Usage:
    from core.llm import get_llm, LLMProvider

    llm = get_llm()
    response = llm.complete("Your prompt here")
    response = llm.complete_json("Extract JSON", schema=...)
"""

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Provider Config
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    """User configuration for LLM provider."""
    provider: str = "minimax"  # minimax | openai | claude | local
    model: str = "MiniMax-M2.7-highspeed"
    api_key: str = ""
    base_url: str = ""
    timeout: float = 30.0
    max_tokens: int = 4096

    @classmethod
    def from_dict(cls, d: dict) -> "LLMConfig":
        return cls(
            provider=d.get("provider", "minimax"),
            model=d.get("model", "MiniMax-M2.7-highspeed"),
            api_key=d.get("api_key", ""),
            base_url=d.get("base_url", ""),
            timeout=float(d.get("timeout", 30.0)),
            max_tokens=int(d.get("max_tokens", 4096)),
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
        }


# ---------------------------------------------------------------------------
# Base Provider
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract base for all LLM providers."""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    def complete(self, prompt: str, system: str = None, max_tokens: int = None) -> str:
        """
        Return text completion. Should handle errors gracefully.
        Returns error message string on failure (starts with [ERROR]).
        """

    @abstractmethod
    def complete_json(self, prompt: str, system: str = None, schema: dict = None) -> dict:
        """
        Return JSON-serializable dict.
        Returns {"error": "...", "raw": "..."} on failure.
        """

    @abstractmethod
    def name(self) -> str:
        """Provider name for logging."""

    def _error(self, msg: str) -> str:
        return f"[ERROR:{self.name()}] {msg}"

    def _json_error(self, msg: str, raw: str = "") -> dict:
        return {"error": msg, "raw": raw}


# ---------------------------------------------------------------------------
# MiniMax Provider
# ---------------------------------------------------------------------------

class MiniMaxProvider(LLMProvider):
    """MiniMax API via OpenAI-compatible endpoint."""

    DEFAULT_URL = "https://api.minimaxi.com/anthropic/v1/messages"

    def name(self) -> str:
        return "minimax"

    def complete(self, prompt: str, system: str = None, max_tokens: int = None) -> str:
        import subprocess

        if not self.config.api_key:
            return self._error("No API key configured")

        base_url = self.config.base_url or self.DEFAULT_URL
        max_tokens = max_tokens or self.config.max_tokens

        system_prompt = system or (
            "You are a helpful AI assistant. "
            "Be concise and direct. Respond in the same language as the query."
        )

        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }

        cmd = [
            "curl", "-s", "--ipv4",
            "-X", "POST", base_url,
            "-H", f"Authorization: Bearer {self.config.api_key}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.config.timeout)
            if result.returncode != 0:
                return self._error(f"curl exit {result.returncode}: {result.stderr[:100]}")

            data = json.loads(result.stdout)
            if "error" in data:
                return self._error(str(data["error"]))

            content = data.get("content", [])
            if isinstance(content, list):
                text = "\n".join(block.get("text", "") for block in content if block.get("type") == "text")
            else:
                text = str(content)

            return text.strip()

        except json.JSONDecodeError:
            return self._error(f"Invalid JSON response: {result.stdout[:200]}")
        except subprocess.TimeoutExpired:
            return self._error(f"Timeout after {self.config.timeout}s")
        except Exception as e:
            return self._error(str(e))

    def complete_json(self, prompt: str, system: str = None, schema: dict = None) -> dict:
        """Return parsed JSON. Attempts JSON.parse of complete() output."""
        text = self.complete(prompt, system)
        if text.startswith("[ERROR"):
            return self._json_error(text)

        try:
            # Strip markdown code blocks if present
            cleaned = text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            return self._json_error(f"JSON parse failed: {e}", raw=text[:500])


# ---------------------------------------------------------------------------
# OpenAI Provider
# ---------------------------------------------------------------------------

class OpenAIProvider(LLMProvider):
    """OpenAI API (compatible endpoint)."""

    DEFAULT_URL = "https://api.openai.com/v1/chat/completions"

    def name(self) -> str:
        return "openai"

    def complete(self, prompt: str, system: str = None, max_tokens: int = None) -> str:
        import subprocess

        if not self.config.api_key:
            return self._error("No API key configured")

        base_url = self.config.base_url or self.DEFAULT_URL
        max_tokens = max_tokens or self.config.max_tokens

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }

        cmd = [
            "curl", "-s",
            "-X", "POST", f"{base_url}/chat/completions",
            "-H", f"Authorization: Bearer {self.config.api_key}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.config.timeout)
            if result.returncode != 0:
                return self._error(f"curl exit {result.returncode}")

            data = json.loads(result.stdout)
            if "error" in data:
                return self._error(str(data["error"]))

            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
            return ""

        except Exception as e:
            return self._error(str(e))

    def complete_json(self, prompt: str, system: str = None, schema: dict = None) -> dict:
        text = self.complete(prompt, system)
        if text.startswith("[ERROR"):
            return self._json_error(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            return self._json_error(f"JSON parse failed: {e}", raw=text[:500])


# ---------------------------------------------------------------------------
# Local Provider (ollama / lm-studio)
# ---------------------------------------------------------------------------

class LocalProvider(LLMProvider):
    """Local LLM via Ollama or LM Studio REST API."""

    DEFAULT_URL = "http://localhost:11434"

    def name(self) -> str:
        return "local"

    def complete(self, prompt: str, system: str = None, max_tokens: int = None) -> str:
        import subprocess

        base_url = self.config.base_url or self.DEFAULT_URL
        max_tokens = max_tokens or self.config.max_tokens

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.config.model or "llama3",
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.3,
            }
        }

        cmd = [
            "curl", "-s",
            "-X", "POST", f"{base_url}/api/chat",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.config.timeout)
            if result.returncode != 0:
                return self._error(f"curl exit {result.returncode}")

            data = json.loads(result.stdout)
            if "error" in data:
                return self._error(str(data["error"]))

            content = data.get("message", {}).get("content", "")
            return content.strip()

        except Exception as e:
            return self._error(str(e))

    def complete_json(self, prompt: str, system: str = None, schema: dict = None) -> dict:
        text = self.complete(prompt, system)
        if text.startswith("[ERROR"):
            return self._json_error(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            return self._json_error(f"JSON parse failed: {e}", raw=text[:500])


# Module-level flag: True if this module loaded without fatal errors
LLM_AVAILABLE = True

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "minimax": MiniMaxProvider,
    "openai": OpenAIProvider,
    "local": LocalProvider,
}


def get_llm(config: LLMConfig = None) -> LLMProvider:
    """
    Factory: create LLM provider from config.
    If no config provided, reads from ~/.amber-hunter/config.json
    """
    if config is None:
        config = load_llm_config()

    provider_class = _PROVIDERS.get(config.provider, MiniMaxProvider)
    return provider_class(config)


def load_llm_config() -> LLMConfig:
    """Load LLM config from ~/.amber-hunter/config.json

    Priority:
    1. os.environ["MINIMAX_API_KEY"] (explicit env var)
    2. config["llm"] (new v1.2 format)
    3. config["api_key"] (legacy amber-hunter token — NOT an LLM key, skip)
    4. ~/.openclaw/openclaw.json provider minimax-cn apiKey
    """
    # 1. Environment variable
    env_key = os.environ.get("MINIMAX_API_KEY", "")
    if env_key and env_key.startswith("sk-"):
        return LLMConfig(
            provider="minimax",
            model="MiniMax-M2.7-highspeed",
            api_key=env_key,
            base_url="https://api.minimaxi.com/anthropic/v1/messages",
        )

    # 2. New nested format
    config_path = os.path.expanduser("~/.amber-hunter/config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                data = json.load(f)
            if "llm" in data:
                return LLMConfig.from_dict(data["llm"])
        except (json.JSONDecodeError, IOError):
            pass

    # 3. Legacy root-level api_key (only use if it looks like an LLM key)
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                data = json.load(f)
            root_key = data.get("api_key", "")
            # Amber-hunter tokens are NOT LLM keys — skip them
            if root_key and root_key.startswith("sk-cp-") and len(root_key) > 50:
                return LLMConfig(
                    provider="minimax",
                    model="MiniMax-M2.7-highspeed",
                    api_key=root_key,
                    base_url="https://api.minimaxi.com/anthropic/v1/messages",
                )
        except:
            pass

    # 4. OpenClaw config (~/.openclaw/openclaw.json)
    # Providers are at models.providers (not top-level)
    openclaw_config = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.exists(openclaw_config):
        try:
            with open(openclaw_config) as f:
                oc = json.load(f)
            models = oc.get("models", {})
            providers = models.get("providers", {})
            mc = providers.get("minimax-cn", {})
            oc_key = mc.get("apiKey", "")
            if oc_key and oc_key.startswith("sk-"):
                base = mc.get("baseUrl", "https://api.minimaxi.com/anthropic")
                # Ensure /v1/messages suffix
                if not base.endswith("/messages"):
                    base = base.rstrip("/") + "/v1/messages"
                return LLMConfig(
                    provider="minimax",
                    model="MiniMax-M2.7-highspeed",
                    api_key=oc_key,
                    base_url=base,
                )
        except:
            pass

    return LLMConfig()


def save_llm_config(config: LLMConfig) -> None:
    """Save LLM config to ~/.amber-hunter/config.json"""
    config_path = os.path.expanduser("~/.amber-hunter/config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    # Read existing config
    data = {}
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                data = json.load(f)
        except:
            pass

    data["llm"] = config.to_dict()

    with open(config_path, "w") as f:
        json.dump(data, f, indent=2)
