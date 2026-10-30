"""
LLMAdapter — Muse (Meta Model API) LLM interface.

Muse is the only supported LLM provider. The Meta Model API exposes an
OpenAI-compatible chat completions endpoint:

    POST {base_url}/chat/completions
    Authorization: Bearer <META_API_KEY>

Config (from config.yaml):
  llm:
    provider: muse                     # only supported value
    model: muse-spark-1.3              # Muse Spark model id
    api_key: LLM|...                   # or META_API_KEY env var
    base_url: https://api.meta.ai/v1   # optional override
    call_interval_sec: 120
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("muse-iot.llm_adapter")

PROVIDER = "muse"
DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"

MAX_RETRIES = 3
RETRY_DELAY = 5   # seconds


@dataclass
class LLMResponse:
    raw_text: str
    commands: dict
    model: str
    latency_ms: int
    tokens_used: int
    reason: str


class LLMAdapter:

    def __init__(self, config: dict):
        provider = config.get("provider", PROVIDER)
        if provider != PROVIDER:
            raise ValueError(
                f"Unsupported LLM provider: {provider!r} — "
                f"muse-iot supports only '{PROVIDER}' (Meta Model API)."
            )
        self.provider = PROVIDER
        self.model    = config.get("model", DEFAULT_MODEL)
        self.api_key  = config.get("api_key") or self._get_env_key()
        self.base_url = (config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.max_tokens = config.get("max_tokens", 1024)

    def call_freetext(self, context: dict) -> str:
        """Call Muse without JSON constraints — returns raw text. Used by ReflectionEngine."""
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(MAX_RETRIES):
            try:
                return self._call_freetext_once(context)
            except Exception as e:
                last_exc = e
                if "429" in str(e):
                    raise
                log.warning(f"Freetext LLM call attempt {attempt+1} failed: {type(e).__name__}: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
        raise RuntimeError(f"Freetext LLM call failed after {MAX_RETRIES} attempts: {type(last_exc).__name__}: {last_exc}")

    def _call_freetext_once(self, context: dict) -> str:
        text, _ = self._call_muse(context, json_mode=False)
        return text

    def call(self, context: dict) -> LLMResponse:
        """
        Send assembled context to Muse and return parsed response.
        Retries up to MAX_RETRIES on transient failures.
        429 rate-limit errors are NOT retried — the poll interval provides
        the natural back-off so we don't burn quota on repeated attempts.
        On retry, a strict JSON reminder is appended to break markdown reasoning loops.
        """
        JSON_REMINDER = (
            "\n\nCRITICAL: your entire response must be a single JSON object. "
            "No bullets, no markdown, no explanation. Start with { and end with }."
        )
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(MAX_RETRIES):
            try:
                ctx = context if attempt == 0 else {
                    **context,
                    "user": context["user"] + JSON_REMINDER,
                }
                return self._call_once(ctx)
            except Exception as e:
                last_exc = e
                if "429" in str(e):
                    log.warning("LLM rate limited (429) — skipping retries, will try next poll cycle")
                    raise
                log.warning(f"LLM call attempt {attempt+1} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
        raise RuntimeError(f"LLM call failed after {MAX_RETRIES} attempts: {type(last_exc).__name__}: {last_exc}")

    def _call_once(self, context: dict) -> LLMResponse:
        t0 = time.time()

        raw, tokens = self._call_muse(context)

        latency_ms = int((time.time() - t0) * 1000)
        commands   = self._parse_json(raw)
        reason     = commands.pop("reason", "")

        return LLMResponse(
            raw_text=raw,
            commands=commands,
            model=self.model,
            latency_ms=latency_ms,
            tokens_used=tokens,
            reason=reason,
        )

    # ── Muse (Meta Model API, OpenAI-compatible) ─────────────────────────────

    def _call_muse(self, context: dict, json_mode: bool = True) -> tuple[str, int]:
        import requests
        if not self.api_key:
            raise ValueError(
                "Missing Muse API key — set llm.api_key in config.yaml "
                "or the META_API_KEY environment variable."
            )
        body: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": context["system"]},
                {"role": "user",   "content": context["user"]},
            ],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if not choices or not choices[0].get("message", {}).get("content"):
            raise ValueError(f"Muse returned no content. Full response: {data}")

        text = choices[0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        log.debug(f"Muse raw response: {text[:300]}")
        return text, tokens

    # ── JSON parsing ──────────────────────────────────────────────────────

    def _parse_json(self, raw: str) -> dict:
        """Extract first complete JSON object from LLM response."""
        log.info(f"Parsing LLM response for JSON content (truncated): {raw}")
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
        start = text.find("{")
        if start == -1:
            raise ValueError(f"No JSON object found in LLM response: {raw[:200]}")
        try:
            obj, _ = json.JSONDecoder().raw_decode(text, start)
            return obj
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON parse failed: {e} — raw: {raw[:200]}")

    def _get_env_key(self) -> Optional[str]:
        return (
            os.getenv("META_API_KEY") or
            os.getenv("MUSE_API_KEY") or
            os.getenv("MUSE_IOT_LLM_KEY")
        )
