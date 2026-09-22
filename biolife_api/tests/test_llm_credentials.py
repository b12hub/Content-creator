"""Regression: the Anthropic SDK raised 'Could not resolve authentication method' because the key
was never passed in — pydantic-settings reads .env but does not populate os.environ."""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.dependencies import BrokenLLMClient, build_llm_client
from app.llm.base import MissingApiKey
from app.models.script_package import ReelScriptLLM


def test_settings_read_unprefixed_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-looking-key")
    assert Settings().anthropic_api_key.get_secret_value() == "sk-ant-real-looking-key"


def test_client_gets_the_key_and_does_not_rely_on_os_environ(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(llm_provider="anthropic", anthropic_api_key=SecretStr("sk-ant-real-looking-key"))
    client = build_llm_client(s)
    assert client.provider == "anthropic"
    assert client._client.api_key == "sk-ant-real-looking-key"      # reached the SDK


def test_missing_key_yields_a_clear_error_not_a_typeerror(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = build_llm_client(Settings(llm_provider="anthropic", anthropic_api_key=None))
    assert isinstance(client, BrokenLLMClient)


def test_placeholder_key_is_refused(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = build_llm_client(Settings(llm_provider="anthropic",
                                       anthropic_api_key=SecretStr("sk-ant-...")))
    assert isinstance(client, BrokenLLMClient) and "placeholder" in client._reason


@pytest.mark.asyncio
async def test_broken_client_raises_missing_api_key():
    client = BrokenLLMClient("anthropic", "ANTHROPIC_API_KEY is empty.")
    with pytest.raises(MissingApiKey, match="empty"):
        await client.generate_structured(system="s", user="u", schema=ReelScriptLLM)


def test_llm_endpoint_answers_502_with_the_reason(monkeypatch):
    """No more 500 TypeError from inside the SDK."""
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.dependencies import get_llm_client
    from app.main import app

    app.dependency_overrides[get_llm_client] = lambda: BrokenLLMClient(
        "anthropic", "ANTHROPIC_API_KEY is empty. Put it in .env (no BIOLIFE_ prefix).")
    payload = json.loads((Path(__file__).parent / "fixtures" / "chilla_43C.json").read_text())
    with TestClient(app) as client:
        r = client.post("/generate-reel-script", json=payload)
    app.dependency_overrides.clear()
    assert r.status_code == 502
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]["detail"]
