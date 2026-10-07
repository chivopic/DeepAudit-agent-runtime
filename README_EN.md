# DeepAudit - Your AI Security Audit Team, Making Vulnerability Discovery Accessible

<div style="width: 100%; max-width: 600px; margin: 0 auto;">
  <img src="frontend/public/images/logo.png" alt="DeepAudit Logo" style="width: 100%; height: auto; display: block; margin: 0 auto;">
</div>

<div align="center">

[![Version](https://img.shields.io/badge/version-3.0.0--agent--runtime-blue.svg)](https://github.com/chenzh659)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![React](https://img.shields.io/badge/React-18-61dafb.svg)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178c6.svg)](https://www.typescriptlang.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776ab.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-agent%20runtime-1f6feb.svg)](https://langchain-ai.github.io/langgraph/)

<p align="center">
  <a href="README.md">简体中文</a> | <strong>English</strong> | <a href="README_JA.md">日本語</a>
</p>

> **About this repo**: experimental **Agent Runtime refactor (M0–M11)** based on upstream [lintsinghua/DeepAudit](https://github.com/lintsinghua/DeepAudit) v3.0.0.  
> Production ReAct path stays compatible; LangGraph dual-path + protocolized runtime are additive. Previous READMEs: [`backup/`](backup/).  
> New Agent audits default to LangGraph through `/api/v1/agent-tasks`. The create dialog can still select classic ReAct. Single-host resume is in place. Multi-host Postgres resume and a Docker verification worker are not. Scope: [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md).

</div>

<div align="center">
  <img src="frontend/public/DeepAudit.gif" alt="DeepAudit Demo" width="90%">
</div>

---

## Screenshots

<div align="center">

### Agent Audit Entry

<img src="frontend/public/images/README-show/Agent审计入口（首页）.png" alt="Agent Audit Entry" width="90%">

*Quick access to Multi-Agent deep audit from the homepage*

</div>

<table>
<tr>
<td width="50%" align="center">
<strong>Audit Flow Logs</strong><br/><br/>
<img src="frontend/public/images/README-show/审计流日志.png" alt="Audit Flow Logs" width="95%"><br/>
<em>Watch agents reason and act in real time</em>
</td>
<td width="50%" align="center">
<strong>Dashboard</strong><br/><br/>
<img src="frontend/public/images/README-show/仪表盘.png" alt="Dashboard" width="95%"><br/>
<em>Security posture at a glance</em>
</td>
</tr>
<tr>
<td width="50%" align="center">
<strong>Instant Analysis</strong><br/><br/>
<img src="frontend/public/images/README-show/即时分析.png" alt="Instant Analysis" width="95%"><br/>
<em>Paste code or upload files for quick results</em>
</td>
<td width="50%" align="center">
<strong>Project Management</strong><br/><br/>
<img src="frontend/public/images/README-show/项目管理.png" alt="Projects" width="95%"><br/>
<em>Import from GitHub / GitLab / Gitea</em>
</td>
</tr>
</table>

---

## CVE Discovery

Upstream DeepAudit (closed-source lineage) has been credited with **49 CVEs** and **6 GHSA** advisories across popular open-source projects. Full tables: [CVEList.md](CVEList.md) and [backup/README.md](backup/README.md).

This repository focuses on the **agent runtime architecture** evolution.

---

## Overview

**DeepAudit** is a next-generation code security audit platform built on multi-agent collaboration—not “just another SAST scanner.”

This branch delivers the **Agent Runtime refactor (M0–M11)** while freezing the production API surface:

| Pillar | Decision |
|--------|----------|
| Orchestration | **LangGraph** for state / checkpoints / interrupt |
| Domain | **Pydantic** models for API, tools, sandbox I/O |
| Persistence | Checkpoint ≠ business DB ≠ Artifact Store |
| Security | Phase 1: no untrusted exec by default; `verification_status=NOT_RUN` |
| Migration | Same entry, two engines: new tasks default to LangGraph; `engine=react` keeps classic ReAct |

> Core idea: attack like a hacker, defend like an expert.

## Why DeepAudit?

| Pain | DeepAudit approach |
| :--- | :--- |
| Manual audit can’t keep up with CI/CD | Multi-agent autonomous planning |
| High false positives | Semantic + structured findings / RAG |
| Cloud data privacy concerns | Local models via Ollama |
| Unverified findings | Optional sandbox / verification subgraph |

---

## Architecture

### Dual-path agent runtime (this repo)

```text
Frontend  →  /api/v1/agent-tasks/*
             engine=graph   default LangGraph audit
             engine=react   classic ReAct fallback
          →  /api/v1/graph-audits/*  fixture FakeLLM only

backend/app/services/agent/
  domain/ graph/ persistence/ application/
  context/ sandbox/ tooling/ observability/ harness/
```

| Milestone | Topic | Status |
|-----------|-------|--------|
| M0–M11 | Experimental runtime | Recorded complete (2026-07-24) |
| R2–R5 | Product path on `/api/v1/agent-tasks` | Landed for one host (2026-10-02) |

The current path uses serial file analysis and pinned source artifacts for resume. Input omissions produce partial coverage. See the [repair record](docs/REPAIR_2026-10-03.md); full product acceptance is pending.

M0–M11 is the experimental runtime record. The create dialog now starts R2–R5. Checkpoint resume is a single-host file. M3 is not a Postgres checkpointer, and M7 is not a Docker worker.

### What a user can run

Create still posts to `/api/v1/agent-tasks`. LangGraph is the default; the same dialog can select classic ReAct. Create already starts the run. There is no `POST /{id}/start`. Paused or failed graph tasks continue with `POST /{id}/resume` from the local file checkpoint. Completed, partial, and cancelled resumes do not start again.

The activity log is short Chinese sentences: list files, match common dangerous patterns, then one updating analysis-progress line. The progress denominator is the queued file count. Pattern hits are clues, not confirmed vulnerabilities. Model opinions are labeled separately. The closing sentence states how many files were read and how long it took. A task that already finished keeps the text it stored.

A `partial` progress bar is 100 because the run has stopped. File coverage stays `analyzed / total`, for example 7/15. A paused task keeps the phase-weighted position.

With no model key the task stays partial and runs pattern scan only. The verification checkbox is off by default, so findings stay unconfirmed. Turning it on rechecks static patterns. Results remain inconclusive and confidence does not increase. `/api/v1/graph-audits` stays the fixture API.

Still open: multi-host Postgres resume, an isolated Docker verification worker, RAG on the graph path, and registering stdio MCP on the product tool router.

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md), [retro & audit](docs/implementation/m0-m11-retro-and-audit.md), [target architecture](docs/implementation/target-architecture.md).

### Layout

```text
DeepAudit/
├── backend/app/services/agent/   # ★ runtime packages (M1–M11)
├── backend/tests/                # deterministic unit + evals
├── frontend/
├── docs/architecture/            # ADRs
├── docs/implementation/          # milestone docs
├── backup/                       # previous READMEs
└── IMPLEMENTATION_STATUS.md
```

---

## Quick Start

### One-liner (upstream images)

```bash
curl -fsSL https://raw.githubusercontent.com/lintsinghua/DeepAudit/v3.0.0/docker-compose.prod.yml | docker compose -f - up -d
```

Open http://localhost:3000

### Clone this repo (agent-runtime work)

```bash
git clone https://github.com/chenzh659/DeepAudit-agent-runtime.git
cd DeepAudit-agent-runtime
cp backend/env.example backend/.env
docker compose up -d
```

### Local backend tests

```bash
cd backend
uv sync --extra dev
uv run pytest tests/test_agent_*.py -q
uv run python -m tests.evals.runner --ci
# Includes test_agent_graph_r1.py and test_agent_product_r2.py.
# Counts live in IMPLEMENTATION_STATUS.md for the run that produced them.
```

---

## Phase 1 security invariants

1. Findings default to `verification_status=not_run`
2. No raw model shell; sandbox allowlist only
3. Path jails on domain paths, tools, file reads, artifacts
4. Large blobs via `ArtifactRef`, not checkpoints
5. Harness **wraps** LangGraph (no second agent loop)

---

## Feature matrix

| Feature | Mode |
|---------|------|
| Multi-Agent deep audit | Agent |
| LangGraph dual-path + protocols | This branch |
| RAG / rules / reports / projects | Product |
| Deterministic evals + CI | This branch |

---

## Roadmap

- [x] Multi-Agent product path
- [x] Agent Runtime M0–M11
- [x] JWT and owner checks on `/graph-audits`
- [x] Tools, tracer, and budget inside graph nodes
- [x] Single-host file-checkpoint resume on `/api/v1/agent-tasks`
- [ ] Multi-host Postgres resume
- [ ] Sandbox worker process (ADR-003)
- [ ] Auto-fix / PR incremental audit

---

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). Upstream product: [lintsinghua/DeepAudit](https://github.com/lintsinghua/DeepAudit). This fork/experiment: [@chenzh659](https://github.com/chenzh659).

## License

[AGPL-3.0](LICENSE) (same as upstream).

---

## Security notice

Unauthorized security testing is prohibited. Research / education only. See [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md).

Code may be sent to the LLM provider you configure; use local models for sensitive code.
