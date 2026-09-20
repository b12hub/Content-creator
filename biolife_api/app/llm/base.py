from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Provider failed, timed out, or returned unparsable output."""


class LLMRefusal(LLMError):
    """Model refused (safety). Not retried."""


class LLMClient(Protocol):
    provider: str
    model: str

    async def generate_structured(self, *, system: str, user: str, schema: type[T]) -> T: ...
