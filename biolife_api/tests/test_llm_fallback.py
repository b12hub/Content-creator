"""Anthropic -> OpenRouter fallback. OpenRouter is served by an httpx MockTransport, so the
request that would go over the network is inspected exactly as OpenRouter would receive it."""
from __future__ import annotations

import anthropic
import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.dependencies import build_fallback_client, build_llm
from app.llm.base import LLMError, LLMRefusal, MissingApiKey
from app.llm.fallback import API_FAILURES, FallbackLLMClient, GenerationResult
from app.llm.openrouter_client import OpenRouterClient
from app.models.script_package import ReelScriptLLM

pytestmark = pytest.mark.asyncio

OK_BODY = {"choices": [{"message": {"content": "Zaxira model javobi"}, "finish_reason": "stop"}]}


def router(handler) -> OpenRouterClient:
    client = OpenRouterClient("or-key", "nvidia/nemotron-3-ultra:free")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                       headers=client._headers)
    return client


class Primary:
    """Stub of the Anthropic client."""
    provider, model = "anthropic", "claude-3-5-sonnet-20240620"

    def __init__(self, error: Exception | None = None, text: str = "Asosiy model javobi"):
        self.error, self.text, self.calls = error, text, 0

    async def generate_text(self, *, system, user, model=None, max_tokens=None) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return self.text

    async def generate_structured(self, *, system, user, schema):
        if self.error:
            raise self.error
        return schema.model_construct()


# ------------------------------------------------------------ OpenRouter client
async def test_request_matches_openrouter_contract():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=OK_BODY)

    text = await router(handler).generate_text(system="SYS", user="USR", max_tokens=1500)
    assert text == "Zaxira model javobi"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer or-key"
    assert seen["headers"]["http-referer"] == "https://biolife.uz"
    assert seen["headers"]["x-title"] == "BioLife Telegram Bot"
    assert seen["body"] == {"model": "nvidia/nemotron-3-ultra:free", "max_tokens": 1500,
                            "messages": [{"role": "system", "content": "SYS"},
                                         {"role": "user", "content": "USR"}]}


@pytest.mark.parametrize("status,fragment", [
    (401, "rejected the key"), (402, "needs credit"), (404, "does not know the model"),
    (429, "rate limit"), (503, "busy"), (500, "busy"),
])
async def test_http_errors_become_readable_messages(status, fragment):
    client = router(lambda request: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(LLMError, match=fragment):
        await client.generate_text(system="s", user="u")


async def test_200_with_an_error_object_is_not_treated_as_success():
    client = router(lambda request: httpx.Response(200, json={"error": {"message": "upstream down"}}))
    with pytest.raises(LLMError, match="upstream down"):
        await client.generate_text(system="s", user="u")


@pytest.mark.parametrize("body", [{"choices": []}, {"choices": [{"message": {}}]}, {"nope": 1}])
async def test_unexpected_shapes_are_rejected(body):
    client = router(lambda request: httpx.Response(200, json=body))
    with pytest.raises(LLMError):
        await client.generate_text(system="s", user="u")


async def test_empty_content_is_an_error():
    client = router(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}))
    with pytest.raises(LLMError, match="empty content"):
        await client.generate_text(system="s", user="u")


async def test_truncated_answer_is_marked():
    body = {"choices": [{"message": {"content": "yarim javob"}, "finish_reason": "length"}]}
    assert (await router(lambda r: httpx.Response(200, json=body)).generate_text(
        system="s", user="u")).endswith("[…]")


async def test_timeout_and_network_errors():
    def boom(request):
        raise httpx.ConnectTimeout("timeout", request=request)
    with pytest.raises(LLMError, match="timed out"):
        await router(boom).generate_text(system="s", user="u")

    def refused(request):
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(LLMError, match="unreachable"):
        await router(refused).generate_text(system="s", user="u")


async def test_model_existence_check():
    body = {"data": [{"id": "qwen/qwen3.8-27b:free"}]}
    client = router(lambda r: httpx.Response(200, json=body))
    assert await client.model_exists() is False
    assert await client.model_exists("qwen/qwen3.8-27b:free") is True


# ---------------------------------------------------------------- the fallback
def make_request(*_args, **_kwargs) -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("error", [
    anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=make_request()), body=None),
    anthropic.RateLimitError("slow down", response=httpx.Response(429, request=make_request()), body=None),
    anthropic.APIStatusError("overloaded", response=httpx.Response(529, request=make_request()), body=None),
    anthropic.InternalServerError("boom", response=httpx.Response(500, request=make_request()), body=None),
    anthropic.APIConnectionError(request=make_request()),
    LLMError("Anthropic rejected the API key (401)"),
    MissingApiKey("ANTHROPIC_API_KEY is empty"),
])
async def test_api_failures_switch_to_openrouter(error):
    primary = Primary(error=error)
    client = FallbackLLMClient(primary, router(lambda r: httpx.Response(200, json=OK_BODY)))
    result = await client.generate(system="s", user="u")
    assert isinstance(result, GenerationResult)
    assert result.degraded is True and result.provider == "openrouter"
    assert result.text == "Zaxira model javobi"


async def test_healthy_primary_is_not_degraded():
    primary = Primary()
    client = FallbackLLMClient(primary, router(lambda r: pytest.fail("fallback must not be called")))
    result = await client.generate(system="s", user="u")
    assert result.degraded is False and result.provider == "anthropic" and primary.calls == 1


async def test_refusal_is_never_routed_to_another_provider():
    """A refusal is a content decision, not an outage."""
    client = FallbackLLMClient(Primary(error=LLMRefusal("refused")),
                               router(lambda r: pytest.fail("fallback must not be called")))
    with pytest.raises(LLMRefusal):
        await client.generate(system="s", user="u")


async def test_both_providers_down_reports_both_reasons():
    client = FallbackLLMClient(Primary(error=LLMError("anthropic 529")),
                               router(lambda r: httpx.Response(401, json={"error": {"message": "bad"}})))
    with pytest.raises(LLMError) as exc:
        await client.generate(system="s", user="u")
    assert "anthropic 529" in str(exc.value) and "rejected the key" in str(exc.value)


async def test_without_a_fallback_the_original_error_is_raised():
    client = FallbackLLMClient(Primary(error=LLMError("anthropic down")), None)
    with pytest.raises(LLMError, match="anthropic down"):
        await client.generate(system="s", user="u")


async def test_structured_output_never_falls_back():
    """The JSON endpoints need Anthropic's schema guarantee."""
    client = FallbackLLMClient(Primary(error=LLMError("anthropic down")),
                               router(lambda r: pytest.fail("fallback must not be called")))
    with pytest.raises(LLMError, match="anthropic down"):
        await client.generate_structured(system="s", user="u", schema=ReelScriptLLM)


async def test_generate_text_keeps_the_protocol():
    client = FallbackLLMClient(Primary(), None)
    assert await client.generate_text(system="s", user="u") == "Asosiy model javobi"


async def test_anthropic_error_classes_exist():
    """Guards against an SDK rename silently disabling the fallback."""
    for cls in (anthropic.AuthenticationError, anthropic.RateLimitError, anthropic.APIStatusError,
                anthropic.OverloadedError, anthropic.APIConnectionError):
        assert cls in API_FAILURES or any(issubclass(cls, base) for base in API_FAILURES)


# ------------------------------------------------------------------- wiring
async def test_settings_read_openrouter_env(monkeypatch):
    monkeypatch.setenv("BIOLIFE_OPENROUTER_API_KEY", "or-abc")
    s = Settings()
    assert s.openrouter_api_key.get_secret_value() == "or-abc"
    assert s.openrouter_fallback_model == "nvidia/nemotron-3-ultra:free"


async def test_build_llm_wraps_only_when_configured(monkeypatch):
    monkeypatch.delenv("BIOLIFE_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    plain = build_llm(Settings(llm_provider="fake", openrouter_api_key=None))
    assert not isinstance(plain, FallbackLLMClient)

    hybrid = build_llm(Settings(llm_provider="fake", openrouter_api_key=SecretStr("or-abc")))
    assert isinstance(hybrid, FallbackLLMClient) and hybrid.fallback.provider == "openrouter"
    assert build_fallback_client(Settings(openrouter_api_key=None)) is None


# ============================ review findings: regressions ============================
async def test_slow_primary_hands_over_to_the_fallback_in_time():
    """Regression: the primary could eat the whole budget, so the fallback never ran."""
    import asyncio

    class Hanging(Primary):
        async def generate_text(self, **kw):
            await asyncio.sleep(5)
            return "too late"

    client = FallbackLLMClient(Hanging(), router(lambda r: httpx.Response(200, json=OK_BODY)),
                               primary_timeout_s=0.1)
    async with asyncio.timeout(2):                     # the handler's budget, scaled down
        result = await client.generate(system="s", user="u")
    assert result.degraded and result.provider == "openrouter"


async def test_primary_timeout_without_a_fallback_is_reported():
    import asyncio

    class Hanging(Primary):
        async def generate_text(self, **kw):
            await asyncio.sleep(5)

    with pytest.raises(LLMError, match="timed out"):
        await FallbackLLMClient(Hanging(), None, primary_timeout_s=0.1).generate(system="s", user="u")


async def test_default_budgets_leave_room_for_the_fallback():
    s = Settings()
    from app.telegram.handlers import GENERATION_TIMEOUT_S
    assert s.llm_primary_timeout_s + s.openrouter_timeout_s < GENERATION_TIMEOUT_S


@pytest.mark.parametrize("error", [
    anthropic.OverloadedError("529", response=httpx.Response(529, request=make_request()), body=None),
    anthropic.APITimeoutError(request=make_request()),
])
async def test_overloaded_and_timeout_errors_also_switch(error):
    client = FallbackLLMClient(Primary(error=error), router(lambda r: httpx.Response(200, json=OK_BODY)))
    assert (await client.generate(system="s", user="u")).degraded is True


async def test_closed_client_is_not_resurrected():
    """Regression: aclose() used to be undone by the next call, leaking a new pool."""
    client = router(lambda r: httpx.Response(200, json=OK_BODY))
    assert await client.generate_text(system="s", user="u")
    await client.aclose()
    with pytest.raises(LLMError, match="closed"):
        await client.generate_text(system="s", user="u")


async def test_fallback_client_aclose_closes_both_sides():
    closed: list[str] = []

    class Closable(Primary):
        async def aclose(self):
            closed.append("primary")

    openrouter = router(lambda r: httpx.Response(200, json=OK_BODY))
    await FallbackLLMClient(Closable(), openrouter).aclose()
    assert closed == ["primary"] and openrouter._closed is True


@pytest.mark.parametrize("content,expected", [
    ([{"type": "text", "text": "qism bir"}, {"type": "text", "text": "qism ikki"}], "qism bir\nqism ikki"),
    ("oddiy matn", "oddiy matn"),
])
async def test_content_parts_are_supported(content, expected):
    body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    assert await router(lambda r: httpx.Response(200, json=body)).generate_text(
        system="s", user="u") == expected


async def test_none_content_is_an_error_not_a_crash():
    body = {"choices": [{"message": {"content": None}}]}
    with pytest.raises(LLMError, match="empty content"):
        await router(lambda r: httpx.Response(200, json=body)).generate_text(system="s", user="u")


async def test_startup_model_check_warns_on_a_stale_slug(caplog):
    import logging
    client = router(lambda r: httpx.Response(200, json={"data": [{"id": "qwen/qwen3.8-27b:free"}]}))
    with caplog.at_level(logging.ERROR, logger="biolife.llm.openrouter"):
        await client.warn_if_model_unknown()
    assert "does not list the fallback model" in caplog.text


async def test_api_key_never_appears_in_error_messages():
    client = router(lambda r: httpx.Response(401, json={"error": {"message": "invalid"}}))
    with pytest.raises(LLMError) as exc:
        await client.generate_text(system="s", user="u")
    assert "or-key" not in str(exc.value)
