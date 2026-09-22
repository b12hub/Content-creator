from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Provider failed, timed out, or returned unparsable output."""


class LLMRefusal(LLMError):
    """Model refused (safety). Not retried."""


class MissingApiKey(LLMError):
    """The provider has no usable credentials. Raised at construction, surfaced as a clear 502."""


class LLMClient(Protocol):
    provider: str
    model: str

    async def generate_structured(self, *, system: str, user: str, schema: type[T]) -> T: ...

    async def generate_text(self, *, system: str, user: str, model: str | None = None,
                            max_tokens: int | None = None) -> str:
        """Plain-text completion for the Telegram copywriter.

        Kept separate from generate_structured on purpose: structured outputs need a recent model
        (Sonnet 4.5+/Opus 4.5+/Haiku 4.5), while free-form text works on any model, including
        claude-3-5-sonnet-20240620."""
        ...
