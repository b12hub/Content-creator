"""Anthropic structured outputs (GA): messages.create(output_config={"format": {"type": "json_schema", ...}}).
Docs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs

We deliberately use create() + our own validation instead of messages.parse(): parse() validates the
JSON before returning, so a refusal or a max_tokens truncation surfaces as a pydantic ValidationError
and the stop_reason can never be inspected (verified in anthropic==1.7.0, lib/_parse/_response.py)."""
from __future__ import annotations

from typing import TypeVar

import anthropic
from anthropic import transform_schema
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMError, LLMRefusal

T = TypeVar("T", bound=BaseModel)


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, model: str, max_tokens: int, timeout_s: float, api_key: str | None = None):
        self.model = model
        self.max_tokens = max_tokens
        self._schemas: dict[type, dict] = {}
        # api_key=None -> SDK reads ANTHROPIC_API_KEY from env
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=2)

    def _schema_for(self, schema: type[BaseModel]) -> dict:
        if schema not in self._schemas:
            self._schemas[schema] = transform_schema(schema)   # strips unsupported constraints
        return self._schemas[schema]

    async def generate_structured(self, *, system: str, user: str, schema: type[T]) -> T:
        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": self._schema_for(schema)}},
            )
        except anthropic.APIError as e:
            raise LLMError(f"Anthropic API error: {e}") from e

        if resp.stop_reason == "refusal":
            raise LLMRefusal("Model refused to generate this script")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Output truncated (max_tokens). Increase BIOLIFE_MAX_OUTPUT_TOKENS.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        if not text:
            raise LLMError("Anthropic returned no text content")
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            raise LLMError(f"Anthropic output failed schema validation: {e}") from e

    async def aclose(self) -> None:
        await self._client.close()
