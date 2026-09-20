"""Verify SDK integration shape without network: schema compatibility + call arguments."""
import types

import pytest

from app.llm.anthropic_client import AnthropicClient
from app.llm.base import LLMError, LLMRefusal
from app.llm.fake_client import default_payload
from app.models.script_package import ReelScriptLLM


def test_schema_accepted_by_anthropic_transform():
    from anthropic import transform_schema
    s = transform_schema(ReelScriptLLM)
    assert s["type"] == "object"


def test_schema_accepted_by_openai_strict():
    from openai.lib._pydantic import to_strict_json_schema
    s = to_strict_json_schema(ReelScriptLLM)
    assert s["additionalProperties"] is False
    assert set(s["required"]) == {"strategic_selection", "visual_hook", "script", "call_to_action"}


def _resp(stop_reason, text):
    return types.SimpleNamespace(stop_reason=stop_reason,
                                 content=[types.SimpleNamespace(type="text", text=text)])


def _client_with(resp):
    c = AnthropicClient("claude-sonnet-5", 4000, 30, api_key="test")
    captured = {}

    async def create(**kw):
        captured.update(kw)
        return resp
    c._client.messages.create = create
    return c, captured


@pytest.mark.asyncio
async def test_anthropic_call_shape_and_parse():
    payload = ReelScriptLLM.model_validate(default_payload("", ""))
    c, kw = _client_with(_resp("end_turn", payload.model_dump_json()))
    out = await c.generate_structured(system="SYS", user="USR", schema=ReelScriptLLM)
    assert out == payload
    assert kw["system"] == "SYS" and kw["model"] == "claude-sonnet-5"
    assert kw["messages"] == [{"role": "user", "content": "USR"}]
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and "strategic_selection" in fmt["schema"]["properties"]


@pytest.mark.asyncio
async def test_anthropic_refusal_detected_even_with_non_json_text():
    c, _ = _client_with(_resp("refusal", "I can't help with that."))
    with pytest.raises(LLMRefusal):
        await c.generate_structured(system="", user="", schema=ReelScriptLLM)


@pytest.mark.asyncio
async def test_anthropic_truncation_and_bad_json():
    c, _ = _client_with(_resp("max_tokens", '{"strategic_sel'))
    with pytest.raises(LLMError, match="truncated"):
        await c.generate_structured(system="", user="", schema=ReelScriptLLM)
    c, _ = _client_with(_resp("end_turn", '{"oops": 1}'))
    with pytest.raises(LLMError, match="schema validation"):
        await c.generate_structured(system="", user="", schema=ReelScriptLLM)
