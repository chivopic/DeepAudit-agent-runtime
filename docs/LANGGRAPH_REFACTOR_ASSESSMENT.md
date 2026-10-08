# LangChain / LangGraph 改造现状核验

核验日期：2026-10-02。代码基线：`3197824`。

## 结论

当前达到的是“可运行、可测试的 LangGraph 架构原型”，实际产品流程尚未迁移完成。
领域模型、图节点、存储协议、工具协议、观测接口和 Harness 有可复用实现；但网页审计仍走旧 ReAct 路径，新增 HTTP 路径使用 FakeLLM，关键生产适配器和端到端连接未完成。

README / IMPLEMENTATION_STATUS 中 M0–M11 的 Done 应理解为实验阶段交付，不能据此认定真实模型审计、持久化恢复、工具集成或产品切换已经完成。

本轮只核验现状、运行测试并补充本文档，未改业务代码。未调用付费模型、启动数据库或 Docker，也未运行浏览器产品验收。

## 已实现与待接入

| 能力 | 当前可确认实现 | 仍缺的产品连接 |
| --- | --- | --- |
| 领域模型 | Pydantic 审计请求、发现、证据、预算、报告及 API 映射 | 与现有业务数据库的完整读写连接 |
| 图编排 | StateGraph 请求校验、仓库元信息、文件清单、扫描、规划、逐文件分析、去重、报告 | 真实角色协作、跨文件分析、受预算约束的并发 |
| 模型调用 | LLMGateway 协议、FakeLLM、可注入依赖 | 现有 LLMService / adapter 网关的适配器；用户模型配置、超时与错误语义 |
| HTTP 门面 | 新建、查询、取消、恢复、发现、事件列表；JWT 与审计创建者权限 | 正式项目来源、旧产品 API 的完整功能契约、网页入口 |
| 持久化 | MemorySaver、InMemoryBusinessStore、内存/文件制品存储、SQLite 工厂 | 生产异步 checkpointer、SQLAlchemy 业务适配器、进程重启恢复 |
| 取消与恢复 | 进程内协作式取消；完成结果读取 | 从未完成 checkpoint 继续执行；跨 worker 控制 |
| 上下文 | ContextManager 的构建/压缩/制品卸载及单测 | 主图和 Harness 中的实际调用；目前分析只传文件前 4000 个字符 |
| 工具 / MCP | ToolRegistry、Builtin / Mock / MCP adapter、允许列表及节点调用 | 旧审计工具、RAG、AST 能力迁移；真实 MCP SDK transport |
| 验证 | 独立验证子图、Null / 本地允许列表执行器 | 主图验证分支；Docker worker 实际执行。默认 NOT_RUN 是合理的阶段行为 |
| 观测与事件 | 注入 tracer、节点/工具/模型 span、内存事件总线 | 节点执行中实时发布；持久事件、断线重放及多 worker 协调 |
| CLI | 独立 cli/ 标准库流水线、外部 Semgrep、覆盖率与退出码 | 它是静态扫描工具，不代表 LangGraph 模型审计迁移完成 |

## 关键证据

1. `frontend/src/shared/api/agentTasks.ts:154` 创建任务仍 POST `/agent-tasks/`，列表、详情、报告和流接口也使用旧 API。`backend/app/api/v1/endpoints/agent_tasks.py:439` 创建旧 OrchestratorAgent。
2. `backend/app/api/v1/endpoints/graph_audits.py:231` 显式构造 `FakeLLM()`、`offline=True`；同文件拒绝 Git 来源，默认要求 fixture_files。`harness/__init__.py:57` 的 ModelRouter 返回注入的 default，并未按 model/provider 选择真实网关。
3. `application/runner.py:79` 默认业务库和制品库为内存实现；默认 checkpoint backend 为 memory。当前 API 不使用现有 AgentTask / AgentFinding 数据表。
4. `application/runner.py:230` 对 completed / partial / cancelled / failed 的 resume 直接返回已有结果；未完成任务最终重新调用 run()。没有从 checkpoint 继续执行剩余节点的实现。
5. `harness/__init__.py:81` 明确将 ContextPolicy 接入标为 later；主图未调用新 ContextManager。`graph/nodes/__init__.py:975` 只给模型传 `content[:4000]`，未在报告中表达这一模型阅读范围。
6. `graph/builder.py` 中优先级节点直接进入报告，未连接验证子图；`sandbox/__init__.py:225` 的 DockerSandboxExecutor 是占位实现，允许动作也返回 worker_required 失败。
7. `application/facade.py` 等待 runner.run() 完成后才 publish_many(result.events)；HTTP events 路由返回列表。内部异步队列不等于已有实时网页流功能。
8. 新计划固定 strategy=sequential、max_parallel=1。Harness 中的 max_parallel_analyzers 参数未驱动图并发。

## 本轮复现的问题

### P0：文件数稍大即触发图步数限制

在当前虚拟环境（langchain 1.2.0、langchain-core 1.2.1、langgraph 1.0.5）中，RunnableConfig 默认 recursion_limit 为 25。AuditRunner 未显式配置该值，逐文件分析每次消耗一个图步骤，固定前后节点同样消耗步骤。

复现通过 fixture map 输入 N 个内容为 `value = 1` 的 Python 文件，max_files=100、max_model_calls=100、max_tokens=100000：

| 文件数 | 运行结果 |
| --- | --- |
| 15 | completed |
| 16 | failed：Recursion limit of 25 reached |
| 20 | failed：同上 |
| 30 | failed：同上 |

触发点：`application/runner.py:126`。失败分支将业务结果写为 findings=[]，未把 checkpoint 中的已有发现和进度保留下来，resume 对 failed 又直接返回。

改造要求：让图步骤上限与有界文件预算一致，或改为有界批处理/并发调度；异常保存已经产出的状态；覆盖大于默认图步数的输入。增大上限只是近期修复，不能替代终止条件和预算。

### P0：模型不可用却报告审计完成

注入 complete() 始终抛 TimeoutError 的网关，关闭启发式分析，输入 `app.py: eval(user_input)`。结果为：

```json
{"status":"completed","findings":0,"errors":0,"model_calls_accounted":0}
```

报告仍写 Audit completed with 0 finding(s)。规划和分析节点 catch Exception 后只记录日志，不把模型失败放入结构化错误或覆盖率；报告据预算/扫描状态判定完成。

触发点：`graph/nodes/__init__.py:1025`，规划节点存在同类处理。

改造要求：区分调用尝试、成功调用与 token 使用；记录失败分析单元和实际覆盖范围；按剩余有效分析给出 failed / partial，禁止把模型不可用映射为完整审计完成。

### P1：旧 Agent 默认取消状态有初始化缺陷

5 个角色 Agent 测试因 `_cancel_callback` 不存在失败。`agents/base.py` 把该字段初始化放在 cancel() 内，而构造函数没有初始化。未调用 set_cancel_callback() 的实例访问 is_cancelled 会抛 AttributeError。

这是当前代码的可复现缺陷；本轮未追溯它由哪个历史提交引入。

### P2：遗留 LangChain 工具桥与锁定依赖不兼容

`tools/base.py:113` 使用 `from langchain.tools import Tool, StructuredTool`。在当前虚拟环境直接导入会抛 ImportError。仓库中未发现调用 get_langchain_tool() 的生产代码，所以它目前是未接通的旧桥；接入旧工具时必须修复与覆盖，不能把这个方法存在当成已完成迁移的证据。

## 实际验证结果

| 检查 | 结果与限制 |
| --- | --- |
| M1–M11 运行时测试 | 133 passed；主要为 FakeLLM / 内存依赖 / 小 fixture |
| CI eval runner | 2/2 passed；是小型离线案例，未验证真实模型审计质量 |
| 独立 CLI 测试 | 19 passed；未重新执行真实 Semgrep 审计 |
| 后端全部测试 | 收集阶段因本机缺 libgobject-2.0-0，无法导入 WeasyPrint |
| 排除报告测试后的后端套件 | 1080 passed、8 failed、8 skipped |
| 新增边界复现 | 16+ 文件触发图步数限制；模型超时仍 completed；旧工具桥导入失败 |
| 前端构建 / 浏览器 / DB / Docker / 真模型 | 本轮未验证；frontend/node_modules 未安装 |

8 个失败中，5 个是上述旧 Agent 初始化缺陷；2 个事件测试和 1 个执行器测试 patch 了当前模块不存在的 get_agent_config，属于测试与实现接口不一致，不能全部归因为运行时缺陷。

复核命令（从 backend/ 执行）：

```bash
.venv/bin/python -m pytest tests/test_agent_domain.py tests/test_agent_graph_m2.py tests/test_agent_persistence_m3.py tests/test_agent_facade_m4.py tests/test_agent_context_m5.py tests/test_agent_sandbox_m6.py tests/test_agent_verification_m7.py tests/test_agent_tooling_m8.py tests/test_agent_observability_m9.py tests/test_agent_evals_m10.py tests/test_agent_harness_m11.py -q --tb=short
.venv/bin/python -m tests.evals.runner --ci
.venv/bin/python -m pytest -q --tb=short --disable-warnings --ignore=tests/test_report_generator.py
```

## 接下来按可验收流程完成迁移

1. **恢复可信执行基线。** 修复两个 P0 和取消字段初始化，校准过时测试与依赖桥；把模型失败、输入截断、文件覆盖率和已有结果保留写进契约。验收：100 文件有界运行、失败真实报告、取消前后状态正确。
2. **接通真实审计的最小完整流程。** 用适配器复用现有 LLM 网关和审计工具，输入来自用户有权限访问的服务器项目快照；把上下文构建接进分析节点，产出结构化发现和报告。验收：一个真实项目能完成输入 → 模型/工具 → 证据 → 报告，配置与预算生效。
3. **完成持久执行。** 增加异步持久 checkpointer、SQLAlchemy 业务存储、制品保存与中途继续执行；共享任务控制和事件。验收：进程重启后能查询结果、继续剩余工作，并且不重复写发现或重复计费已经完成的单元。
4. **接入现有产品契约。** 在应用层统一 runtime 选择，完整支持旧网页依赖的列表、详情、发现编辑、报告、事件与取消接口，再接通实时事件。不能只把前端 URL 替换成 /graph-audits，因为后者目前缺少对应功能。验收：网页走 LangGraph 完成真实项目审计、查看/编辑结果、下载报告。
5. **扩展协作与可选验证。** 在上述完整流程稳定后添加角色子图、跨文件任务与受控并发；按明确开关接验证子图和真实 worker。验收：跨文件证据可追踪，启用验证才标记运行结果，未执行继续保持 NOT_RUN。

可以保留现有 domain、StateGraph、协议和基础测试；接下来的主要工作是完善这些边界、复用旧能力并把实际产品连接起来。

## R1 落地后（2026-10-02）

上文「本轮复现的问题」是修复前的证据，保留不动。R1 之后的代码状态：

| 原先的问题 | 现在 |
| --- | --- |
| 16 个以上文件触发 recursion limit 25，失败时发现被清空 | `recursion_limit_for_budget` 按文件上限和模型调用上限取较小值。100 文件、充足预算的回归为 completed。图异常时从最后一次 checkpoint 写回已有发现，状态为 failed |
| 模型始终超时仍 completed、0 findings、0 errors | 全失败且无降级结果为 failed。启发式仍产出发现时为 partial，发现保留，未完成单元写在报告覆盖率里 |
| `_cancel_callback` 未初始化 | 构造函数初始化。5 个旧 Agent 测试通过 |
| 三份测试 patch 不存在的 `get_agent_config` | `executor.py` 与 `event_manager.py` 恢复了这个模块级名字，并分别读取子 Agent 超时和 SSE 心跳间隔。三份测试通过 |
| `get_langchain_tool()` 导入失败 | 未改。仍无生产调用方，留到接入旧工具时处理 |
| 模型只看 `content[:4000]` | 超长文件记入 `truncated_units`，报告为 partial。ContextManager 分块仍未接入，属于 R2 |

M1–M11 与 R1 一起跑是 147 passed。排除报告测试后的后端套件当时是 1102 passed、8 skipped。没有启动数据库、Docker 或真实模型，也没有做浏览器验收。

## R2–R5 落地后（2026-10-02）

网页产品入口仍是 `/api/v1/agent-tasks`。新建任务默认 LangGraph，对话框里可以改回 ReAct。`/graph-audits` 继续是 FakeLLM 夹具面。

单机文件 checkpoint 加 sqlite 业务行可以跨进程续跑，并且不重分析已完成文件。缺密钥时是 partial 加模式扫描，不是一次假装成功的模型审计。验证默认 `NOT_RUN`；勾选后只做进程内模式确认。跨文件 eval 在 CI 三例里通过。

没有做的事：Postgres checkpointer（请求它会失败）、Alembic 图业务表、Docker 工人、RAG、把 stdio MCP 注册进产品工具、付费模型质量、登录后的浏览器走查。细节在 `IMPLEMENTATION_STATUS.md`。

官方语义参考：[持久化与内存 checkpoint 限制](https://docs.langchain.com/oss/python/langgraph/persistence)、[interrupt / resume](https://docs.langchain.com/oss/python/langgraph/interrupts)、[图步数限制](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)。
