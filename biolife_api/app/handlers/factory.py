"""Deterministic routing: framework.name from the DB → exactly one handler class."""
from __future__ import annotations

from app.config import Settings
from app.handlers.base import BaseReelHandler
from app.handlers.dopamine import DopamineHandler
from app.handlers.emotional import EmotionalHandler
from app.handlers.hybrid import HybridHandler
from app.llm.base import LLMClient
from app.models.context import FrameworkName

HANDLER_REGISTRY: dict[FrameworkName, type[BaseReelHandler]] = {
    FrameworkName.COCA_COLA_EMOTIONAL: EmotionalHandler,
    FrameworkName.PEPSI_BEHAVIORAL_DOPAMINE: DopamineHandler,
    FrameworkName.HYBRID_SPRING_RENEWAL: HybridHandler,
}

# fail at import time if a new enum value is added without a handler
_missing = set(FrameworkName) - set(HANDLER_REGISTRY)
if _missing:  # pragma: no cover
    raise RuntimeError(f"No handler registered for frameworks: {_missing}")


class UnknownFrameworkError(ValueError):
    pass


def get_handler(framework: FrameworkName, llm: LLMClient, settings: Settings) -> BaseReelHandler:
    try:
        return HANDLER_REGISTRY[framework](llm, settings)
    except KeyError as e:  # pragma: no cover - guarded above
        raise UnknownFrameworkError(framework) from e
