"""Dependency injection: the LLM client lives on app.state (created once in lifespan)."""
from __future__ import annotations

from fastapi import Request

from app.config import Settings, get_settings
from app.llm.base import LLMClient


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_client import AnthropicClient
        return AnthropicClient(settings.anthropic_model, settings.max_output_tokens, settings.llm_timeout_s)
    if settings.llm_provider == "openai":
        from app.llm.openai_client import OpenAIClient
        return OpenAIClient(settings.openai_model, settings.max_output_tokens, settings.llm_timeout_s)
    from app.llm.fake_client import FakeLLMClient
    return FakeLLMClient()


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm_client


def settings_dep() -> Settings:
    return get_settings()
