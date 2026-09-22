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

from app.llm.base import LLMError, LLMRefusal, MissingApiKey

T = TypeVar("T", bound=BaseModel)


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, model: str, max_tokens: int, timeout_s: float, api_key: str):
        """The key is passed in explicitly. It is NOT left to the SDK's env lookup: the app reads
        .env through pydantic-settings, which never copies values into os.environ, so the SDK would
        find nothing and fail at request time with
        'Could not resolve authentication method...' (a TypeError, i.e. a 500)."""
        if not api_key or not api_key.strip():
            raise MissingApiKey("ANTHROPIC_API_KEY is empty. Put it in .env (no BIOLIFE_ prefix) "
                                "or export it before starting uvicorn.")
        if api_key.strip().endswith("...") or api_key.strip() in {"sk-ant-...", "sk-ant-"}:
            raise MissingApiKey(f"ANTHROPIC_API_KEY still holds the placeholder {api_key.strip()!r} - "
                                "replace it with the real key from console.anthropic.com.")
        self.model = model
        self.max_tokens = max_tokens
        self._schemas: dict[type, dict] = {}
        self._client = anthropic.AsyncAnthropic(api_key=api_key.strip(), timeout=timeout_s, max_retries=2)

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
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY in .env "
                           f"and that the workspace has credit: {e}") from e
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

    async def generate_text(self, *, system: str, user: str, model: str | None = None,
                            max_tokens: int | None = None) -> str:
        """Free-form text (no JSON schema), used by the Telegram copywriter."""
        try:
            resp = await self._client.messages.create(
                model=model or self.model,
                max_tokens=max_tokens or self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY "
                           f"and the workspace balance: {e}") from e
        except anthropic.APIError as e:
            raise LLMError(f"Anthropic API error: {e}") from e

        if resp.stop_reason == "refusal":
            raise LLMRefusal("Model refused to answer this prompt")
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if not text:
            raise LLMError("Anthropic returned an empty answer")
        if resp.stop_reason == "max_tokens":
            text += "\n\n[…]"          # truncated: tell the reader instead of pretending it is complete
        return text

    async def aclose(self) -> None:
        await self._client.close()
