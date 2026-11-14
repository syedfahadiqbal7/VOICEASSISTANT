# agent/llm_client.py
"""
Local Ollama client with optional cloud failover + redaction.

Config file (optional): history/llm_failover.json
The API key is intentionally NOT stored in this JSON file. Configure it only
through the VOICEASSISTANT_CLOUD_API_KEY environment variable.

Env overrides (optional):
  VOICEASSISTANT_CLOUD_BASE_URL
  VOICEASSISTANT_CLOUD_API_KEY
  VOICEASSISTANT_CLOUD_MODEL
  VOICEASSISTANT_FAILOVER=1
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
try:
    from openai import OpenAI
except ImportError:  # Keep non-LLM subsystems importable for diagnostics and degraded startup.
    OpenAI = None

from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, DEFAULT_MODEL
from agent.redaction import redact_messages

try:
    from config import BASE_DIR
except Exception:
    BASE_DIR = Path(".").resolve()

FAILOVER_FILE = Path(BASE_DIR) / "history" / "llm_failover.json"

DEFAULT_FAILOVER: Dict[str, Any] = {
    "enabled": False,
    "cloud_base_url": "https://api.openai.com/v1",
    "cloud_api_key": "",
    "cloud_model": "gpt-4o-mini",
    "local_timeout": 90,
    "cloud_timeout": 60,
    "cloud_allow_tools": False,
}


class LLMClient:
    def __init__(self):
        self.local_base = OLLAMA_BASE_URL
        self.client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY) if OpenAI else None
        self.model = DEFAULT_MODEL
        self.last_provider = "local"  # local | cloud
        self.last_error: Optional[str] = None
        self.failover_cfg = self._load_failover_cfg()
        self._cloud_client: Optional[OpenAI] = None

    def _load_failover_cfg(self) -> Dict[str, Any]:
        cfg = dict(DEFAULT_FAILOVER)
        if FAILOVER_FILE.exists():
            try:
                raw = json.loads(FAILOVER_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    if raw.get("cloud_api_key"):
                        print("[LLMClient] Ignoring API key stored in JSON; use VOICEASSISTANT_CLOUD_API_KEY instead.")
                    raw.pop("cloud_api_key", None)
                    cfg.update(raw)
            except Exception as e:
                print(f"[LLMClient] failover config load failed: {e}")
        # Env overrides
        if os.environ.get("VOICEASSISTANT_FAILOVER", "").strip() in ("1", "true", "yes"):
            cfg["enabled"] = True
        if os.environ.get("VOICEASSISTANT_CLOUD_BASE_URL"):
            cfg["cloud_base_url"] = os.environ["VOICEASSISTANT_CLOUD_BASE_URL"].strip()
        if os.environ.get("VOICEASSISTANT_CLOUD_API_KEY"):
            cfg["cloud_api_key"] = os.environ["VOICEASSISTANT_CLOUD_API_KEY"].strip()
        if os.environ.get("VOICEASSISTANT_CLOUD_MODEL"):
            cfg["cloud_model"] = os.environ["VOICEASSISTANT_CLOUD_MODEL"].strip()
        return cfg

    def reload_failover_cfg(self) -> None:
        self.failover_cfg = self._load_failover_cfg()
        self._cloud_client = None

    def save_failover_cfg(self, updates: Dict[str, Any]) -> None:
        updates = dict(updates or {})
        # Accept a key for the current process only; persistence is environment-only.
        if "cloud_api_key" in updates:
            key = str(updates.pop("cloud_api_key") or "").strip()
            if key:
                self.failover_cfg["cloud_api_key"] = key
        self.failover_cfg.update(updates)
        FAILOVER_FILE.parent.mkdir(parents=True, exist_ok=True)
        to_save = dict(self.failover_cfg)
        to_save.pop("cloud_api_key", None)
        from agent.storage import atomic_write_json
        atomic_write_json(FAILOVER_FILE, to_save, indent=2, ensure_ascii=False)
        self._cloud_client = None

    def set_model(self, model_name: str):
        self.model = model_name

    def is_local_available(self, timeout: float = 2.0) -> bool:
        try:
            # OLLAMA_BASE_URL is like http://localhost:11434/v1
            base = self.local_base.rstrip("/")
            if base.endswith("/v1"):
                root = base[:-3]
            else:
                root = base
            resp = requests.get(f"{root}/api/tags", timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def _get_cloud_client(self) -> Optional[OpenAI]:
        cfg = self.failover_cfg
        key = (cfg.get("cloud_api_key") or "").strip()
        base = (cfg.get("cloud_base_url") or "").strip()
        if not key or not base or OpenAI is None:
            return None
        if self._cloud_client is None:
            self._cloud_client = OpenAI(base_url=base, api_key=key)
        return self._cloud_client

    def _is_failover_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        keys = (
            "connection",
            "timeout",
            "timed out",
            "connecterror",
            "refused",
            "unavailable",
            "apiconnectionerror",
            "read timeout",
            "failed to establish",
            "10061",  # Win refused
            "actively refused",
        )
        return any(k in msg for k in keys)

    def chat(self, messages: list, tools: list | None = None, timeout: float | None = None):
        """
        Prefer local Ollama. On connection/timeout, optional cloud failover
        with redacted messages. Cloud tools off by default.
        """
        self.last_error = None
        cfg = self.failover_cfg
        local_timeout = timeout if timeout is not None else float(cfg.get("local_timeout") or 90)

        kwargs_local: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "timeout": local_timeout,
        }
        if tools:
            kwargs_local["tools"] = tools

        # Fast pre-check: if local clearly down and failover on, skip straight to cloud
        skip_local = False
        if cfg.get("enabled") and not self.is_local_available(timeout=1.5):
            skip_local = True
            print("[LLMClient] Local Ollama not reachable — trying cloud failover")

        if not skip_local:
            try:
                if self.client is None:
                    raise RuntimeError("The 'openai' Python package is required for Ollama's OpenAI-compatible API client.")
                self.last_provider = "local"
                return self.client.chat.completions.create(**kwargs_local)
            except Exception as e:
                self.last_error = str(e)
                if not (cfg.get("enabled") and self._is_failover_error(e)):
                    raise
                print(f"[LLMClient] Local failed ({e}) — cloud failover")

        # Cloud path
        cloud = self._get_cloud_client()
        if cloud is None:
            raise RuntimeError(
                "Cloud failover enabled but cloud_api_key / cloud_base_url not configured. "
                "Edit history/llm_failover.json or set VOICEASSISTANT_CLOUD_* env vars."
            ) from (RuntimeError(self.last_error) if self.last_error else None)

        cloud_model = (cfg.get("cloud_model") or "gpt-4o-mini").strip()
        cloud_timeout = float(cfg.get("cloud_timeout") or 60)
        safe_messages = redact_messages(messages)

        kwargs_cloud: Dict[str, Any] = {
            "model": cloud_model,
            "messages": safe_messages,
            "timeout": cloud_timeout,
        }
        if tools and cfg.get("cloud_allow_tools"):
            kwargs_cloud["tools"] = tools
        # else: no tools on cloud by default (safer)

        self.last_provider = "cloud"
        print(f"[LLMClient] Cloud request model={cloud_model} (messages redacted)")
        return cloud.chat.completions.create(**kwargs_cloud)

    def get_available_models(self) -> list[str]:
        """Ollama se live installed models fetch karo."""
        try:
            resp = requests.get("http://localhost:11434/api/tags", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return sorted(models) if models else [self.model]
        except Exception as e:
            print(f"[LLMClient] Could not fetch models: {e}")
            # If cloud configured, still return local default
            return [self.model]

    def provider_status(self) -> str:
        local = "up" if self.is_local_available() else "down"
        cloud_ready = "yes" if self._get_cloud_client() else "no"
        en = "on" if self.failover_cfg.get("enabled") else "off"
        return (
            f"LLM: local={local} failover={en} cloud_configured={cloud_ready} "
            f"last_provider={self.last_provider}"
        )
