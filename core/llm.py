"""
core/llm.py — LLM Provider Abstraction Layer

Unified interface for MiniMax / OpenAI / Claude / Local models.
Users configure which provider to use; all calls go through this layer.

Usage:
    from core.llm import get_llm, LLMProvider

    llm = get_llm()
    response = llm.complete("Your prompt here")
    response = llm.acomplete("Your prompt here")  # async version
    response = llm.complete_json("Extract JSON")
"""

import asyncio
import json
import os
import subprocess
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
    max_tokens: int = 2048
    temperature: float = 0.3  # 0.0~1.0, default 0.3

    @classmethod
    def from_dict(cls, d: dict) -> "LLMConfig":
        return cls(
            provider=d.get("provider", "minimax"),
            model=d.get("model", "MiniMax-M2.7-highspeed"),
            api_key=d.get("api_key", ""),
            base_url=d.get("base_url", ""),
            timeout=float(d.get("timeout", 30.0)),
            max_tokens=int(d.get("max_tokens", 2048)),
            temperature=float(d.get("temperature", 0.3)),
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }


# ---------------------------------------------------------------------------
# Base Provider
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract base for all LLM providers."""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """
        Return text completion (synchronous).
        Should handle errors gracefully — returns error message string on failure.
        """

    async def acomplete(
        self,
        prompt: str,
        system: str = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """
        Async version of complete(). Default implementation wraps sync complete()
        via asyncio.to_thread() — simple and effective for I/O-bound curl calls.
        """
        return await asyncio.to_thread(
            self.complete, prompt, system, max_tokens, temperature
        )

    @abstractmethod
    def complete_json(self, prompt: str, system: str = None) -> dict:
        """
        Return JSON-serializable dict.
        Returns {"error": "...", "raw": "..."} on failure.
        """

    @abstractmethod
    def provider_name(self) -> str:
        """Provider name for logging."""

    # Alias for backward compat
    name = provider_name

    def _error(self, msg: str) -> str:
        return f"[ERROR:{self.provider_name()}] {msg}"

    def _json_error(self, msg: str, raw: str = "") -> dict:
        return {"error": msg, "raw": raw}


# ---------------------------------------------------------------------------
# MiniMax Provider
# ---------------------------------------------------------------------------

class MiniMaxProvider(LLMProvider):
    """MiniMax API via OpenAI-compatible endpoint."""

    DEFAULT_URL = "https://api.minimaxi.com/anthropic/v1/messages"

    def provider_name(self) -> str:
        return "minimax"

    def complete(
        self,
        prompt: str,
        system: str = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        if not self.config.api_key:
            return self._error("No API key configured")

        base_url = self.config.base_url or self.DEFAULT_URL

        system_prompt = system or (
            "You are a helpful AI assistant. "
            "Be concise and direct. Respond in the same language as the query."
        )

        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
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
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.config.timeout
            )
            if result.returncode != 0:
                return self._error(f"curl exit {result.returncode}: {result.stderr[:100]}")

            data = json.loads(result.stdout)
            if "error" in data:
                return self._error(str(data["error"]))

            content = data.get("content", [])
            if isinstance(content, list):
                text = "\n".join(
                    block.get("text", "")
                    for block in content
                    if block.get("type") == "text"
                )
            else:
                text = str(content)

            return text.strip()

        except json.JSONDecodeError:
            return self._error(f"Invalid JSON response: {result.stdout[:200]}")
        except subprocess.TimeoutExpired:
            return self._error(f"Timeout after {self.config.timeout}s")
        except Exception as e:
            return self._error(str(e))

    def complete_json(self, prompt: str, system: str = None) -> dict:
        text = self.complete(prompt, system)
        if text.startswith("[ERROR"):
            return self._json_error(text)

        try:
            cleaned = text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(
                    lines[1:] if lines[0].startswith("```") else lines
                )
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

    def provider_name(self) -> str:
        return "openai"

    def complete(
        self,
        prompt: str,
        system: str = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        if not self.config.api_key:
            return self._error("No API key configured")

        base_url = self.config.base_url or self.DEFAULT_URL

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        cmd = [
            "curl", "-s",
            "-X", "POST", f"{base_url}/chat/completions",
            "-H", f"Authorization: Bearer {self.config.api_key}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.config.timeout
            )
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

    def complete_json(self, prompt: str, system: str = None) -> dict:
        text = self.complete(prompt, system)
        if text.startswith("[ERROR"):
            return self._json_error(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            return self._json_error(f"JSON parse failed: {e}", raw=text[:500])


# ---------------------------------------------------------------------------
# Claude Provider (NEW — Phase 1)
# ---------------------------------------------------------------------------

class ClaudeProvider(LLMProvider):
    """
    Anthropic Claude API.
    Model: claude-3-5-haiku-20241022 (fast, cheap).
    API: https://api.anthropic.com/v1/messages
    """

    DEFAULT_URL = "https://api.anthropic.com/v1/messages"
    DEFAULT_MODEL = "claude-3-5-haiku-20241022"

    def provider_name(self) -> str:
        return "claude"

    def _build_payload(
        self,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> dict:
        model = self.config.model or self.DEFAULT_MODEL
        return {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system or "",
            "messages": [{"role": "user", "content": prompt}],
        }

    def _headers(self) -> dict:
        return {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _call_api(self, payload: dict) -> str:
        base_url = self.config.base_url or self.DEFAULT_URL

        cmd = [
            "curl", "-s", "--ipv4",
            "-X", "POST", base_url,
            "-H", f"x-api-key: {self.config.api_key}",
            "-H", "anthropic-version: 2023-06-01",
            "-H", "content-type: application/json",
            "-d", json.dumps(payload),
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=self.config.timeout
        )
        if result.returncode != 0:
            raise RuntimeError(f"curl exit {result.returncode}: {result.stderr[:100]}")

        data = json.loads(result.stdout)

        # Anthropic error format
        if "error" in data:
            raise RuntimeError(str(data["error"]))

        # Claude returns: { "content": [{ "type": "text", "text": "..." }] }
        content = data.get("content", [])
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    return block.get("text", "").strip()
        return ""

    def complete(
        self,
        prompt: str,
        system: str = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        if not self.config.api_key:
            return self._error("No API key configured")

        try:
            payload = self._build_payload(prompt, system, max_tokens, temperature)
            return self._call_api(payload)
        except json.JSONDecodeError:
            return self._error("Invalid JSON response from Claude API")
        except subprocess.TimeoutExpired:
            return self._error(f"Timeout after {self.config.timeout}s")
        except Exception as e:
            return self._error(str(e))

    def complete_json(self, prompt: str, system: str = None) -> dict:
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

    def provider_name(self) -> str:
        return "local"

    def complete(
        self,
        prompt: str,
        system: str = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        base_url = self.config.base_url or self.DEFAULT_URL

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
                "temperature": temperature,
            }
        }

        cmd = [
            "curl", "-s",
            "-X", "POST", f"{base_url}/api/chat",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.config.timeout
            )
            if result.returncode != 0:
                return self._error(f"curl exit {result.returncode}")

            data = json.loads(result.stdout)
            if "error" in data:
                return self._error(str(data["error"]))

            content = data.get("message", {}).get("content", "")
            return content.strip()

        except Exception as e:
            return self._error(str(e))

    def complete_json(self, prompt: str, system: str = None) -> dict:
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
    "claude": ClaudeProvider,  # Phase 1: NEW
    "local": LocalProvider,
}


def get_llm(config: LLMConfig = None) -> LLMProvider:
    """
    Factory: create LLM provider from config.
    If no config provided, reads from ~/.amber-hunter/config.json
    """
    if config is None:
        config = load_llm_config()

    # Support aliases: "ollama" → LocalProvider
    provider_key = config.provider.lower()
    if provider_key in ("ollama",):
        provider_key = "local"

    provider_class = _PROVIDERS.get(provider_key, MiniMaxProvider)
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
