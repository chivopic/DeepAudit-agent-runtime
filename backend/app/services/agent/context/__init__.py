"""Context Manager: build / compact context packs under token budgets (M5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.agent.domain import ArtifactRef, Finding, RunBudget
from app.services.agent.persistence.artifact_store import (
    ArtifactStore,
    InMemoryArtifactStore,
)


@dataclass
class ContextLayer:
    """One layer of the context pack."""

    name: str
    text: str = ""
    token_estimate: int = 0
    pinned: bool = False
    refs: list[ArtifactRef] = field(default_factory=list)


@dataclass
class ContextPack:
    """Assembled context for a node / LLM call."""

    audit_id: str
    layers: list[ContextLayer] = field(default_factory=list)
    total_tokens_est: int = 0
    compacted: bool = False
    dropped_layers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_prompt_block(self) -> str:
        parts = []
        for layer in self.layers:
            if not layer.text:
                continue
            parts.append(f"### {layer.name}\n{layer.text}")
        return "\n\n".join(parts)

    def layer_names(self) -> list[str]:
        return [l.name for l in self.layers]


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


# Fields that must never be dropped on compaction (security / continuity)
_PINNED_LAYER_NAMES = frozenset(
    {
        "pinned",
        "findings_index",
        "user_decisions",
        "verification_results",
        "pending_tasks",
        "security_policy",
        "artifact_refs",
    }
)


class ContextManager:
    """Build and compact context packs under a token budget."""

    def __init__(
        self,
        *,
        artifacts: Optional[ArtifactStore] = None,
        default_budget_tokens: int = 8_000,
        offload_threshold_chars: int = 4_000,
    ) -> None:
        self.artifacts = artifacts or InMemoryArtifactStore()
        self.default_budget_tokens = default_budget_tokens
        self.offload_threshold_chars = offload_threshold_chars

    async def build_context(
        self,
        *,
        audit_id: str,
        pinned: Optional[dict[str, str]] = None,
        active: Optional[str] = None,
        running_summary: Optional[str] = None,
        retrieved: Optional[list[str]] = None,
        findings: Optional[list[Finding]] = None,
        pending_task_ids: Optional[list[str]] = None,
        security_policy: Optional[str] = None,
        budget: Optional[RunBudget] = None,
        max_context_tokens: Optional[int] = None,
    ) -> ContextPack:
        layers: list[ContextLayer] = []

        # Pinned: never compact away
        pin_parts = []
        for k, v in (pinned or {}).items():
            pin_parts.append(f"{k}: {v}")
        pin_text = "\n".join(pin_parts)
        layers.append(
            ContextLayer(
                name="pinned",
                text=pin_text,
                token_estimate=estimate_tokens(pin_text),
                pinned=True,
            )
        )

        if security_policy:
            layers.append(
                ContextLayer(
                    name="security_policy",
                    text=security_policy,
                    token_estimate=estimate_tokens(security_policy),
                    pinned=True,
                )
            )

        if findings:
            idx_lines = []
            for f in findings:
                loc = (
                    f"{f.location.file_path}:{f.location.start_line}"
                    if f.location
                    else "n/a"
                )
                idx_lines.append(
                    f"- [{f.severity.value}] {f.id} {f.title} @ {loc} "
                    f"conf={f.confidence:.2f} verify={f.verification_status.value}"
                )
            idx = "\n".join(idx_lines)
            layers.append(
                ContextLayer(
                    name="findings_index",
                    text=idx,
                    token_estimate=estimate_tokens(idx),
                    pinned=True,
                )
            )

        if pending_task_ids:
            pt = "pending: " + ", ".join(pending_task_ids)
            layers.append(
                ContextLayer(
                    name="pending_tasks",
                    text=pt,
                    token_estimate=estimate_tokens(pt),
                    pinned=True,
                )
            )

        if running_summary:
            layers.append(
                ContextLayer(
                    name="running_summary",
                    text=running_summary,
                    token_estimate=estimate_tokens(running_summary),
                    pinned=False,
                )
            )

        if active:
            text, refs = await self._maybe_offload(active, audit_id=audit_id)
            layers.append(
                ContextLayer(
                    name="active",
                    text=text,
                    token_estimate=estimate_tokens(text),
                    pinned=False,
                    refs=refs,
                )
            )

        for i, chunk in enumerate(retrieved or []):
            text, refs = await self._maybe_offload(
                chunk, audit_id=audit_id, label=f"retrieved_{i}"
            )
            layers.append(
                ContextLayer(
                    name=f"retrieved_{i}",
                    text=text,
                    token_estimate=estimate_tokens(text),
                    pinned=False,
                    refs=refs,
                )
            )

        pack = ContextPack(audit_id=audit_id, layers=layers)
        pack.total_tokens_est = sum(l.token_estimate for l in pack.layers)

        limit = max_context_tokens
        if limit is None:
            if budget is not None:
                limit = min(self.default_budget_tokens, budget.remaining_tokens() or self.default_budget_tokens)
            else:
                limit = self.default_budget_tokens

        if pack.total_tokens_est > limit:
            pack = await self.compact(pack, max_tokens=limit)

        return pack

    async def compact(self, pack: ContextPack, *, max_tokens: int) -> ContextPack:
        """Drop unpinned layers (largest first) until under budget; never drop pinned."""
        layers = list(pack.layers)
        dropped: list[str] = []
        total = sum(l.token_estimate for l in layers)

        # Sort unpinned by size desc for dropping
        while total > max_tokens:
            candidates = [
                (i, l)
                for i, l in enumerate(layers)
                if not l.pinned and l.name not in _PINNED_LAYER_NAMES and l.token_estimate > 0
            ]
            if not candidates:
                break
            candidates.sort(key=lambda x: x[1].token_estimate, reverse=True)
            idx, layer = candidates[0]
            # Prefer shrink over full drop for active
            if layer.name == "active" and len(layer.text) > 200:
                half = layer.text[: len(layer.text) // 2] + "\n…[compacted]"
                layers[idx] = ContextLayer(
                    name=layer.name,
                    text=half,
                    token_estimate=estimate_tokens(half),
                    pinned=False,
                    refs=layer.refs,
                )
            else:
                dropped.append(layer.name)
                layers[idx] = ContextLayer(
                    name=layer.name,
                    text=f"[offloaded/dropped:{layer.name}]",
                    token_estimate=1,
                    pinned=False,
                    refs=layer.refs,
                )
            total = sum(l.token_estimate for l in layers)

        return ContextPack(
            audit_id=pack.audit_id,
            layers=layers,
            total_tokens_est=total,
            compacted=True,
            dropped_layers=dropped + list(pack.dropped_layers),
            metadata=dict(pack.metadata),
        )

    async def _maybe_offload(
        self,
        text: str,
        *,
        audit_id: str,
        label: str = "blob",
    ) -> tuple[str, list[ArtifactRef]]:
        if len(text) <= self.offload_threshold_chars:
            return text, []
        from app.services.agent.domain import ArtifactKind

        ref = await self.artifacts.put(
            text,
            kind=ArtifactKind.SOURCE_SNIPPET,
            media_type="text/plain",
            audit_id=audit_id,
            suffix=".txt",
        )
        head = text[:500]
        summary = (
            f"[{label} offloaded to {ref.uri} hash={ref.content_hash}]\n"
            f"preview:\n{head}\n…"
        )
        return summary, [ref]
