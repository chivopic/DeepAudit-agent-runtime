# Context Manager (M5)

> Package: `backend/app/services/agent/context/`  
> Status: implemented 2026-07-24

## Layers

```text
Pinned · SecurityPolicy · FindingsIndex · PendingTasks
  · RunningSummary · Active · Retrieved*
```

Pinned layers are **never** dropped on compaction. Large active/retrieved blobs are offloaded to `ArtifactStore` and replaced with a preview + `ArtifactRef`.

## API

```python
cm = ContextManager(artifacts=InMemoryArtifactStore())
pack = await cm.build_context(audit_id=..., pinned=..., active=..., findings=..., max_context_tokens=8000)
prompt = pack.as_prompt_block()
```

## Tests

`tests/test_agent_context_m5.py`
