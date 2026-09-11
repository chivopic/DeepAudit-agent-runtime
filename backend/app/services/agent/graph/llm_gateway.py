"""Adapter exposing the production ``LLMService`` as a graph ``LLMGateway``.

The graph nodes depend only on the :class:`LLMGateway` protocol, so this is
the single place where the LangGraph path meets the real model gateway.
Credentials are never held here: ``LLMService`` resolves them per user from
the stored config (falling back to environment settings), exactly as the
production ReAct path does.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.agent.domain import ModelUsage
from app.services.agent.graph.llm import LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class LLMServiceGateway:
    """Implements ``LLMGateway`` on top of ``app.services.llm.LLMService``.

    Parameters
    ----------
    llm_service:
        A configured ``LLMService``. Injected rather than constructed here so
        tests can pass a stub and callers stay in control of user config.
    """

    def __init__(self, llm_service: Any) -> None:
        self._llm = llm_service

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict[str, Any]] = None,
    ) -> LLMResponse:
        # ``model`` / ``response_format`` are part of the protocol but the
        # legacy raw interface resolves the model from user config and has no
        # structured-output switch, so they are accepted and ignored here.
        payload = [{"role": m.role, "content": m.content} for m in messages]
        raw = await self._llm.chat_completion_raw(
            payload,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content = str((raw or {}).get("content") or "")
        usage_raw = (raw or {}).get("usage") or {}
        provider, resolved_model = self._describe_config()

        usage = ModelUsage(
            provider=provider,
            model=model or resolved_model,
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
            total_tokens=int(usage_raw.get("total_tokens") or 0),
            call_count=1,
        )
        return LLMResponse(
            content=content,
            usage=usage,
            raw=raw if isinstance(raw, dict) else {},
            model=usage.model,
            provider=provider,
        )

    def _describe_config(self) -> tuple[Optional[str], Optional[str]]:
        """Best-effort provider/model labels for usage accounting."""
        try:
            cfg = self._llm.config
        except Exception as exc:  # noqa: BLE001 — labels must never fail a run
            logger.debug("llm config unavailable for usage labels: %s", exc)
            return None, None

        provider = getattr(cfg, "provider", None)
        provider = getattr(provider, "value", provider)
        return (
            str(provider) if provider is not None else None,
            getattr(cfg, "model", None),
        )
