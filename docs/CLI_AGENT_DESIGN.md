# DeepAudit CLI Agent 设计文档

> 状态：提案 v0.2（与 PRD v0.2 对齐）  
> 日期：2026-08-19  
> 受众：希望在本地运行 DeepAudit 的开发者、维护者与安全工程师  
> Gate 0 记录：[`CLI_AGENT_GATE0.md`](./CLI_AGENT_GATE0.md)  
> CLI-1 实现记录：[`CLI_AGENT_CLI1.md`](./CLI_AGENT_CLI1.md)

## 1. 结论与目标

本提案新增一个**本地优先**的 `deepaudit` 命令行程序，用于对本地代码目录进行可复现的安全审计。工作区由用户选择，但其中的文件名、源码、配置、规则消息和工具输出一律按不可信输入处理。CLI 应复用 `backend/app/services/agent/` 中已有的领域模型、运行时、预算、工具权限和报告模型；CLI 只是输入、输出和本地运行的适配层，不能复制一套独立的 Agent 循环。

第一版聚焦 Python/FastAPI。候选审计信号来自确定性的 Semgrep；AI 是可选的“证据解释与分流”能力，不能是漏洞 Evidence 的独立来源。默认不执行目标代码、不上传代码、不修改目标目录；Scanner、模型与遥测分别治理网络权限。

### 目标

1. 以交互和非交互两种入口审计本地目录，并支持终端与 JSON 输出。
2. 将 Semgrep 输出规范化为统一的 `Finding`、`Evidence`、Evidence Level 和 Finding State。
3. 让每条结果具备文件、行号、规则 ID、扫描器、支持/反向证据和验证状态。
4. 支持预算、范围、忽略规则、覆盖状态和清晰的自动化退出码。
5. 保持现有 Web API、`/api/v1/agent-tasks/*` 和双存储模型不变。

SARIF、baseline、多 Scanner 和正式 CI 门禁属于产品验证后的候选扩展，不是 MVP 承诺。

### 非目标

- 不将现有 Web API 包装成“远程 CLI”；初版不要求启动 FastAPI、PostgreSQL、Redis 或 Docker Compose。
- 不在第一版自动克隆 URL、扫描 Git 历史、安装项目依赖，或执行项目测试。
- 不让模型生成或执行 shell 命令、PoC、补丁或网络请求。
- 不改变生产 ReAct 路径，亦不把 CLI 的运行结果写入产品数据库。
- 不以 CLI 为由新建第二个 Python 包根；代码仍在 `backend/app/` 下。

## 2. 当前仓库基础与设计约束

| 已有能力 | 可复用方式 | CLI 需要补齐的部分 |
| --- | --- | --- |
| `AuditRequest`、`Finding`、`Evidence`、`AuditReport`、`RunBudget` | 作为 CLI 的唯一核心数据契约 | 以向后兼容默认值补充 Evidence Level/Finding State，并新增 `ReasoningArtifact`；不得复制 CLI 专用 Finding |
| LangGraph 审计图与 `AuditRunner` | 作为审计状态机和取消/预算边界 | 增加受治理的静态扫描阶段；当前图仅含启发式扫描 |
| `AgentRuntime` / `ToolRegistry` / `PermissionPolicy` | 作为模型、工具和执行权限的装配点 | 新增只读的扫描器工具适配器 |
| 老的 Semgrep/Bandit/Gitleaks 工具 | 作为 JSON 解析与规则选择的参考 | 不直接复用其拼接 `command: str` 的执行路径 |
| `SandboxPolicy` | 作为“默认不执行、禁止原始 shell”的安全基线 | 专用扫描器 runner：仅固定 argv 模板、无网络默认值 |
| `Settings` | 作为环境变量、LLM 与运行时配置来源 | CLI 参数优先级和审计 profile 的解析 |

相关实现见：

- `backend/app/services/agent/domain/`
- `backend/app/services/agent/graph/`
- `backend/app/services/agent/harness/`
- `backend/app/services/agent/tooling/`
- `backend/app/services/agent/sandbox/`
- `backend/app/services/agent/tools/external_tools.py`

**重要现状：** 新 Graph 默认使用 `FakeLLM` 和本地启发式规则，`verification_status` 默认是 `NOT_RUN`。旧 `external_tools.py` 虽能调用真实扫描器，但依赖 Docker 和字符串命令，并且 Semgrep 为下载规则使用网络。因此它不能原样作为 CLI 的信任边界；其扫描结果格式和规则知识可以迁移，执行机制必须替换为受控适配器。

现有领域模型没有完整表达 E0–E3 和独立的 `ReasoningArtifact`。CLI-1 应优先扩展共享 domain，并为旧 API/持久化映射提供默认值；不能通过另建一套 CLI Finding 来规避兼容工作，也不能继续让一个模糊的 `confidence` 字段同时代表 Scanner 可信度、推理可信度和验证强度。

## 3. 用户体验与命令契约

### 3.1 首批命令

```text
deepaudit [PATH]                 # Interactive Mode
deepaudit audit <PATH> [options]
deepaudit doctor
```

交互模式中的 `/audit` 和非交互 `audit` 命令必须调用同一个 `AuditApplication`。非交互最小示例：

```bash
cd backend
uv run deepaudit audit ../target-project \
  --format json \
  --out ./artifacts/audit.json
```

推荐选项：

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `--include PATH` / `--exclude PATH` | 相对工作区的范围过滤，可重复 | 默认排除构建产物、依赖目录和 VCS 元数据 |
| `--severity info|low|medium|high|critical` | 报告和退出码的关注下限 | `medium` |
| `--format terminal|json` | 输出格式 | `terminal` |
| `--out FILE` | 显式写入报告；使用验证后的工作区外路径 | 无，默认仅 stdout/终端 |
| `--strict` | 任一启用扫描器失败即视为失败 | 关闭；失败时产生 partial 结果 |
| `--max-files`、`--max-duration`、`--max-tokens` | 覆盖 `RunBudget` 上限 | 使用 MVP 默认预算 |
| `--no-color`、`--quiet` | 适配终端与脚本消费 | 关闭 |

`doctor` 只检查版本、可执行文件、规则缓存、配置和权限；它不扫描目标项目，也不上传诊断数据。

### 3.2 Profile（Post-MVP）

| Profile | 设计用途 | 启用能力 | 明确不做 |
| --- | --- | --- | --- |
| `quick` | 本地快速反馈 | 文件清单、启发式、已安装的轻量规则 | LLM、网络、执行、深层依赖分析 |
| `balanced` | 开发者默认审计 | Semgrep、语言适配扫描器、密钥扫描、lockfile 审计 | LLM、网络、执行 |
| `ci` | 稳定门禁 | 锁定规则版本、SARIF/JSON、baseline、严格预算 | AI、网络、非确定性输出 |
| `deep` | 人工发起的深入分析 | `balanced` + 可选 `--ai` 上下文分析 | 自动验证；仍须另行明确批准 |

MVP 不实现 profile 矩阵。后续实现时，Scanner 网络、模型 egress 和遥测不能合并成一个 `--allow-network` 开关；模型调用需要 provider/endpoint/data scope 的 session consent，Scanner 仍使用固定或本地规则。

### 3.3 退出码

| 退出码 | 含义 | 自动化建议 |
| --- | --- | --- |
| `0` | 审计完整，未发现达到阈值的新问题 | 成功 |
| `1` | 审计完整，发现达到阈值的新问题 | 由调用者决定告警/失败 |
| `2` | 参数、路径或配置无效 | 视为配置错误 |
| `3` | `--strict` 下扫描器不可用/失败，或运行环境不满足要求 | 视为运行失败 |
| `4` | 非严格模式下得到 partial 结果（预算耗尽、扫描器失败或覆盖不完整） | 不得当作 clean |
| `5` | 未处理的内部错误 | 视为运行失败并保留已脱敏日志 |

当既有高危问题又有扫描失败时，以完整性错误优先：严格模式返回 `3`，非严格模式返回 `4`。JSON 报告始终带有完整状态，不能仅依赖退出码判断结果可信度。

## 4. 总体架构

```text
┌──────────────┐     Pydantic 命令请求      ┌───────────────────────┐
│ Typer CLI    │ ─────────────────────────▶ │ CLI Application Service│
│ 参数/显示/码 │                             │ scope / profile / run  │
└──────┬───────┘                             └───────────┬───────────┘
       │                                                 │
       │ 交互会话/终端/JSON                               ▼
       │                                     ┌─────────────────────────┐
       └──────────────────────────────────── │ AgentRuntime + AuditRunner│
                                             │ LangGraph 是唯一状态机   │
                                             └───────────┬─────────────┘
                                                         │
                       ┌─────────────────────────────────┼─────────────────────────────┐
                       ▼                                 ▼                             ▼
             文件清单/路径防护                   ToolRegistry                    Artifact Store
             限制在工作区内                   ┌───────┴────────┐              原始输出/报告
                                             静态扫描器适配器   ExplainApplication→LLM
                                             固定 argv/超时    仅交互解释
                                                        │
                                                        ▼
                                  CandidateFinding → Finding → 去重/排序/报告
```

依赖方向必须是单向的：CLI 依赖 Application/Domain；Application 依赖协议；扫描器、LLM、文件系统和呈现器实现协议。Graph 节点不能导入 Typer、`subprocess` 或具体扫描器 SDK。

### 4.1 建议目录布局

```text
backend/app/
├── cli/
│   ├── main.py                 # Typer 根命令、统一异常映射
│   ├── commands/
│   │   ├── audit.py            # audit 参数 → 应用请求
│   │   └── doctor.py
│   ├── interactive/
│   │   ├── repl.py
│   │   ├── session.py
│   │   └── intents.py          # slash command + 有限本地意图
│   ├── presenters/             # terminal / json
│   └── exit_codes.py
└── services/agent/
    ├── application/
    │   ├── local_audit.py      # CLI 和将来的 API 共用的编排入口
    │   ├── explain.py          # Finding → ReasoningArtifact
    │   └── session.py
    ├── domain/
    │   └── cli.py              # CLI 请求、Session、ReasoningArtifact、报告信封
    ├── graph/
    │   └── nodes/              # 新的静态扫描节点及其状态增量
    ├── tooling/
    │   ├── scanners.py         # ScannerProtocol + ToolProtocol 适配器
    │   └── normalizers.py      # 扫描器 JSON → CandidateFinding
    └── persistence/
        └── artifact_store.py   # 复用文件制 ArtifactStore，限定 run 目录
```

在 `backend/pyproject.toml` 中登记入口：

```toml
[project.scripts]
deepaudit = "app.cli.main:app"
```

Typer 是 CLI 依赖，应作为 `cli` optional extra 加入，而不是让 Web 服务启动时必须安装 CLI 展示库。运行时环境参数仍由 `app.core.config.Settings` 提供。以后若提供项目级审计配置文件，它只描述审计范围/规则/profile，解析后形成 `AuditRequest`；它不是第二套进程 Settings。

## 5. 审计流水线与数据流

### 5.1 状态机扩展

现有图已经拥有 `validate → ingest → manifest → plan → analyze → aggregate → dedupe → prioritize → report` 骨架。CLI 应在此图中增加受治理的静态扫描阶段，而不是另写一个 `for file in files` 的平行审计循环：

```text
validate_request
  → ingest_repository
  → build_manifest
  → run_static_scanner         # CLI-1：注入的固定 Semgrep adapter
  → plan_audit                 # CLI-1 scanner-only 时为空计划
  → aggregate_findings
  → deduplicate_findings
  → prioritize_findings
  → generate_report
```

`run_static_scanners` 返回 `CandidateFinding`、覆盖范围、工具版本、失败详情和工件引用；不得直接构造前端 JSON。`normalize_scanner_results` 负责字段校验、严重度映射、相对路径化、行号纠正、结果上限和秘密脱敏。所有节点都返回 state delta；原始扫描器输出放入 `ArtifactRef`，不要塞进 LangGraph checkpoint。

AI 解释不属于非交互 audit graph。交互用户选择 Finding 后，由独立的 `ExplainApplication` 读取该 Finding、最小必要上下文和需要检查的反向证据，输出 Pydantic `ReasoningArtifact`。这样 canonical audit result 不依赖模型，模型也不能凭自己的文字创建正式 Finding、提升 Evidence Level，或提高 Finding/Verification 状态。

### 5.2 数据生命周期

1. CLI 将 `PATH` 解析为绝对根路径，只作为 `GraphRuntime.workspace_root` 注入；领域对象中只保存相对路径。
2. `build_manifest` 生成受 include/exclude、大小、二进制和生成文件规则约束的 `RepositoryManifest`。
3. 计划节点根据语言、lockfile、入口路径和预算选择扫描器及顺序。
4. 每个 Scanner Adapter 在临时只读副本或受限工作区内运行固定 argv，使用清理后的环境、受控 cwd/timeout/output limit，产生原始 JSON 工件。MVP 工件进入 OS 临时目录并在运行结束后销毁。
5. 规范化器将可用结果转换为 `CandidateFinding`；无位置且无可审计证据的结果被记录为工具事件，而非安全发现。
6. 聚合器生成 canonical `Finding`，初始为 E0/candidate，以稳定 fingerprint 去重并保存多来源 Evidence。
7. 排序器计算风险，但不把模型自信度当作验证强度。
8. 呈现器从报告信封读数据，JSON 与终端结果来自同一个 canonical report。
9. 交互用户选择 Finding 并请求解释后，独立 ExplainApplication 才可在 session consent 下调用模型，输出 `ReasoningArtifact`；最多把 Finding 标记为 triaged。

### 5.3 Finding 的证据和去重

每个 Finding 至少应保留：标题、严重度、相对文件路径、行范围、`analyzer`、`rule_id`、`evidence_level`、`finding_state`、`verification_status` 和一条非模型生成的 Evidence。建议 fingerprint 输入：

```text
schema-version + normalized-rule-id + cwe + relative-path
+ normalized-line-range + vulnerable-span-hash
```

同一 fingerprint 的多工具结果合并为一条 Finding，Evidence 追加且保留来源；不同规则但位于同一行的结果不能仅凭标题合并。风险排序应同时考虑严重度、Evidence Level、确定性证据质量、多扫描器一致性和可达性（若有）。不能把 LLM 的自信程度直接当作漏洞严重度或验证状态。

Evidence 等级与状态机遵循 PRD：

```text
E0 Signal → E1 Contextualized → E2 Corroborated → E3 Verified
candidate → triaged → corroborated → verified
```

MVP 的 Semgrep 命中从 E0/candidate 开始。模型只能产生 `ReasoningArtifact` 并协助整理 E1 上下文；E2 需要独立确定性佐证，E3 必须有 VerificationResult。

## 6. 扫描器与 AI 的治理模型

### 6.1 ScannerProtocol

新增一个窄协议，而非向图节点暴露 `subprocess`：

```python
class ScannerProtocol(Protocol):
    name: str
    async def scan(self, request: ScannerRequest) -> ScannerResult: ...
```

`ScannerRequest` 至少包含已验证的工作区引用、相对 include/exclude、固定/本地规则引用、最大结果数和超时。`ScannerResult` 包含状态、工具版本、规则 hash、结构化结果或 `ArtifactRef`、覆盖文件数、标准错误摘要和时长。它随后适配为既有 `ToolProtocol`，从而仍受 allowlist、超时、预算、追踪和事件机制控制。

执行器必须满足：

- argv 以列表传递；禁止 `shell=True`、字符串拼接和模型提供的命令片段；
- 工具名、二进制路径、参数和规则目录均来自应用 allowlist；记录版本并防止 PATH 劫持；
- MVP 不接受远程规则 URL；使用随包、本地或已验证缓存规则，记录规则版本/哈希；
- 设置独立临时目录、受控 cwd、超时、输出上限、环境变量 allowlist 和只读工作区；不继承无关 API key、云凭据或代理变量；
- 来自文件名、规则消息、stdout/stderr 的 ANSI、OSC 和其他控制字符在终端渲染前清理；
- 未来扫描秘密时，终端、JSON 和日志默认只保留掩码上下文或 fingerprint，绝不回显 token 本体；
- 工具未安装、解析错误、超时都是显式 ScannerResult，不应被悄悄当作“无发现”。

固定 argv 并不等于 OS 级网络隔离。如果未部署系统 sandbox，只能承诺 DeepAudit 不为 Scanner 主动发起网络请求和拒绝远程规则，不能声称本地 Scanner 进程在技术上绝对无网络。

MVP 只实现 Semgrep（固定/本地规则）。Bandit、Gitleaks、OSV/lockfile 审计、`npm audit`、Safety、TruffleHog 和远程规则下载均为产品验证后的候选扩展。

### 6.2 交互式 AI 解释

MVP 的非交互 `audit` 命令永不调用模型。AI 只在交互用户请求解释某条 Finding 时启用，并遵循以下规则：

- 首次模型调用前展示 provider、endpoint 和数据范围并取得本次 session consent；模型 egress 与 Scanner 网络是不同权限。
- 仅发送命中的局部代码、最小上下文和已脱敏工具输出；在构建请求前排除密钥、凭据文件和无关敏感上下文；遵守 token、文件、调用和时长预算。
- 代码、注释、README 和扫描器输出均视为不可信数据，不能改变系统提示、工具权限或策略。
- 模型以 JSON schema 返回 `ReasoningArtifact`（evidence summary、preconditions、impact、limitations、recommendation）；解析失败时保留静态结果并记录 AI 失败，不重试到绕过预算。
- 模型不能调用执行工具、不能扩大扫描范围、不能创建网络请求；`PermissionPolicy.allow_execution` 保持 false。
- 模型自身陈述不计为独立 Evidence，不能创建正式 Finding，不能把 Finding 推进到 corroborated/verified。
- 报告清楚标记模型、提示模板版本、上下文摘要哈希、Finding State 和 `verification_status=not_run`。

## 7. 安全与隐私边界

### 7.1 输入与文件系统

- 初版只接受本地目录；拒绝 URL、归档包、设备文件、FIFO、socket 与目录遍历。
- 目标根目录与每个读取文件都要 `resolve()`，并验证仍在根目录内；默认跳过指向根目录外的符号链接。
- 默认跳过 `.git`、依赖缓存、构建产物、虚拟环境、二进制、大文件与生成目录；用户扩展范围不能绕开根目录检查。
- 目标仓库永不写入。无 `--out` 时结果只输出到终端/stdout；显式输出只能写到验证后的工作区外路径，并使用原子写入、拒绝 symlink 覆盖。不能在目标仓库创建 `.deepaudit` 或扫描缓存。
- 大型原始输出、代码片段和错误日志由 Artifact Store 管理，使用最小权限、大小限制与路径 jail。MVP 默认使用 OS 临时目录并在运行结束后销毁，不持久化未脱敏原始工件。

### 7.2 进程、网络与执行

静态扫描器与“执行目标项目”是不同能力。静态扫描器只运行 allowlisted 的审计二进制，不运行代码库脚本；验证目标项目则属于未来的显式 `verify` 命令。后者必须遵循现有 ADR-003：隔离 worker、非 root、无网络、只读根、无 Docker socket、资源限制、审批门和高层 action allowlist。

Scanner、模型和遥测具有独立 egress 策略：Scanner 使用固定/本地规则且不主动联网；模型默认关闭，在 provider/endpoint/data scope 获得 session consent 后才能发起请求；产品遥测在 MVP 中不存在。一个总的 `allow_network` 开关不能表达这些边界。

因此，第一版绝不提供下列“方便选项”：`--command`、`--run-tests`、`--install`、`--docker-socket` 或由 LLM 传入的工具参数。安全拒绝不是可重试错误。

### 7.3 配置、日志和秘密

- API key 和代理配置只从现有 Settings/环境读取，不写入报告、事件、基线或 shell 历史。
- 默认日志和进度使用 `stderr`，可读报告使用 `stdout`；JSON 模式的 `stdout` 只能包含可直接解析的报告。
- Scanner 子进程只接收环境 allowlist，终端 presenter 清理不可信控制字符。
- Gitleaks 类发现使用指纹、规则名和掩码片段；原始秘密不落盘。若未来提供受控证据保留，必须单独 `--store-sensitive-evidence` 并提示风险。
- 每份报告包含工具版本、规则 hash、profile、预算消耗、覆盖率、跳过原因与 partial 原因，以便复现。

## 8. 输出、兼容性与 Post-MVP 基线

### 8.1 报告信封

JSON 输出应有一个稳定、版本化信封：

```json
{
  "schema_version": "1.0",
  "audit": {"id": "aud_…", "status": "completed", "mode": "non_interactive"},
  "scope": {"root_display": "../target-project", "files_selected": 42},
  "coverage": {"semgrep": "completed"},
  "budget": {"files_analyzed": 42, "max_files": 500},
  "findings": [],
  "errors": [],
  "artifacts": []
}
```

`Finding` 仍使用已有 Pydantic 领域模型并补齐 Evidence Level/State；信封承载 CLI 特有的覆盖与输出元数据。MVP 只实现 JSON 和 terminal。Post-MVP 的 SARIF 2.1.0 必须由同一 Finding 映射，不能拥有另一套漏洞模型；没有位置的依赖发现应映射到 lockfile 或项目级位置，不能伪造源码行号。

### 8.2 Baseline（Post-MVP）

baseline 文件必须是可审查、可重建的 JSON：存储 schema 版本、生成时间、项目标识、规则版本、finding fingerprint、状态、接受理由和可选到期日。它不存储秘密代码片段或模型原文。

处理逻辑：

1. 先扫描和规范化，再计算 fingerprint；
2. 与 baseline 匹配的发现保留在报告中，状态为 `existing` 或 `accepted`；
3. 只有未匹配的、达到阈值的发现影响 `exit 1`；
4. 过期 baseline 项重回“新发现”；
5. `baseline update` 必须从完整 JSON 报告构建，拒绝 partial 报告，防止把覆盖缺口固化为已接受风险。

### 8.3 与现有产品的兼容性

CLI 不调用也不修改 `/api/v1/agent-tasks/*`。它可复用领域对象和 Application service，但默认使用本地 Artifact Store/内存业务 store；不会创建数据库中的 `AgentTask` 或 `AuditIssue` 行。将来若要把 CLI 结果上传到服务端，应新增有版本的导入 API，并把本地报告视作不可信输入再次验证。

## 9. 实施路线与交付门槛

### Phase CLI-0：Feasibility Gate

- 在干净 Python 3.12 环境验证 Agent Runtime 可在不启动 Web、数据库、Redis、Chroma 和 Docker 的情况下导入与运行。
- 决定 Semgrep 二进制与固定/本地规则的供应方式，完成离线 fixture 扫描。
- 生成最小 canonical JSON Finding/Evidence，测量安装体积、启动、扫描时间和内存。
- 验证 Scanner 环境 allowlist、二进制解析、输出上限和工作区只读边界。
- 确认 CLI-0 至 CLI-2 不调用云模型；首个 provider、endpoint 信任策略和 session consent 在进入 CLI-3 前单独过 Gate。

验收：真实 Semgrep 纵切在无 Web/DB 和无远程规则下载环境下成立；否则停止 TUI 开发并先解决运行时边界。

### Phase CLI-1：Evidence Core / Real Audit Vertical Slice

- 实现 ScannerProtocol、Semgrep argv-only adapter 与规范化器。
- 在 Graph 中引入静态扫描节点及覆盖/partial 状态，复用 AuditRunner/Harness 的预算和事件。
- 引入 Evidence Level、Finding State、反向证据容器与 ReasoningArtifact 边界。
- 提供 terminal、JSON；`doctor` 报告二进制、规则和运行环境可用性。
- 建立原始 Semgrep 对照任务、正/负 fixture 和安全回归。

验收：fixture 项目可产生稳定 E0/candidate Finding；扫描器不可用不会被误报为 clean；目标目录 hash 不变；环境凭据不传给 Scanner；终端控制字符被清理。

### Phase CLI-2：Interactive Triage

- 增加 Typer/Rich/prompt_toolkit、Session、slash commands 和有限的本地自然语言意图映射。
- Interactive 与 Command Mode 共享同一个 `AuditApplication`。
- 完成 Finding 选择、稳定别名、Ctrl+C、状态和 graceful shutdown。

验收：`deepaudit audit .` 与交互 `/audit` 产生相同 fingerprint；无模型时核心交互仍可用。

### Phase CLI-3：AI Explanation / Product Validation

- 接一个现有 LLM provider，增加 endpoint/data consent、秘密过滤、ReasoningArtifact 和预算可观测性。
- 增加提示注入、模型超时、schema 无效、反向证据和状态提升红线测试。
- 对比原始 Semgrep 与 DeepAudit 的正确 triage 时间、事实准确率和无证据断言率。

验收：非交互 audit 和未授权 session 完全没有模型调用；模型故障不抹除静态发现；模型不能创建正式 Finding 或推进到 corroborated/verified；达到 PRD 质量护栏。

### Phase CLI-4：产品验证后选择

根据用户反馈选择正式 CI/SARIF/baseline、多 Scanner 或 Fix proposal。验证能力仅在独立 ADR、worker 实现与审批 UX 完成后考虑，不能作为附带功能。

## 10. 测试、质量与可观测性

### 测试分层

| 层级 | 覆盖内容 | 依赖 |
| --- | --- | --- |
| 单元测试 | 参数解析、路径 jail、fingerprint、Evidence Level/State、severity、脱敏、控制字符清理 | fake scanner / fake LLM |
| Graph 测试 | 节点状态增量、预算、partial、取消、工具 allowlist | fixture 文件与 MockToolAdapter |
| CLI 集成 | exit code、stdout/stderr 分离、显式输出路径、golden JSON | 临时目录与假二进制 |
| 扫描器契约测试 | 固定版本 Semgrep 的 JSON 解析和规则 hash | 可选容器/CI job，无远程规则默认 |
| 安全回归 | shell 注入、符号链接逃逸、环境凭据继承、终端注入、超大输出、原子输出 | 受控 fixture |
| Evals | 原始 Semgrep 对照、已知正/负样本、反向证据、事实引用与误报回归 | versioned corpus + 安全标注 |

默认测试不得调用付费模型、外网、Docker socket 或写入真实仓库。外部二进制扫描器测试应被清晰标为 integration，并可在开发机缺失时跳过；核心规范化和安全边界不能依赖它们。

### 观测字段

每次运行记录 audit ID、扫描器版本/规则 hash、覆盖文件数、跳过原因、耗时、工具调用/模型 token 预算、Evidence Level/State 分布、finding 数、partial 原因和输出工件 hash。沿用现有 tracing redaction 规则，绝不记录完整源代码、秘密或 API key。MVP 不启用静默产品遥测。

## 11. 风险、取舍与待决问题

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 旧扫描工具依赖 Docker 与字符串命令 | 破坏本地可用性和 shell 安全边界 | 迁移解析逻辑，重写 argv-only 执行 adapter |
| 远程 Semgrep 规则需要网络 | 不可重现并可能泄露元数据 | 默认本地/锁定规则；网络需显式开启并记录 hash |
| CLI 对大仓库耗时或成本不可控 | 本地与自动化运行不稳定 | 文件、时间、工具、token 上限；明确 partial 状态 |
| 密钥检测输出泄露真实秘密 | 高风险 | 默认脱敏、最小工件、报告 snapshot 安全测试 |
| LLM 误报与提示注入 | 结果可信度下降 | AI 可选、结构化输出、只做补充、证据门槛与预算 |
| Graph 与 CLI 两套流程漂移 | 长期维护成本高 | Graph 是唯一状态机；CLI 不直接实现审计循环 |
| baseline 掩盖新增风险 | CI 假绿 | 指纹/version、到期、partial 禁止更新、保留原始发现 |
| Scanner 继承开发者高权限环境 | 凭据泄露或越权访问 | 环境 allowlist、固定二进制、受控 cwd/timeout/output；不夸大未实现隔离 |
| 恶意仓库输出攻击终端 | ANSI/OSC 注入或欺骗展示 | presenter 统一清理控制字符，快照与安全回归测试 |
| 交互体验没有改善安全判断 | 产品成为 Semgrep 彩色壳 | 与原始 Semgrep 对照正确 triage 时间、事实准确率和无证据断言率 |

已决项和仍需确认的产品选择：

1. **已决：** Semgrep 不并入核心 CLI 依赖；CLI-1 使用独立工具环境或显式本地路径并固定 `1.173.0`，官方规则随 DeepAudit 版本化发布并校验 hash，禁止远程规则 URL。
2. CLI 是否允许把未脱敏原始工件保存在用户本机？推荐初版不允许。
3. 首个模型 provider、允许的 endpoint、数据 consent 和安全标注集由谁维护？进入 CLI-3 前必须决定。
4. 正式 CI/SARIF/baseline、更多 Scanner 和 Fix proposal 的优先级由产品验证决定。

## 12. 推荐的首个可实施切片

先做 **CLI-0 Feasibility Gate**：

```text
干净 Python 环境
  → 不启动 Web/DB 导入 Agent Runtime
  → 固定/本地 Semgrep 规则离线扫描 fixture
  → canonical E0/candidate Finding JSON
  → 验证环境清理、只读和资源边界
```

通过后实现 CLI-1：路径与输出保护、Python/FastAPI 文件清单、一个 argv-only Semgrep adapter、Evidence Level/State、JSON/终端呈现、严格退出码和完整测试。暂不接 LLM、Docker、Gitleaks、OSV、SARIF、baseline 和验证。

这个 Gate 最早验证三件最关键的事：CLI 是否能轻量复用现有 Runtime、真实 Scanner 能否离线且受治理地运行、结果模型是否能区分信号与验证。通过后才投资交互界面和 AI；失败也只会留在 `cli-agent` 实验分支，不影响 `main`。
