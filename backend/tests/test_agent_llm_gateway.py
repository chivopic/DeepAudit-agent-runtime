"""Real-LLM wiring for the graph path: adapter, tolerant parsing, default-off flag.

Deterministic only — no network and no paid model. The adapter is exercised
against a stub LLMService; the real-model run is verified out of band.
"""

from __future__ import annotations

import pytest

from app.services.agent.graph import FakeLLM, LLMGateway, LLMServiceGateway
from app.services.agent.graph.llm import LLMMessage
from app.services.agent.graph.nodes import _parse_llm_findings


class StubLLMService:
    """Minimal stand-in for ``app.services.llm.LLMService``."""

    def __init__(self, content: str = "ok", usage: dict | None = None) -> None:
        self.content = content
        self.usage = usage if usage is not None else {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
        }
        self.seen: list[dict] = []
        self.kwargs: dict = {}

    async def chat_completion_raw(self, messages, temperature=None, max_tokens=None):
        self.seen = messages
        self.kwargs = {"temperature": temperature, "max_tokens": max_tokens}
        return {"content": self.content, "usage": self.usage}


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def test_adapter_satisfies_gateway_protocol():
    assert isinstance(LLMServiceGateway(StubLLMService()), LLMGateway)


@pytest.mark.asyncio
async def test_adapter_maps_messages_and_usage():
    stub = StubLLMService(content="hello")
    gw = LLMServiceGateway(stub)

    resp = await gw.complete(
        [
            LLMMessage(role="system", content="s"),
            LLMMessage(role="user", content="u"),
        ],
        temperature=0.3,
        max_tokens=128,
    )

    # graph LLMMessage -> plain dicts for the legacy raw interface
    assert stub.seen == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert stub.kwargs == {"temperature": 0.3, "max_tokens": 128}

    assert resp.content == "hello"
    assert resp.usage.input_tokens == 7
    assert resp.usage.output_tokens == 3
    assert resp.usage.total_tokens == 10
    # one call per complete(), so budget accounting stays honest
    assert resp.usage.call_count == 1


@pytest.mark.asyncio
async def test_adapter_tolerates_missing_usage_and_content():
    class Bare:
        async def chat_completion_raw(self, messages, temperature=None, max_tokens=None):
            return {}

    resp = await LLMServiceGateway(Bare()).complete([LLMMessage(role="user", content="x")])
    assert resp.content == ""
    assert resp.usage.total_tokens == 0


@pytest.mark.asyncio
async def test_adapter_usage_labels_survive_broken_config():
    """A failing ``config`` property must not break a run — labels are optional."""

    class Exploding(StubLLMService):
        @property
        def config(self):
            raise RuntimeError("no config")

    resp = await LLMServiceGateway(Exploding()).complete(
        [LLMMessage(role="user", content="x")]
    )
    assert resp.usage.total_tokens == 10
    assert resp.usage.provider is None


# ---------------------------------------------------------------------------
# Tolerant findings parsing
# ---------------------------------------------------------------------------


def test_parse_findings_accepts_fenced_json():
    """Real models fence their JSON; without this the findings are dropped."""
    raw = (
        "Here are the issues:\n"
        "```json\n"
        '[{"title": "SQLi", "severity": "high", "line": 3}]\n'
        "```\n"
    )
    assert not raw.strip().startswith("[")  # the old guard would have skipped it
    assert _parse_llm_findings(raw) == [
        {"title": "SQLi", "severity": "high", "line": 3}
    ]


def test_parse_findings_accepts_bare_array():
    assert _parse_llm_findings('[{"title": "X"}]') == [{"title": "X"}]


@pytest.mark.parametrize("key", ["findings", "results", "issues", "vulnerabilities"])
def test_parse_findings_unwraps_object(key):
    assert _parse_llm_findings(f'{{"{key}": [{{"title": "Y"}}]}}') == [{"title": "Y"}]


@pytest.mark.parametrize("raw", ["", "   ", "No issues found.", "null"])
def test_parse_findings_returns_empty_for_non_findings(raw):
    assert _parse_llm_findings(raw) == []


def test_parse_findings_drops_non_dict_items():
    assert _parse_llm_findings('["nope", {"title": "Z"}, 42]') == [{"title": "Z"}]


def test_parse_findings_handles_none():
    assert _parse_llm_findings(None) == []


# ---------------------------------------------------------------------------
# Default-off flag
# ---------------------------------------------------------------------------


class FakeUser:
    id = "u1"


@pytest.mark.asyncio
async def test_graph_audits_defaults_to_fake_llm():
    """Paid APIs stay unreachable from this surface unless explicitly enabled."""
    from app.api.v1.endpoints import graph_audits as ga

    assert ga.settings.GRAPH_AUDITS_USE_REAL_LLM is False

    llm, offline = await ga._resolve_llm(db=None, user=FakeUser())
    assert isinstance(llm, FakeLLM)
    assert offline is True


@pytest.mark.asyncio
async def test_graph_audits_uses_real_gateway_when_enabled(monkeypatch):
    from app.api.v1.endpoints import agent_tasks as at
    from app.api.v1.endpoints import graph_audits as ga

    monkeypatch.setattr(ga.settings, "GRAPH_AUDITS_USE_REAL_LLM", True, raising=False)

    async def fake_get_user_config(db, user_id):
        assert user_id == "u1"  # the caller's own config, not a shared one
        return {"llmConfig": {"llmProvider": "deepseek"}}

    monkeypatch.setattr(at, "_get_user_config", fake_get_user_config)

    llm, offline = await ga._resolve_llm(db=None, user=FakeUser())
    assert isinstance(llm, LLMServiceGateway)
    assert offline is False
