import json
import os
from pathlib import Path

import pytest

os.environ["BIOLIFE_LLM_PROVIDER"] = "fake"

from fastapi.testclient import TestClient  # noqa: E402

from app.dependencies import get_llm_client  # noqa: E402
from app.llm.fake_client import FakeLLMClient, default_payload  # noqa: E402
from app.main import app  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def make_client():
    def _make(responses=None):
        fake = FakeLLMClient(responses)
        app.dependency_overrides[get_llm_client] = lambda: fake
        return TestClient(app), fake
    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def good():
    return default_payload("", "")


@pytest.fixture(scope="session")
def tg_dispatcher():
    """One Dispatcher for the whole test session: aiogram attaches a router to one dispatcher only.
    Throttling is disabled so tests can feed updates back to back."""
    from app.config import get_settings
    from app.telegram.bot import build_dispatcher
    return build_dispatcher(get_settings().model_copy(update={"telegram_min_interval_s": 0.0}))
