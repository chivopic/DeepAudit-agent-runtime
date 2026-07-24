"""LLM gateway protocol + FakeLLM for deterministic graph tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from app.services.agent.domain import ModelUsage


@dataclass
class LLMMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class LLMResponse:
    content: str
    usage: ModelUsage = field(default_factory=ModelUsage)
    raw: dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    provider: Optional[str] = None


@runtime_checkable
class LLMGateway(Protocol):
    """Nodes depend on this protocol — never import LiteLLM directly."""

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict[str, Any]] = None,
    ) -> LLMResponse: ...


class FakeLLM:
    """Scripted LLM for tests and offline graph runs.

    Queue responses via ``enqueue`` / constructor ``responses``.
    If the queue is empty, returns a minimal deterministic JSON blob.
    """

    def __init__(
        self,
        responses: Optional[list[str | LLMResponse]] = None,
        *,
        default_content: str = '{"ok": true}',
        model: str = "fake-model",
        provider: str = "fake",
    ) -> None:
        self.default_content = default_content
        self.model = model
        self.provider = provider
        self.calls: list[list[LLMMessage]] = []
        self._queue: list[LLMResponse] = []
        for r in responses or []:
            self.enqueue(r)

    def enqueue(self, response: str | LLMResponse) -> None:
        if isinstance(response, str):
            response = LLMResponse(
                content=response,
                usage=ModelUsage(
                    provider=self.provider,
                    model=self.model,
                    input_tokens=10,
                    output_tokens=max(1, len(response) // 4),
                    call_count=1,
                ),
                model=self.model,
                provider=self.provider,
            )
        self._queue.append(response)

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict[str, Any]] = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if self._queue:
            return self._queue.pop(0)
        return LLMResponse(
            content=self.default_content,
            usage=ModelUsage(
                provider=self.provider,
                model=model or self.model,
                input_tokens=10,
                output_tokens=5,
                call_count=1,
            ),
            model=model or self.model,
            provider=self.provider,
        )

    def reset(self) -> None:
        self._queue.clear()
        self.calls.clear()
