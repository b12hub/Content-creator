"""OpenRouter fallback client (OpenAI-compatible chat completions over httpx).

Verified against OpenRouter's docs (Sep 2026):
  • POST https://openrouter.ai/api/v1/chat/completions
  • Authorization: Bearer <key>; HTTP-Referer identifies the app (required for attribution),
    X-Title is still accepted (X-OpenRouter-Title is the newer name - both are sent).
  • Errors come back as HTTP status codes (400/401/402/403/404/408/413/422/429/500/502/503/524/529)
    with {"error": {"code", "message"}}; success is {"choices": [{"message": {"content": ...}}]}.
  • ':free' models are capped at 20 requests/minute and 50 requests/day (1000 after buying
    at least 10 credits), so the fallback is a safety net, not a second primary.

httpx is used directly: it already ships with FastAPI, so no new dependency.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.llm.base import LLMError

log = logging.getLogger("biolife.llm.openrouter")

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 524, 529}


class OpenRouterClient:
    provider = "openrouter"

    def __init__(self, api_key: str, model: str, *, base_url: str = "https://openrouter.ai/api/v1",
                 timeout_s: float = 60.0, referer: str = "https://biolife.uz",
                 title: str = "BioLife Telegram Bot") -> None:
        if not api_key or not api_key.strip():
            raise LLMError("OPENROUTER_API_KEY is empty")
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "HTTP-Referer": referer,
            "X-Title": title,
            "X-OpenRouter-Title": title,
            "Content-Type": "application/json",
        }
        self._timeout = timeout_s
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    def _http(self) -> httpx.AsyncClient:
        """One connection pool per process, created lazily so nothing opens at import time.
        After aclose() it is NOT recreated: a resurrected pool would never be closed again."""
        if self._closed:
            raise LLMError("OpenRouter client is closed")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, headers=self._headers)
        return self._client

    async def generate_text(self, *, system: str, user: str, model: str | None = None,
                            max_tokens: int | None = None) -> str:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            response = await self._http().post(f"{self._base_url}/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMError(f"OpenRouter timed out after {self._timeout:.0f}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenRouter is unreachable: {exc}") from exc

        if response.status_code != 200:
            raise LLMError(self._describe_error(response))

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("OpenRouter returned a non-JSON body") from exc

        # Defensive: some providers answer 200 with an error object instead of choices.
        if isinstance(data, dict) and data.get("error") and not data.get("choices"):
            raise LLMError(f"OpenRouter error: {str(data['error'])[:200]}")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMError(f"Unexpected OpenRouter response shape: {str(data)[:200]}") from exc
        text = _content_to_text(content)
        if not text:
            raise LLMError("OpenRouter returned empty content "
                           "(a free model may be overloaded - try another one)")
        finish = (data["choices"][0].get("finish_reason") or "").lower()
        if finish == "length":
            text += "\n\n[…]"
        return text

    def _describe_error(self, response: httpx.Response) -> str:
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("error", body))[:200]
        except ValueError:
            detail = response.text[:200]
        if response.status_code == 401:
            return f"OpenRouter rejected the key (401): {detail}"
        if response.status_code == 402:
            return f"OpenRouter needs credit (402): {detail}"
        if response.status_code == 404:
            return (f"OpenRouter does not know the model {self.model!r} (404). "
                    "Check the id at https://openrouter.ai/api/v1/models")
        if response.status_code == 429:
            return (f"OpenRouter rate limit (429): {detail}. Free models allow 20 requests/minute "
                    "and 50/day (1000 with 10+ credits purchased).")
        if response.status_code in RETRYABLE_STATUS:
            return f"OpenRouter is busy ({response.status_code}): {detail}"
        return f"OpenRouter error {response.status_code}: {detail}"

    async def warn_if_model_unknown(self) -> None:
        """Startup check: a stale slug would otherwise surface as a 404 during the first outage."""
        exists = await self.model_exists()
        if exists is False:
            log.error("OpenRouter does not list the fallback model %r - the fallback will fail with "
                      "404. Pick a current id from https://openrouter.ai/api/v1/models", self.model)
        elif exists:
            log.info("OpenRouter fallback model %r is available", self.model)

    async def model_exists(self, model: str | None = None) -> bool | None:
        """True/False, or None when the catalogue could not be read."""
        wanted = model or self.model
        try:
            response = await self._http().get(f"{self._base_url}/models")
            response.raise_for_status()
            ids = {item.get("id") for item in response.json().get("data", [])}
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            log.info("could not verify the OpenRouter model list: %s", exc)
            return None
        return wanted in ids

    async def aclose(self) -> None:
        self._closed = True
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()


def _content_to_text(content: object) -> str:
    """Most providers answer with a string, some with a list of content parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()
