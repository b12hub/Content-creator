"""OpenAI structured outputs: client.responses.parse(text_format=PydanticModel) -> response.output_parsed.
Docs: https://developers.openai.com/api/docs/guides/structured-outputs"""
from __future__ import annotations

from typing import TypeVar

import openai
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMError, MissingApiKey

T = TypeVar("T", bound=BaseModel)


class OpenAIClient:
    provider = "openai"

    def __init__(self, model: str, max_tokens: int, timeout_s: float, api_key: str):
        if not model:
            raise ValueError("Set BIOLIFE_OPENAI_MODEL when BIOLIFE_LLM_PROVIDER=openai")
        if not api_key or not api_key.strip() or api_key.strip().endswith("..."):
            raise MissingApiKey("OPENAI_API_KEY is missing or is still the placeholder.")
        api_key = api_key.strip()
        self.model = model
        self.max_tokens = max_tokens
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout_s, max_retries=2)

    async def generate_structured(self, *, system: str, user: str, schema: type[T]) -> T:
        try:
            resp = await self._client.responses.parse(
                model=self.model,
                instructions=system,
                input=user,
                text_format=schema,
                max_output_tokens=self.max_tokens,
            )
        except ValidationError as e:
            raise LLMError(f"OpenAI output failed schema validation: {e}") from e
        except openai.OpenAIError as e:
            raise LLMError(f"OpenAI API error: {e}") from e
        if resp.output_parsed is None:
            raise LLMError("OpenAI returned no parsed output (possible refusal or truncation)")
        return resp.output_parsed

    async def aclose(self) -> None:
        await self._client.close()
