"""Observability façade (M9): tracing, metrics, redaction.

Does not require OpenTelemetry at runtime — provides a compatible API that
records spans in-memory for tests, and optionally emits OTEL if installed.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator, Iterator, Optional

logger = logging.getLogger(__name__)

# Default deny: never log these attribute keys or patterns
_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "token",
    "cookie",
    "private_key",
    "credential",
)

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^\s'\"]+"),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
]


def redact_value(value: Any) -> Any:
    """Redact secrets from attribute values."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: (REDACTED if _is_sensitive_key(k) else redact_value(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        text = value
        for pat in _SECRET_PATTERNS:
            text = pat.sub("***", text)
        if len(text) > 2000:
            text = text[:2000] + "…[redacted-long]"
        return text
    return value


REDACTED = "***REDACTED***"


def _is_sensitive_key(key: str) -> bool:
    k = key.lower().replace("-", "_")
    return any(p in k for p in _SENSITIVE_KEY_PARTS)


def redact_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if _is_sensitive_key(k):
            out[k] = REDACTED
        else:
            out[k] = redact_value(v)
    return out


@dataclass
class SpanRecord:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok | error
    error_message: Optional[str] = None
    start_ms: float = 0.0
    end_ms: float = 0.0
    children: list[SpanRecord] = field(default_factory=list)
    parent: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        if self.end_ms and self.start_ms:
            return max(0.0, self.end_ms - self.start_ms)
        return 0.0


@dataclass
class MetricPoint:
    name: str
    value: float
    attributes: dict[str, Any] = field(default_factory=dict)
    ts_ms: float = field(default_factory=lambda: time.time() * 1000)


class Tracer:
    """In-process span collector with optional OTEL bridge."""

    def __init__(self, *, service_name: str = "deepaudit-agent") -> None:
        self.service_name = service_name
        self.spans: list[SpanRecord] = []
        self.metrics: list[MetricPoint] = []
        self._stack: list[SpanRecord] = []
        self._otel = None
        try:
            from opentelemetry import trace  # type: ignore

            self._otel = trace.get_tracer(service_name)
        except Exception:  # noqa: BLE001
            self._otel = None

    @contextmanager
    def span(
        self,
        name: str,
        **attributes: Any,
    ) -> Generator[SpanRecord, None, None]:
        safe = redact_attributes(attributes)
        rec = SpanRecord(
            name=name,
            attributes=safe,
            start_ms=time.time() * 1000,
            parent=self._stack[-1].name if self._stack else None,
        )
        self._stack.append(rec)
        otel_cm = None
        otel_span = None
        if self._otel is not None:
            try:
                otel_cm = self._otel.start_as_current_span(name)
                otel_span = otel_cm.__enter__()
                for k, v in safe.items():
                    if v is not None and not isinstance(v, (dict, list)):
                        otel_span.set_attribute(k, v)
            except Exception:  # noqa: BLE001
                otel_cm = None
        try:
            yield rec
            rec.status = rec.status or "ok"
        except Exception as exc:  # noqa: BLE001
            rec.status = "error"
            rec.error_message = str(exc)[:500]
            raise
        finally:
            rec.end_ms = time.time() * 1000
            self._stack.pop()
            if self._stack:
                self._stack[-1].children.append(rec)
            else:
                self.spans.append(rec)
            if otel_cm is not None:
                try:
                    otel_cm.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass

    def counter(self, name: str, value: float = 1.0, **attributes: Any) -> None:
        self.metrics.append(
            MetricPoint(
                name=name,
                value=value,
                attributes=redact_attributes(attributes),
            )
        )

    def histogram(self, name: str, value: float, **attributes: Any) -> None:
        self.counter(name, value, **attributes)

    def get_spans_flat(self) -> list[SpanRecord]:
        out: list[SpanRecord] = []

        def walk(s: SpanRecord) -> None:
            out.append(s)
            for c in s.children:
                walk(c)

        for s in self.spans:
            walk(s)
        return out

    def reset(self) -> None:
        self.spans.clear()
        self.metrics.clear()
        self._stack.clear()


# Recommended span names (target architecture)
SPAN_AUDIT_RUN = "audit.run"
SPAN_GRAPH_NODE = "graph.node"
SPAN_CONTEXT_BUILD = "context.build"
SPAN_CONTEXT_COMPACT = "context.compact"
SPAN_LLM_CALL = "llm.call"
SPAN_TOOL_CALL = "tool.call"
SPAN_SANDBOX = "sandbox.execute"
SPAN_VERIFY = "verification.run"
SPAN_REPORT = "report.generate"


_global_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = Tracer()
    return _global_tracer


def set_tracer(tracer: Tracer) -> None:
    global _global_tracer
    _global_tracer = tracer


def trace_node(node_name: str, **attrs: Any):
    """Context manager helper for graph nodes."""
    return get_tracer().span(
        f"{SPAN_GRAPH_NODE}.{node_name}",
        **{"node.name": node_name, **attrs},
    )
