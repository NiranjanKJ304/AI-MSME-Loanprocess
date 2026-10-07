"""LLM provider abstraction.

The rest of the system only calls `LLMProvider.complete_json(system, user, schema)`,
which must return an instance of the given Pydantic model (validated). Providers are
selected by LLM_PROVIDER; with "none" every LLM-dependent step is skipped and the
deterministic pipeline result stands on its own.

Rules enforced by callers (not by providers):
  * LLM output is only used where deterministic rules failed (fallback).
  * Every LLM-extracted value must be found verbatim in the source text, else rejected.
  * LLM-derived values get capped confidence and are routed to human review.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger("llm")

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema adapted for strict structured output:
    every object gets additionalProperties=false and all properties required
    (optional fields remain nullable via anyOf[..., null])."""
    schema = model.model_json_schema()

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            # strip metadata keys (but not properties that happen to be named "title"/"default")
            for key in ("title", "default"):
                if key in node and not isinstance(node[key], dict):
                    del node[key]
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema


class LLMProvider(ABC):
    name = "abstract"

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def _complete_raw(self, system: str, user: str, schema: dict[str, Any]) -> str:
        """Return the model's JSON text for the given schema."""

    def complete_json(self, system: str, user: str, model: type[T], retries: int = 1) -> T:
        if not self.available():
            raise LLMUnavailable(f"LLM provider '{self.name}' not available")
        schema = strict_json_schema(model)
        last_err: Exception | None = None
        for _ in range(retries + 1):
            text = self._complete_raw(system, user, schema)
            try:
                return model.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_err = exc
                log.warning("LLM output failed schema validation", extra={"ctx": {"error": str(exc)}})
        raise LLMResponseError(f"LLM output did not conform to schema: {last_err}")


class NullProvider(LLMProvider):
    name = "none"

    def available(self) -> bool:
        return False

    def _complete_raw(self, system: str, user: str, schema: dict[str, Any]) -> str:
        raise LLMUnavailable("LLM_PROVIDER=none")


class AnthropicProvider(LLMProvider):
    """Claude via the official Anthropic SDK, using structured outputs (JSON schema)."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float):
        self.model = model
        self._client = None
        try:
            import anthropic

            # api_key=None lets the SDK resolve credentials from its environment/profile.
            self._client = anthropic.Anthropic(api_key=api_key or None, timeout=timeout)
        except Exception as exc:  # SDK missing or no credentials
            log.warning("Anthropic client unavailable", extra={"ctx": {"error": str(exc)}})

    def available(self) -> bool:
        return self._client is not None

    def _complete_raw(self, system: str, user: str, schema: dict[str, Any]) -> str:
        assert self._client is not None
        # Server-side refusal fallback ("default" routing) is enabled; the response is still
        # schema-validated by complete_json before anything uses it.
        response = self._client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMResponseError("LLM declined the request (refusal)")
        if response.stop_reason == "max_tokens":
            raise LLMResponseError("LLM output truncated (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMResponseError("LLM returned no text block")
        return text


_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        name = s.llm_provider.lower().strip()
        if name == "anthropic":
            _provider = AnthropicProvider(s.llm_api_key, s.llm_model, s.llm_timeout_seconds)
        else:
            _provider = NullProvider()
    return _provider


def set_llm_provider(provider: LLMProvider | None) -> None:
    """Override the provider (tests, or registering a different backend)."""
    global _provider
    _provider = provider
