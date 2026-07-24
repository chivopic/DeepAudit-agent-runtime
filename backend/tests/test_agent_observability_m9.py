"""M9 observability façade tests."""

from __future__ import annotations

import pytest

from app.services.agent.observability import (
    REDACTED,
    SPAN_AUDIT_RUN,
    SPAN_GRAPH_NODE,
    SPAN_LLM_CALL,
    SPAN_TOOL_CALL,
    Tracer,
    get_tracer,
    redact_attributes,
    redact_value,
    set_tracer,
    trace_node,
)


def test_redact_sensitive_keys():
    attrs = redact_attributes(
        {
            "audit.id": "a1",
            "api_key": "sk-secret-value",
            "Authorization": "Bearer abc",
            "nested": {"password": "p", "ok": 1},
        }
    )
    assert attrs["audit.id"] == "a1"
    assert attrs["api_key"] == REDACTED
    assert attrs["Authorization"] == REDACTED
    assert attrs["nested"]["password"] == REDACTED
    assert attrs["nested"]["ok"] == 1


def test_redact_value_patterns():
    text = "token=abc123 and Bearer eyJhbGciOi and sk-abcdefghijklmnop"
    red = redact_value(text)
    assert "abc123" not in red or "***" in red
    assert "sk-abcdefghijklmnop" not in red
    assert "Bearer" not in red or "***" in red


def test_span_nesting_and_metrics():
    t = Tracer(service_name="test")
    with t.span(SPAN_AUDIT_RUN, **{"audit.id": "x", "api_key": "secret"}):
        with t.span(f"{SPAN_GRAPH_NODE}.analyze", **{"node.name": "analyze"}):
            with t.span(SPAN_LLM_CALL, **{"model.name": "fake"}):
                pass
            with t.span(SPAN_TOOL_CALL, **{"tool.name": "read_snippet"}):
                t.counter("tool.calls", 1, **{"tool.name": "read_snippet"})
            t.histogram("llm.latency_ms", 12.5)

    flat = t.get_spans_flat()
    names = [s.name for s in flat]
    assert SPAN_AUDIT_RUN in names
    assert f"{SPAN_GRAPH_NODE}.analyze" in names
    assert SPAN_LLM_CALL in names
    assert SPAN_TOOL_CALL in names
    root = t.spans[0]
    assert root.attributes.get("api_key") == REDACTED
    assert root.status == "ok"
    assert root.duration_ms >= 0
    assert any(m.name == "tool.calls" for m in t.metrics)
    assert any(m.name == "llm.latency_ms" for m in t.metrics)


def test_span_records_error_status():
    t = Tracer()
    with pytest.raises(RuntimeError):
        with t.span("boom"):
            raise RuntimeError("fail-me")
    assert t.spans[0].status == "error"
    assert "fail-me" in (t.spans[0].error_message or "")


def test_global_tracer_and_trace_node():
    t = Tracer()
    set_tracer(t)
    assert get_tracer() is t
    with trace_node("plan_audit", **{"audit.id": "z"}):
        pass
    assert any(s.name.endswith("plan_audit") for s in t.get_spans_flat())
    t.reset()
    assert t.spans == []
    assert t.metrics == []


def test_long_string_truncated():
    huge = "a" * 5000
    red = redact_value(huge)
    assert len(red) < 3000
    assert "redacted-long" in red
