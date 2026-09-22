"""Hybrid client: Anthropic first, OpenRouter when Anthropic cannot answer.

Design rules
  • Only INFRASTRUCTURE failures trigger the fallback: bad/missing key, rate limit, overloaded (529),
    timeout, connection error, any 4xx/5xx from the API.
  • A model REFUSAL never triggers it. Routing refused content to another provider would be a way
    around the safety decision, not a resilience feature.
  • generate_structured() does NOT fall back: the JSON endpoints depend on Anthropic's structured
    outputs, and a free OpenRouter model gives no schema guarantee. Failing loudly is safer than
    returning JSON that silently does not match the contract.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from app.llm.base import LLMClient, LLMError, LLMRefusal

log = logging.getLogger("biolife.llm.fallback")

T = TypeVar("T", bound=BaseModel)

# Anthropic exceptions that mean "the API could not serve this request".
API_FAILURES: tuple[type[Exception], ...] = (
    anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.RateLimitError,
    anthropic.InternalServerError, anthropic.OverloadedError, anthropic.APIStatusError,
    anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.APIError,
    LLMError,               # our wrappers already translate the SDK errors
)


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    provider: str           # "anthropic" | "openrouter" | "fake"
    model: str
    degraded: bool          # True when the answer came from the fallback

    def __str__(self) -> str:          # lets old call sites keep treating it as text
        return self.text


class FallbackLLMClient:
    """Implements the LLMClient protocol, so handlers and services need no special case."""

    def __init__(self, primary: LLMClient, fallback: LLMClient | None,
                 primary_timeout_s: float | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        # Hard cap on the primary attempt: SDK retries can otherwise eat the whole budget and the
        # fallback never gets to run (the handler's timeout fires first).
        self.primary_timeout_s = primary_timeout_s
        self.provider = primary.provider
        self.model = primary.model

    async def generate_text(self, *, system: str, user: str, model: str | None = None,
                            max_tokens: int | None = None) -> str:
        return (await self.generate(system=system, user=user, model=model, max_tokens=max_tokens)).text

    async def generate(self, *, system: str, user: str, model: str | None = None,
                       max_tokens: int | None = None) -> GenerationResult:
        try:
            if self.primary_timeout_s:
                async with asyncio.timeout(self.primary_timeout_s):
                    text = await self.primary.generate_text(system=system, user=user, model=model,
                                                            max_tokens=max_tokens)
            else:
                text = await self.primary.generate_text(system=system, user=user, model=model,
                                                        max_tokens=max_tokens)
            return GenerationResult(text=text, provider=self.primary.provider,
                                    model=model or self.primary.model, degraded=False)
        except LLMRefusal:
            raise                                   # a refusal is an answer, not an outage
        except TimeoutError as exc:                 # includes asyncio.TimeoutError
            if self.fallback is None:
                raise LLMError(f"{self.primary.provider} timed out after "
                               f"{self.primary_timeout_s:.0f}s") from exc
            log.warning("primary provider %s timed out after %.0fs - falling back to %s",
                        self.primary.provider, self.primary_timeout_s or 0, self.fallback.provider)
            return await self._run_fallback(system, user, max_tokens,
                                            reason=f"timeout after {self.primary_timeout_s:.0f}s")
        except API_FAILURES as exc:
            if self.fallback is None:
                raise
            log.warning("primary provider %s failed (%s: %s) - falling back to %s",
                        self.primary.provider, type(exc).__name__, str(exc)[:200],
                        self.fallback.provider)
            return await self._run_fallback(system, user, max_tokens, reason=str(exc))

    async def _run_fallback(self, system: str, user: str, max_tokens: int | None,
                            *, reason: str) -> GenerationResult:
        assert self.fallback is not None
        try:
            text = await self.fallback.generate_text(system=system, user=user, max_tokens=max_tokens)
        except Exception as fallback_exc:
            log.error("fallback provider %s also failed: %s", self.fallback.provider, fallback_exc)
            raise LLMError(f"{self.primary.provider}: {reason} | "
                           f"{self.fallback.provider}: {fallback_exc}") from fallback_exc
        log.info("answered by the fallback provider %s", self.fallback.provider)
        return GenerationResult(text=text, provider=self.fallback.provider,
                                model=self.fallback.model, degraded=True)

    async def generate_structured(self, *, system: str, user: str, schema: type[T]) -> T:
        """No fallback on purpose (see the module docstring)."""
        return await self.primary.generate_structured(system=system, user=user, schema=schema)

    async def aclose(self) -> None:
        for client in (self.primary, self.fallback):
            close = getattr(client, "aclose", None)
            if close:
                await close()
