# Claude Handoff：完成 DeepAudit 的 LangChain / LangGraph 改造

交接日期：2026-10-02。本文可直接作为下一轮实现任务使用。

## 0. 进度（2026-10-02，R2–R5 单机产品路径已落地）

R1 到 R5 都已在当前工作区实现。新的 Agent 审计仍走 `/api/v1/agent-tasks`，服务端默认 `engine=graph`，客户端可以显式选择 `engine=react`。`/api/v1/graph-audits` 仍注入 `FakeLLM()`，不要把它当成产品入口。

这不是多机 Postgres 切换，也不是隔离的 Docker 验证工人。Postgres checkpointer 会直接报错，不会退回内存。验证开关默认关闭；打开后只做进程内模式确认。没有模型密钥时任务是 partial，模式扫描仍会跑，不会把 FakeLLM 当成一次成功的模型审计。

验收和未覆盖项写在 `IMPLEMENTATION_STATUS.md` 的 R2–R5 一节。排除 `tests/test_report_generator.py` 后的后端套件是 **1117 passed, 8 skipped**。前端 `tsc --noEmit` 通过。登录后的浏览器点击路径没有跑。

R1（执行可信性）记录保留如下，不要重做。

R1 改动：

- `backend/app/services/agent/graph/limits.py`（新）：按预算计算 `recursion_limit`
- `backend/tests/test_agent_graph_r1.py`（新）：14 项回归
- `backend/app/services/agent/application/runner.py`：预算步数、`durability="sync"`、异常时从 checkpoint 保留发现
- `backend/app/services/agent/graph/nodes/__init__.py`：模型/工具错误、覆盖率、`FAILED` / `PARTIAL`
- `backend/app/services/agent/domain/common.py`：`ModelUsage` 的尝试、成功、失败、未知 token、无效输出
- `backend/app/services/agent/agents/base.py`：`_cancel_callback` 在构造函数初始化
- `backend/app/services/agent/core/executor.py`、`event_manager.py`：恢复测试所 patch 的 `get_agent_config` 契约

验收记录在 `IMPLEMENTATION_STATUS.md` 的 R1 一节。2026-10-02 复核：M1–M11 加 R1 共 **147 passed**。排除 `tests/test_report_generator.py` 后的后端套件是 **1102 passed, 8 skipped**（修复前为 1080 passed、8 failed、8 skipped）。此前失败的 5 个 Agent 初始化测试和 3 个 `get_agent_config` 测试已通过。报告测试仍因本机缺少 `libgobject-2.0-0` 无法收集。

仍未完成，不要标成产品完成：

- P2 `tools/base.py` 的 `get_langchain_tool()`（`from langchain.tools import Tool, StructuredTool` 仍会 ImportError）。没有生产调用方。
- RAG 未接入。默认图测试仍用 4000 字符预览；产品装配才会打开按行窗口。
- SQLAlchemy / Alembic 图业务表未建。续跑证明是单机文件 checkpoint 加 sqlite。
- Docker 验证工人仍是失败占位。stdio MCP 只在测试里接通，没有进产品工具路由。
- `POST /agent-tasks/{id}/start` 仍然不存在。创建任务时已经后台执行。继续跑用 `POST /{id}/resume`。

下文第 4 节是修复前的复现记录，保留作对照。

## 1. 用户目标与本次交接范围

用户希望把此前未完成的 LangChain / LangGraph 改造继续做完，最终让现有网页产品通过新的运行时完成真实项目审计。用户刚要求 Codex 核验进度，现在将实现工作交给 Claude。

请进入代码实现，按下文顺序逐阶段交付。不要停留在再次写架构方案、补目录、添加接口占位或更新“完成”标记。现有基础可以复用，无需整体重写。

Codex 本轮只做了代码阅读、测试和边界复现，并新增现状评估文档；没有修复业务代码。R1–R5 已由后续实现落地，见上文第 0 节。不要按本节后文的「从 R2 开始」再做一遍。

## 2. 工作区与已知状态

| 项目 | 状态 |
| --- | --- |
| 仓库 | `/Users/chiv/developer/github_repo/my repo/DeepAudit-agent-runtime` |
| 分支 | `codex/cli-lightweight` |
| HEAD | `3197824ef9920908b50ce29cfde2264a2c18dd66` |
| Python 环境 | `backend/.venv/bin/python`，Python 3.12.13，可直接运行测试 |
| 已核验依赖 | langchain 1.2.0、langchain-core 1.2.1、langgraph 1.0.5 |
| 前端 | React / Vite；本轮未安装 node_modules，未验证构建或浏览器 |
| 外部运行环境 | 本轮未启动数据库、Docker，未调用真实或付费模型 |

交接前工作区未跟踪文件：

```text
docs/INTERVIEW_QA.md                  # 用户原有文件，不要删除、覆盖或混入无关修改
docs/LANGGRAPH_REFACTOR_ASSESSMENT.md  # Codex 的实测评估
docs/CLAUDE_HANDOFF_LANGGRAPH.md       # 本交接文件
```

开始时重新核对 git status 和 HEAD，以当前工作区为准。不要 reset / clean 掉未跟踪文档。

先读：

1. `CLAUDE.md`
2. `docs/LANGGRAPH_REFACTOR_ASSESSMENT.md`
3. `IMPLEMENTATION_STATUS.md`
4. `docs/implementation/target-architecture.md`
5. ADR-001 / ADR-002 / ADR-003，以及 `docs/architecture/ADR-004-lightweight-cli-extraction.md`

CLAUDE.md 要求只实现当前 milestone。旧状态文件标记 M0–M11 完成，但产品迁移仍未完成：请新增后续迁移阶段 R1–R5，先把 R1 设为当前阶段；逐阶段完成验收、更新状态，再推进下一阶段。保留历史完成记录，补清“实验实现”和“产品接入”的区别。

## 3. 当前实际架构

```text
网页 → /api/v1/agent-tasks/* → 旧 Orchestrator / ReAct → 现有模型和工具

/api/v1/graph-audits/* → GraphAuditFacade → AuditRunner → StateGraph
                         默认 FakeLLM + fixture_files + 内存存储

AgentRuntime / Harness → AuditRunner
  工具、tracer、预算有节点接入；ContextPolicy 尚未实际构建上下文

cli/ → 独立标准库流水线 → 外部 Semgrep
  与后端 LangGraph 产品迁移分开，保持其零 Python 运行依赖边界
```

可复用：domain 模型、StateGraph 与 reducers、预算、ToolRegistry、ContextManager、独立验证子图、存储协议、观测接口、基础测试。

尚未接通：真实模型网关、正式项目来源、旧审计工具与 RAG/AST、上下文构建、生产持久化、中途恢复、实时产品事件、网页运行时切换、真实 MCP transport、Docker worker。

新图当前是顺序逐文件分析，不能把它描述为已完成真实多 Agent 协作。验证子图未接入主图，DockerSandboxExecutor 是占位实现。

## 4. 已复现问题：先处理

| 优先级 | 问题 | 位置与验收 |
| --- | --- | --- |
| P0 | 15 个文件成功，16 / 20 / 30 个文件因 recursion_limit=25 失败 | `application/runner.py` 的 ainvoke 配置；100 文件在充足预算下完成，终止条件与预算保持有界 |
| P0 | 模型 complete() 始终超时，任务仍 completed、0 findings、0 errors | `graph/nodes/__init__.py` 的 plan_audit / analyze_file / generate_report；全失败报告 failed，有有效降级结果时按明确覆盖率报告 partial |
| P1 | 旧 Agent 未设置回调就访问 is_cancelled 会抛 AttributeError | `agents/base.py`：`_cancel_callback` 错放在 cancel() 中初始化；构造、设置回调、取消的行为都应正确 |
| P2 | get_langchain_tool() 中旧 LangChain 导入与当前依赖不兼容 | `tools/base.py`；实际需要的工具桥可导入、可异步调用；未接入方法不要假称可用 |

其他必须处理的语义：

- Runner 图异常时保存的是空发现和错误消息，未保留已有图进度/结果。
- resume 对 completed / partial / cancelled / failed 直接返回；对未完成任务回到 run()，不是真正继续执行。
- 文件分析只给模型 `content[:4000]`，没有通过 ContextManager 分块、检索或说明模型阅读范围。
- facade 在图结束后才 publish_many(result.events)，中途查询进度不会得到完整实时节点事件。
- 新 HTTP 门面直接创建 GraphRuntime，Harness 则单独组装模型/工具/预算；应收敛依赖组装，避免网页路径绕过治理能力。

从 backend/ 执行以下脚本可重现两个 P0。该脚本只用内存 fixture 和假模型，不访问网络：

```bash
.venv/bin/python - <<'PY'
import asyncio
import json
import logging
from app.services.agent.application import AuditRunner
from app.services.agent.domain import AuditRequest, RepositoryRef, RunBudget
from app.services.agent.graph.runtime import GraphRuntime

logging.disable(logging.CRITICAL)

class BrokenLLM:
    async def complete(self, messages, **kwargs):
        raise TimeoutError("handoff: model unavailable")

async def main():
    for n in (15, 16, 20, 30):
        request = AuditRequest(
            repository=RepositoryRef(source_type="local", local_path="fixture://handoff"),
            budget=RunBudget(max_files=100, max_model_calls=200, max_tokens=100000),
        )
        runtime = GraphRuntime(extra={
            "fixture_files": {f"f{i}.py": "value = 1\n" for i in range(n)}
        })
        result = await AuditRunner().run(request, runtime=runtime)
        print(json.dumps({
            "files": n,
            "status": result.status.value,
            "error": result.record.error_message if result.record else None,
        }))
    request = AuditRequest(repository=RepositoryRef(
        source_type="local", local_path="fixture://handoff"
    ))
    runtime = GraphRuntime(
        llm=BrokenLLM(),
        enable_heuristic_analysis=False,
        extra={"fixture_files": {"app.py": "eval(user_input)\n"}},
    )
    result = await AuditRunner().run(request, runtime=runtime)
    print(json.dumps({
        "model_timeout_status": result.status.value,
        "findings": len(result.findings),
        "errors": len(result.raw_state.get("errors", [])),
    }))

asyncio.run(main())
PY
```

## 5. 实施顺序与验收标准

### R1：修复执行和结果可信度

先阅读相关实现和测试，列出本阶段修改文件，然后修上述 P0/P1。

- 图步骤限制根据实际有界分析计划设置，或采用有界批处理/并发。不要设置一个巨大固定值掩盖终止问题。
- 模型超时、无效结构化输出、工具错误进入结构化状态和覆盖率；区分调用尝试、成功调用、未知/已知 token 消耗。
- 部分失败保留已有发现和证据，报告指出哪些单元未完成。预算耗尽仍应 partial。
- 修复取消字段初始化；核实另外 3 个失败测试是否过时，按当前有效契约更新测试或恢复缺失行为，不能删断言绕过失败。
- 补有意义的回归测试：100 文件、全模型失败、部分模型失败、异常保留已有结果、取消与预算边界。

验收：原有运行时测试通过；两个 P0 不再复现；旧 Agent 初始化相关 5 个测试通过；其余失败有修复或明确的环境/契约说明。

### R2：真实项目 → 模型与工具 → 结构化报告

- 为 Graph LLMGateway 实现复用 `app.services.llm` 的适配器，传递用户模型配置、调用参数和 usage。FakeLLM 留给明确的离线/测试模式。
- 统一运行时组装，让实际 API 使用 Harness 的模型、工具、权限、预算和上下文策略。
- 复用现有项目获取/快照逻辑，输入来自服务端已授权项目；不要通过开放任意主机 local_path 或任意 URL 绕过项目边界。
- 为旧审计工具提供 ToolProtocol 适配，真正接入读取、检索和分析能力；不要以 heuristic_scan 演示替代产品能力。
- 让 ContextManager 参与真实模型请求，长文件按可说明的块/范围处理；RAG 检索按开关接入，报告记录覆盖范围和缺口。
- 对模型结构化发现做 schema、位置和证据校验，保持候选与已验证发现的区别。

验收：API 的真实项目流程可用注入网关做完整离线集成验证；实际网关另有显式集成检查，验证配置和调用，不泄露 key。真实模型审计质量另行记录，不把 FakeLLM 测试当作质量证明。

### R3：持久化、恢复和共享任务控制

- 引入兼容锁定 LangGraph 版本的异步持久 checkpointer；开发内存模式与生产持久模式明确区分，生产配置失败不能悄悄回退内存。
- 实现 BusinessAuditStore 的 SQLAlchemy async 适配，复用/扩展现有业务模型与 Alembic 迁移。checkpoint、业务记录、制品分别保存。
- 从 checkpoint 的剩余节点继续运行。明确“继续”“失败重试”“从头重跑”的语义；避免与已在运行任务并发重复启动。
- 持久事件与任务控制支持跨进程/worker，依序重放、取消和租约/所有权协调。可复用仓库已有 Redis 依赖。
- 幂等保存发现和报告，保存模型/工具结果后不重放已完成单元。对外部模型请求发送后、结果持久化前的崩溃窗口记录未知状态与恢复策略，不承诺外部请求天然 exactly-once。

验收：独立进程 A 执行一部分后退出，进程 B 查询并继续剩余工作；已有结果保留、已完成单元不重复分析；另一 worker 的查询/取消/事件重放正确。只在同一 runner 上 resume completed 不算通过。

### R4：接入现有网页与 API 契约

- 优先在应用层增加可控 runtime 选择，保留 `/api/v1/agent-tasks/*` 和既有响应/SSE 字段。先完成兼容路径，再切换实际网页使用的运行时。
- 覆盖创建、启动、列表、详情、发现查询与编辑、summary、agent tree/checkpoints（按现有界面需要）、报告导出、实时事件和取消。
- 不要仅将前端 URL 换为 `/graph-audits`：新路由目前没有上述完整契约。
- 节点执行中发布事件并更新业务进度；断线可按 sequence 重放。确保终态、partial、错误和验证状态正确展示。

验收：浏览器从项目页创建 LangGraph 审计，过程中看到事件，完成后查看/编辑发现、下载报告；覆盖取消与失败反馈。旧 ReAct 可保留兼容回退，产品有明确方式选择并实际运行新引擎。

### R5：角色协作和可选验证的剩余能力

- 在完整主流程稳定后，接入侦察、分析、聚合角色职责、跨文件任务和受预算约束的并发，定义正确 reducers 与合并/去重逻辑。
- 接入验证子图；启用执行时使用实际隔离 worker，未执行保持 NOT_RUN。DockerSandboxExecutor 的占位错误不能算 worker 完成。
- 实现真实 MCP transport 生命周期、工具发现与授权；可配置外部服务，默认测试仍用假 transport。
- 检查并发 tracer 隔离与指标，确保各任务的观测记录不会被可变全局状态混淆。
- 扩充覆盖真实失败模式和跨文件案例的 eval，并让 eval 失败产生失败退出码/CI 结果。

验收：跨文件案例有完整证据链；并发限制和预算生效；验证执行/未执行的报告语义不同；MCP 集成有独立验证；没有将占位模块标为产品完成。

## 6. 兼容与实现约束

沿用 CLAUDE.md 的栈和边界：

- 服务端代码放在 `backend/app/services/agent/`，使用现有 Settings、SQLAlchemy async、Alembic 和 LLM 网关，不增加第二套配置或 ORM。
- LangGraph 管状态与流程，Harness 包装它；不要重新实现一个 ReAct/调度循环来绕过 StateGraph。
- 节点返回状态增量，大源码和执行产物用 ArtifactRef；运行时客户端不放入持久图状态。
- 保持身份与项目权限、路径约束、工具允许列表和验证开关；默认测试不执行不可信代码、不访问付费模型。
- ADR-004 已允许独立 `cli/`，不要删除它或让它依赖后端、LangChain、Web/DB SDK。两套 deepaudit 入口来自不同发行包，留意测试和安装环境。
- 保持轻量 import 边界；Harness import 不应无条件初始化旧 RAG 或 Orchestrator。
- 不做无关依赖升级、大范围重命名或全文格式化。新增依赖确有必要时同步 pyproject / lock 和部署配置。

## 7. 已知测试基线与复核命令

以下是 2026-10-02 Codex 实际运行结果，不是待达到的最终指标：

| 检查 | 基线 |
| --- | --- |
| M1–M11 运行时 | 133 passed |
| CI 离线 eval | 2/2 passed |
| 独立 CLI | 19 passed |
| 后端全量 | WeasyPrint 缺 `libgobject-2.0-0`，收集被阻断 |
| 排除报告测试后 | 1080 passed、8 failed、8 skipped |

8 个失败：

```text
tests/agent/test_agents.py::TestReconAgent::test_recon_agent_run
tests/agent/test_agents.py::TestReconAgent::test_recon_agent_identifies_python
tests/agent/test_agents.py::TestReconAgent::test_recon_agent_finds_high_risk_areas
tests/agent/test_agents.py::TestAnalysisAgent::test_analysis_agent_run
tests/agent/test_agents.py::TestAnalysisAgent::test_analysis_agent_finds_vulnerabilities
tests/test_event_manager_deep.py::TestEventManagerStreamEvents::test_stream_filters_by_sequence
tests/test_event_manager_deep.py::TestEventManagerStreamEvents::test_stream_creates_queue_if_missing
tests/test_executor.py::TestDynamicAgentExecutor::test_constructor_reads_config_when_timeout_is_none
```

后 3 项 patch 了当前模块不存在的 get_agent_config，需要阅读契约再修，不能直接认定为生产缺陷。报告收集错误是本机系统库缺失，本轮没有证明 PDF 功能正常或有代码缺陷。

从 backend/ 执行：

```bash
.venv/bin/python -m pytest tests/test_agent_domain.py tests/test_agent_graph_m2.py tests/test_agent_persistence_m3.py tests/test_agent_facade_m4.py tests/test_agent_context_m5.py tests/test_agent_sandbox_m6.py tests/test_agent_verification_m7.py tests/test_agent_tooling_m8.py tests/test_agent_observability_m9.py tests/test_agent_evals_m10.py tests/test_agent_harness_m11.py -q --tb=short
.venv/bin/python -m tests.evals.runner --ci
.venv/bin/python -m pytest -q --tb=short --disable-warnings --ignore=tests/test_report_generator.py
.venv/bin/python -m pytest tests/test_agent_import_boundaries.py tests/test_cli_application.py tests/test_semgrep_scanner.py -q
```

从 cli/ 执行：

```bash
../backend/.venv/bin/python -m pytest -q --tb=short
```

改动后按 CLAUDE.md 对相关文件运行 lint / format / typecheck。前端使用仓库现有锁文件选择包管理器，运行 package.json 的 test、type-check、lint、build，并做浏览器验证。不要把排除报告测试的结果称为全量通过。

## 8. 关键代码导航

| 关注点 | 文件 |
| --- | --- |
| 图构建 / 状态 / 路由 | `backend/app/services/agent/graph/{builder,state,routing}.py` |
| 节点实现 | `backend/app/services/agent/graph/nodes/__init__.py` |
| 模型协议 / 注入 | `backend/app/services/agent/graph/{llm,runtime}.py` |
| Runner / API 门面 / 事件 | `backend/app/services/agent/application/{runner,facade,event_bus,api_mapping}.py` |
| Harness | `backend/app/services/agent/harness/__init__.py` |
| 持久化 | `backend/app/services/agent/persistence/{checkpointer,business_store,artifact_store}.py` |
| 新旧路由 | `backend/app/api/v1/endpoints/{graph_audits,agent_tasks}.py` |
| 旧 Agent / 工具 | `backend/app/services/agent/agents/`、`tools/`、`core/` |
| 模型网关 | `backend/app/services/llm/{service,factory,types,base_adapter}.py`、`adapters/` |
| 上下文 / 工具 / 验证 / 沙箱 | `backend/app/services/agent/context/`、`tooling/`、`graph/subgraphs/`、`sandbox/` |
| 业务数据 | `backend/app/models/agent_task.py`、项目/权限模型、现有 migrations |
| 网页契约 | `frontend/src/shared/api/{agentTasks,agentStream}.ts`、`frontend/src/pages/AgentAudit/` |
| 轻量 CLI | `cli/src/deepaudit_cli/`，另有后端旧原型 `application/local_audit.py` |
| CI | `.github/workflows/agent-pytest.yml` |

## 9. 交付和最终完成标准

每阶段给出实际改动、测试结果、未验证依赖与剩余工作，更新 IMPLEMENTATION_STATUS；README 的能力声明与代码一致。缺少外部环境时先完成可独立验证的实现，清楚记录集成检查尚未执行，不以占位实现替代。

只有以下链路实际通过验收，才可称主产品迁移完成：

```text
网页创建有权限的真实项目审计
→ LangGraph + 真实网关/工具 + 受控上下文/预算
→ 过程中可查看事件并取消
→ 结构化证据与明确覆盖率
→ 持久保存、跨进程查询与中途恢复
→ 网页查看/编辑发现、导出报告
```

R5 的 MCP、角色协作和可选执行能力也应如实列出完成状态；不能用“主流程通过”掩盖仍在占位的扩展能力。

**下一步从 R2 开始。** R1 已落地，不要重做。R2 要做的是：在现有 `app.services.llm` 网关上加适配器，由 Harness 组装 API 路径的运行时，项目输入来自已授权的服务端快照，把现有审计工具接到 `ToolProtocol`，并让 ContextManager 进入模型请求。验收用离线注入的网关集成测试。不要把 FakeLLM 的通过当成审计质量证明。
