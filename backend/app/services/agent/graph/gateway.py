"""Graph LLMGateway adapter over the existing LLMService.

Nodes keep depending on ``LLMGateway``. This adapter translates that call
into ``LLMService.chat_completion_raw`` and copies token usage. It never
puts the API key on the response or in log messages.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.agent.domain import ModelUsage
from app.services.agent.graph.llm import LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class LLMServiceGateway:
    """Production gateway. ``service`` is an object with ``chat_completion_raw``."""

    def __init__(
        self,
        service: Any,
        *,
        provider: str,
        model: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._service = service
        self.provider = provider
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        payload = [{"role": m.role, "content": m.content} for m in messages]
        if response_format:
            payload.append(
                {
                    "role": "system",
                    "content": "Respond with a single JSON value and no markdown.",
                }
            )
        try:
            raw = await asyncio.wait_for(
                self._service.chat_completion_raw(
                    payload,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise TimeoutError("model gateway timed out") from exc

        if not isinstance(raw, dict):
            raise RuntimeError("model gateway returned a non-object payload")
        usage_value = raw.get("usage")
        usage_raw: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
        prompt = int(usage_raw.get("prompt_tokens") or 0)
        completion = int(usage_raw.get("completion_tokens") or 0)
        total = int(usage_raw.get("total_tokens") or (prompt + completion))
        chosen_model = model or self.model
        return LLMResponse(
            content=str(raw.get("content") or ""),
            usage=ModelUsage(
                provider=self.provider,
                model=chosen_model,
                input_tokens=max(0, prompt),
                output_tokens=max(0, completion),
                total_tokens=max(0, total),
                call_count=1,
            ),
            model=chosen_model,
            provider=self.provider,
            raw={"has_usage": bool(usage_raw)},
        )
