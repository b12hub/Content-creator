"""FastAPI webhook endpoint: secret verification, payload validation, background processing."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import get_settings
from app.dependencies import settings_dep
from app.main import app
from tests.telegram_factories import make_bot, message_update

SECRET = "test-secret-token"
PATH = get_settings().telegram_webhook_path


@pytest.fixture
def webhook_client(tg_dispatcher):
    settings = get_settings().model_copy(update={"telegram_webhook_secret": SecretStr(SECRET),
                                                 "telegram_min_interval_s": 0.0})
    app.dependency_overrides[settings_dep] = lambda: settings
    bot, session = make_bot()
    dp = tg_dispatcher
    with TestClient(app) as client:
        app.state.telegram_bot = bot
        app.state.telegram_dispatcher = dp
        yield client, session, dp
    app.dependency_overrides.clear()
    app.state.telegram_bot = None
    app.state.telegram_dispatcher = None


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_valid_update_is_accepted_and_processed(webhook_client):
    client, session, dp = webhook_client
    r = client.post(PATH, json=message_update("/create"), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 200
    assert wait_for(lambda: session.method_names()[:1] == ["SendMessage"]), session.method_names()


def test_wrong_secret_is_403(webhook_client):
    client, session, _ = webhook_client
    r = client.post(PATH, json=message_update("/create"), headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert r.status_code == 403
    assert session.calls == []


def test_missing_secret_header_is_403(webhook_client):
    client, session, _ = webhook_client
    assert client.post(PATH, json=message_update("/create")).status_code == 403
    assert session.calls == []


def test_malformed_update_is_400(webhook_client):
    client, session, _ = webhook_client
    r = client.post(PATH, json={"not_an_update": True}, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 400
    assert session.calls == []


def test_non_json_body_is_400(webhook_client):
    client, _, _ = webhook_client
    r = client.post(PATH, content=b"<xml/>", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET,
                                                      "content-type": "application/json"})
    assert r.status_code == 400


def test_returns_503_when_bot_not_configured(webhook_client):
    client, _, _ = webhook_client
    app.state.telegram_bot = None
    r = client.post(PATH, json=message_update("/create"), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 503


def test_webhook_is_hidden_from_openapi(webhook_client):
    client, _, _ = webhook_client
    assert PATH not in client.get("/openapi.json").json()["paths"]


def test_other_endpoints_unaffected(webhook_client):
    client, _, _ = webhook_client
    assert client.get("/health").json()["status"] == "ok"


def test_non_ascii_secret_header_is_403_not_500(webhook_client):
    """hmac.compare_digest raises TypeError on non-ASCII str - headers are attacker-controlled."""
    client, session, _ = webhook_client
    r = client.post(PATH, json=message_update("/create"),
                    headers={"X-Telegram-Bot-Api-Secret-Token": "sécret-ключ".encode("utf-8")})
    assert r.status_code == 403
    assert session.calls == []


def test_drain_background_waits_for_tasks():
    import asyncio

    from app.routers import telegram as tg

    async def scenario() -> list[str]:
        done: list[str] = []

        async def work():
            await asyncio.sleep(0.2)
            done.append("finished")

        task = asyncio.create_task(work())
        tg._background.add(task)
        task.add_done_callback(tg._background.discard)
        await tg.drain_background(timeout=5)
        return done

    assert asyncio.run(scenario()) == ["finished"]
