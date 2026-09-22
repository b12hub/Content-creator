"""Dependency injection: the LLM client lives on app.state (created once in lifespan)."""
from __future__ import annotations

import logging

from fastapi import Request

from app.config import Settings, get_settings
from app.llm.base import LLMClient, MissingApiKey

log = logging.getLogger("biolife.llm")


class BrokenLLMClient:
    """Stand-in used when credentials are missing: the app still starts (the Telegram bot and the
    media planner do not need an LLM), and the LLM endpoints answer 502 with the real reason
    instead of crashing with a TypeError from inside the SDK."""

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.model = "unavailable"
        self._reason = reason

    async def generate_structured(self, *, system: str, user: str, schema: type) -> object:
        raise MissingApiKey(self._reason)

    async def aclose(self) -> None:
        return None


def build_llm_client(settings: Settings) -> LLMClient:
    """Credentials are read from Settings and passed to the SDK explicitly."""
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_client import AnthropicClient
        try:
            client = AnthropicClient(
                settings.anthropic_model, settings.max_output_tokens, settings.llm_timeout_s,
                api_key=settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else "")
        except MissingApiKey as exc:
            log.error("Anthropic disabled: %s", exc)
            return BrokenLLMClient("anthropic", str(exc))
        log.info("LLM provider: anthropic model=%s key=***%s", settings.anthropic_model,
                 settings.anthropic_api_key.get_secret_value()[-4:])
        return client
    if settings.llm_provider == "openai":
        from app.llm.openai_client import OpenAIClient
        try:
            return OpenAIClient(
                settings.openai_model, settings.max_output_tokens, settings.llm_timeout_s,
                api_key=settings.openai_api_key.get_secret_value() if settings.openai_api_key else "")
        except (MissingApiKey, ValueError) as exc:
            log.error("OpenAI disabled: %s", exc)
            return BrokenLLMClient("openai", str(exc))
    from app.llm.fake_client import FakeLLMClient
    log.info("LLM provider: fake (offline demo)")
    return FakeLLMClient()


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm_client


def settings_dep() -> Settings:
    return get_settings()
