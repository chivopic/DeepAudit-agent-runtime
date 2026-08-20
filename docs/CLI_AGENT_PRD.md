# DeepAudit CLI & Interactive Security Agent PRD

> 文档状态：Draft v0.2（已完成第一轮对抗性审查）  
> 产品阶段：MVP Proposal  
> 日期：2026-08-19  
> 开发分支：`cli-agent`  
> 关联技术设计：[`CLI_AGENT_DESIGN.md`](./CLI_AGENT_DESIGN.md)  
> Gate 0 记录：[`CLI_AGENT_GATE0.md`](./CLI_AGENT_GATE0.md)  
> CLI-1 实现记录：[`CLI_AGENT_CLI1.md`](./CLI_AGENT_CLI1.md)

## 1. 产品定义

DeepAudit CLI 的长期方向是成为面向普通开发者的、在代码仓库内使用的交互式 AI Security Agent。用户进入项目后运行 `deepaudit`，可以用自然语言发起安全审计、查看有证据支持的安全发现，并围绕某条发现继续提问。

MVP 刻意收窄为 **Python/FastAPI 项目的交互式静态发现分流工具**：Semgrep 提供候选信号，DeepAudit 将信号组织为可追溯 Evidence，并帮助开发者更快作出正确的安全判断。MVP 不承诺发现所有漏洞，也不把 Scanner 命中描述为已确认漏洞。

DeepAudit 同时提供确定性的命令模式，用于本地脚本和机器可读导出：

```bash
deepaudit audit . --format json
```

两个模式共用同一审计内核、Finding、Evidence、权限和预算，不形成两套审计逻辑。

### 1.1 一句话定位

> Talk to a security audit grounded in real code evidence.

### 1.2 产品原则

1. **目标上 Correct Decisions First**：产品价值以正确安全判断和分流效率衡量，而不是对话次数或 Finding 数量。
2. **体验上 Agent First**：用户面对一个统一的 DeepAudit，而不是多个内部 Agent。
3. **判断上 Evidence First**：严重发现必须能指向代码或扫描器证据，并明确证据强度与反向证据。
4. **权限上 Read-only by Default**：默认不修改代码、不执行项目；Scanner、模型和遥测分别治理网络权限。
5. **自动化上 Deterministic Core**：路径、扫描、去重、严重度、报告和退出码不交给 LLM 决定。
6. **能力上 Graceful Degradation**：没有模型或模型失败时，静态审计仍然可用。

## 2. 背景与机会

传统静态扫描器常一次产生大量缺少上下文的结果。普通开发者需要自行理解规则、判断误报、追踪调用关系并寻找修复方法。通用 Coding Agent 虽然可以读写代码，但通常缺少 Finding、Evidence、CWE、验证状态、扫描器、权限和沙箱等安全领域边界。

DeepAudit 已经具备 Agent Runtime、领域模型、工具治理、预算、上下文、报告和沙箱基础。当前缺少的是一个开发者可以直接在终端使用的产品入口，以及围绕审计结果持续交互的会话体验。

机会是把二者组合起来：

```text
静态扫描器发现信号
        ↓
Evidence 形成可信锚点
        ↓
AI 解释上下文、可达性和影响
        ↓
开发者通过对话理解并处理问题
```

## 3. 目标用户与核心任务

### 3.1 第一目标用户

- 使用 Python/FastAPI 的后端和全栈开发者；
- 使用 FastAPI 构建 AI 应用的独立开发者；
- 维护 Python Web 项目的开源项目维护者；
- 没有专职安全团队的小型 Python 研发团队。

他们会使用终端和 Git，但不应被要求理解 SAST、SARIF、taint analysis、CWE 或 scanner orchestration。

### 3.2 Jobs to Be Done

1. 当我完成一个功能或接手陌生项目时，我想快速知道最值得关注的安全问题。
2. 当工具报告漏洞时，我想知道它为什么成立、具体代码在哪里、影响是什么。
3. 当我不熟悉安全术语时，我希望获得面向开发者的解释和下一步建议。
4. 当我要把结果交给脚本或其他工具时，我需要稳定的机器可读结果和明确退出码。

MVP 验证成功后再扩展到其他语言和正式 CI 门禁；Semgrep 可以扫描更多语言不等于 DeepAudit 对这些语言承诺同等质量。

### 3.3 暂非目标用户

- 需要企业级团队、权限、合规和集中仪表盘的组织；
- 需要自动化渗透测试或对公网目标进行动态攻击的人员；
- 需要一次性扫描数百万行单体仓库的企业平台团队。

这些需求继续由 Web 产品或后续版本承接。

## 4. MVP 目标与边界

### 4.1 MVP 要验证的假设

> 对 Python/FastAPI 的 Semgrep 候选发现，DeepAudit 的 Evidence 组织与交互解释能否显著降低开发者完成正确 triage 的时间，同时不增加无证据安全结论和错误的“已验证”判断？

“用户愿意对话”是体验信号，不是产品价值成立的充分条件。MVP 必须与原始 Semgrep 输出进行对照评估。

### 4.2 MVP 最小闭环

```text
cd project
    ↓
deepaudit
    ↓
> audit this repository
    ↓
Semgrep 扫描
    ↓
Finding + Evidence
    ↓
> explain FND-001
    ↓
基于证据的安全解释
```

### 4.3 MVP 包含

- `deepaudit` 交互模式；
- `deepaudit audit [PATH]` 命令模式；
- 工作区识别、文件清单和安全路径限制；
- 一个真实的 argv-only Semgrep Scanner Adapter；
- canonical `Finding` / `Evidence`、证据等级和 Finding 状态机；
- 终端和 JSON 输出；
- Finding 列表、稳定展示 ID 和详情；
- 基于 Finding/Evidence/局部代码的 AI 解释；
- 无 AI 配置时的确定性结果展示；
- `deepaudit doctor` 环境检查；
- 清晰的审计状态、partial 状态和退出码；
- Scanner 二进制/规则来源验证、环境清理、输出限制和终端控制字符清理。

### 4.4 MVP 不包含

- 自动修改代码或应用 patch；
- PoC、项目测试、项目依赖安装或动态漏洞验证；
- Gitleaks、OSV、Bandit、CodeQL、MCP；
- 正式 CI gate、SARIF、baseline、Git diff-only；
- Git URL 克隆、压缩包或远程仓库；
- 云端账户、登录、团队、同步和产品遥测；
- 独立 `deepaudit-runtime` 包或新产品仓库；
- Homebrew、winget、single binary 等发行工程。

## 5. 产品形态

### 5.1 Interactive Mode

默认入口：

```bash
cd my-project
deepaudit
```

启动后展示：

```text
╭──────────────────────────────────────╮
│ DeepAudit                            │
│ Evidence-backed security agent       │
│                                      │
│ workspace: my-project                │
╰──────────────────────────────────────╯

Detected: Python · FastAPI · SQLAlchemy
42 source files

Type /help for commands.

> _
```

用户发起审计：

```text
> 帮我审计这个项目

✓ Indexed 42 source files
✓ Semgrep completed
✓ Normalized and deduplicated findings

2 candidate issues found

HIGH    FND-001  Possible SQL Injection       app/api/users.py:84
MEDIUM  FND-002  TLS verification disabled    app/services/client.py:31

All findings are statically detected and not dynamically verified.
```

用户继续追问：

```text
> 第二个问题为什么成立？
```

内部必须先把“第二个问题”解析为当前结果视图中的 `FND-002`，然后执行结构化的 `explain_finding` 意图。不得仅依赖自由聊天记录猜测对象。

### 5.2 Command Mode

```bash
deepaudit audit . --format terminal
deepaudit audit . --format json --out ./artifacts/deepaudit.json
```

命令模式不得要求交互输入。MVP 的非交互审计不调用模型；需要 session consent 的 AI 解释只存在于交互模式。

### 5.3 Slash Commands

MVP 支持：

```text
/help
/audit [path]
/findings
/finding <id|index>
/status
/model
/clear
/exit
```

自然语言和 slash command 应落到同一个 Application Command。slash command 是确定性入口，自然语言是便捷入口。

## 6. 核心用户旅程

### Journey A：首次本地审计

1. 用户在项目根目录运行 `deepaudit`。
2. DeepAudit 验证当前目录，识别支持的源码并展示范围。
3. 用户输入“审计这个项目”或 `/audit`。
4. DeepAudit 展示阶段级进度，不展示思维链或内部 Agent 对话。
5. 扫描完成后列出有稳定 ID、严重度和代码位置的 Finding。
6. 用户可打开某条 Finding 或继续提问。

成功条件：用户在不理解 Semgrep/CWE 的情况下，可以定位并理解至少一条结果。

### Journey B：解释 Finding

1. 用户输入 `/finding FND-001` 或“解释第一个问题”。
2. DeepAudit读取 Finding、Evidence 和最小局部代码上下文。
3. 若已配置模型，首次向云模型发送代码前展示 provider、数据范围和本次会话授权提示。
4. DeepAudit 输出：证据、成立条件、可能影响、限制和建议。
5. 解释不能把 `verification_status=not_run` 描述为“已确认可利用”。

成功条件：解释中的文件、行号和代码事实与 Evidence 一致。

### Journey C：非交互导出

1. 用户或自动化脚本运行 `deepaudit audit . --format json --severity high`。
2. DeepAudit 不调用 LLM、不等待输入、不修改项目。
3. 完整审计且无达到阈值的新发现时退出 `0`。
4. 完整审计且有达到阈值的发现时退出 `1`。
5. 工具失败或覆盖不完整时不能报告为 clean。

MVP 的 JSON/退出码用于本地脚本和产品验证，不宣称已经具备稳定 CI 门禁所需的 baseline、SARIF、规则升级策略和历史发现治理。

## 7. 功能需求

### 7.1 工作区与范围

| ID | 需求 | 优先级 |
| --- | --- | --- |
| FR-001 | 默认以当前目录作为 workspace，也允许 `deepaudit [PATH]` 和 `deepaudit audit [PATH]` | P0 |
| FR-002 | 路径必须解析并限制在 workspace_root 内，拒绝 traversal 和根目录外符号链接 | P0 |
| FR-003 | 默认跳过 `.git`、`node_modules`、`dist`、`build`、`.venv`、`vendor`、二进制和超大文件 | P0 |
| FR-004 | 审计不得修改目标工作区 | P0 |
| FR-005 | 启动时展示 workspace、识别的语言/框架和选中文件数 | P1 |

### 7.2 审计与 Scanner

| ID | 需求 | 优先级 |
| --- | --- | --- |
| FR-010 | 通过 `ScannerProtocol` 调用 Semgrep，不允许图节点直接调用 subprocess | P0 |
| FR-011 | Scanner 参数必须是服务端固定 argv 模板，禁止 shell 字符串拼接 | P0 |
| FR-012 | Scanner 默认不访问网络和远程规则 URL；使用固定版本、本地规则或已验证缓存 | P0 |
| FR-013 | 保存扫描器版本、规则标识/hash、覆盖文件数、时长和状态 | P0 |
| FR-014 | 扫描器不可用、超时或解析失败必须产生 partial/error，而不是“0 findings” | P0 |
| FR-015 | `doctor` 能检查 Semgrep 二进制路径/版本、规则来源/hash、Python、配置和工作区权限 | P0 |
| FR-016 | Scanner 子进程使用清理后的环境、受控 cwd、超时、输出上限和明确的二进制解析策略 | P0 |
| FR-017 | 文件名、规则消息和工具输出中的 ANSI/OSC/控制字符在进入终端前必须转义或清理 | P0 |
| FR-018 | 工作区虽由用户选择，但文件名、源码、配置、规则消息和工具输出一律作为不可信输入处理 | P0 |

### 7.3 Finding 与 Evidence

| ID | 需求 | 优先级 |
| --- | --- | --- |
| FR-020 | 每条 Finding 至少包含稳定 ID、title、severity、location、analyzer、rule_id、Evidence、evidence_level、finding_state 和 verification_status | P0 |
| FR-021 | HIGH/CRITICAL Finding 不得在没有可定位、非模型生成的 Evidence 情况下进入正式列表 | P0 |
| FR-022 | 同一规则/路径/代码片段的重复结果通过 fingerprint 合并，并保留多个 Evidence | P0 |
| FR-023 | Finding 使用稳定内部 ID；`FND-001` 只是当前 run 内的稳定显示别名 | P0 |
| FR-024 | 所有 MVP Finding 默认 `verification_status=not_run` | P0 |
| FR-025 | AI 解释必须同时检查支持性证据和反向证据，如 sanitizer、参数化调用、授权守卫、死代码和不可达路径 | P0 |
| FR-026 | 模型输出保存为 `ReasoningArtifact`；模型自己的陈述不能单独提升 Evidence Level | P0 |

#### 7.3.1 Evidence Level

Evidence 表示“我们观察到了什么”，而不是直接表示“漏洞一定成立”。MVP 使用以下等级：

| Level | 名称 | 最低要求 | 能否称为已验证 |
| --- | --- | --- | --- |
| E0 | Signal | Scanner pattern/rule 命中 | 否 |
| E1 | Contextualized | 命中位置、局部代码、相关入口/调用和已检查的保护措施 | 否 |
| E2 | Corroborated | 确定性数据流或多个独立信号相互支持，并记录反向证据 | 否 |
| E3 | Verified | 受控执行、可重复测试或明确人工复核 | 是 |

MVP 的 Semgrep 输出从 E0 开始；AI 可以帮助整理 E1 的上下文和反向证据，但不能单独产生 E2/E3。MVP 不实现 E3。

#### 7.3.2 Finding State

```text
candidate → triaged → corroborated → verified
```

- `candidate`：Scanner 候选，尚未完成上下文审查；
- `triaged`：AI 或人工已阅读相关上下文，但仍未验证；
- `corroborated`：存在独立的确定性佐证；
- `verified`：必须对应 E3 和 VerificationResult。

Severity、Evidence Level、Finding State 与 Verification Status 是四个不同维度，不能通过单一 `confidence` 数字相互替代。

### 7.4 交互会话

| ID | 需求 | 优先级 |
| --- | --- | --- |
| FR-030 | Interactive 和 Command Mode 调用同一个 `AuditApplication` | P0 |
| FR-031 | Session 保存 workspace、audit_id、findings、selected_finding、budget 和用户授权 | P0 |
| FR-032 | Chat 历史不得无限发送给模型，由 Context Manager 管理 pinned/active/retrieved/summary | P0 |
| FR-033 | 支持 `/audit`、`/findings`、`/finding`、`/status`、`/help`、`/exit` | P0 |
| FR-034 | 自然语言意图必须解析为结构化 Application Command | P0 |
| FR-035 | Ctrl+C 首次取消当前操作，空闲时再次退出；不得留下损坏报告 | P1 |
| FR-036 | MVP 会话默认只在当前进程存在；不承诺跨进程 resume | P1 |
| FR-037 | 无模型授权时，核心自然语言意图只使用本地、有限规则映射；不确定时请求确认而不是猜测 | P0 |

### 7.5 AI 解释

| ID | 需求 | 优先级 |
| --- | --- | --- |
| FR-040 | AI 只读取选中 Finding、Evidence、局部代码和必要仓库上下文 | P0 |
| FR-041 | AI 输出必须通过结构化 schema，至少包含 evidence_summary、preconditions、impact、limitations、recommendation | P0 |
| FR-042 | AI 不得伪造 Evidence、修改代码、调用执行工具或提升 verification_status | P0 |
| FR-043 | 代码、注释、README 和扫描器输出全部作为不可信数据处理，不能改变权限和系统规则 | P0 |
| FR-044 | 首次云模型调用前提示 provider、目标 endpoint 和数据范围，并获取本次会话授权 | P0 |
| FR-045 | 无模型、拒绝授权或模型失败时，静态 Finding 仍可查看，并提供确定性 Evidence 摘要 | P0 |
| FR-046 | API key 只从现有 Settings/环境读取，不出现在日志、报告或会话记录中 | P0 |
| FR-047 | 模型输入构建前检测并排除密钥、凭据文件和与 Finding 无关的敏感上下文 | P0 |
| FR-048 | AI 只能把 Finding 从 candidate 标记为 triaged，不能单独产生 corroborated/verified | P0 |

### 7.6 输出与退出码

| ID | 需求 | 优先级 |
| --- | --- | --- |
| FR-050 | Terminal 和 JSON 必须由同一 canonical AuditResult 渲染 | P0 |
| FR-051 | JSON 输出包含 schema_version、audit status、scope、coverage、budget、findings 和 errors | P0 |
| FR-052 | `stdout` 用于结果，诊断与进度写入 `stderr`；JSON 模式不得混入人类日志 | P0 |
| FR-053 | 支持严重度阈值，默认 `medium` | P0 |
| FR-054 | 状态优先级为 internal/config/tool/partial 错误高于 findings gate | P0 |
| FR-055 | 默认不在 workspace 内创建 `.deepaudit` 或其他文件；无 `--out` 时结果仅输出到终端/stdout | P0 |
| FR-056 | 输出文件使用验证后的目标路径和原子写入，并拒绝 symlink 覆盖 | P0 |

退出码：

| Code | 含义 |
| --- | --- |
| `0` | 审计完整，没有达到阈值的发现 |
| `1` | 审计完整，存在达到阈值的发现 |
| `2` | 参数、路径或配置无效 |
| `3` | 必需 Scanner 或运行环境失败 |
| `4` | 审计为 partial，覆盖或预算不完整 |
| `5` | 未处理的内部错误 |

## 8. AI 与确定性能力边界

| 确定性能力 | AI 能力 |
| --- | --- |
| 路径校验、文件枚举、ignore | 仓库安全语义理解 |
| Scanner 调用与超时 | Finding 上下文解释 |
| 输出解析和严重度映射 | 前置条件与影响分析 |
| fingerprint、去重、排序 | 降低误报的建议性判断 |
| Evidence 和 verification 状态 | 面向开发者的修复建议 |
| 报告、退出码、预算、脱敏 | 自然语言意图识别 |

AI 的判断是 Evidence 的解释和补充。MVP 中静态 Scanner 是发现来源，LLM 不是自由扫描整个仓库的漏洞检测器。AI 产出 `ReasoningArtifact`，只有它引用的代码、Scanner 结果或确定性数据流可以成为 Evidence。

## 9. 安全与隐私要求

### 9.1 默认策略

```text
workspace read-only
scanner egress denied by policy
model egress disabled until explicit session consent
telemetry egress denied
project execution denied
dependency installation denied
arbitrary shell denied
filesystem write denied
secret output denied
```

固定 argv 只能防止命令拼接，不能单独形成 OS 级网络隔离。如果 MVP 尚未实现系统 sandbox，产品文案必须表述为“DeepAudit 不为 Scanner 主动发起网络请求，并拒绝远程规则”，不能声称本地进程的网络已被技术上完全阻断。

### 9.2 数据处理

- 报告和日志只保存相对路径，不泄露用户完整主目录；
- 发送给模型的代码限制在选中 Finding 的最小上下文；
- 不把完整仓库、完整聊天记录或原始扫描输出发送给模型；
- 默认不保存聊天记录；canonical audit result 可在用户指定位置保存；
- MVP 原始 Scanner 工件只进入 OS 临时目录，运行结束后销毁，不持久化未脱敏原始工件；
- 不进行静默遥测；未来增加遥测必须 opt-in 并单独说明；
- 秘密扫描能力进入后，默认只展示掩码和 fingerprint。
- Scanner 子进程不得继承无关的 API key、云凭据和代理环境变量；
- 终端展示前清理来自仓库和工具输出的控制字符，避免终端转义注入；
- 默认输出不落入目标 workspace，显式输出使用原子写入并拒绝符号链接目标。

### 9.3 产品措辞

未执行验证时允许使用：

```text
possible
evidence suggests
requires confirmation
not dynamically verified
```

禁止使用：

```text
confirmed exploitable
successfully exploited
verified vulnerability
```

除非存在对应 VerificationResult。

## 10. 非功能需求

| ID | 需求 | MVP 目标 |
| --- | --- | --- |
| NFR-001 | 启动速度 | 候选目标：参考开发机上进入 prompt P50 ≤ 2 秒；Gate 0 实测后确认 |
| NFR-002 | 首次有用反馈 | 候选目标：100 个受支持源码文件、规则已缓存时 P50 ≤ 60 秒；Gate 0 实测后确认 |
| NFR-003 | 确定性 | 相同代码、规则和工具版本产生稳定 Finding fingerprint |
| NFR-004 | 可恢复性 | Scanner/模型失败不损坏已有 Finding 或输出文件 |
| NFR-005 | 目标完整性 | 审计前后目标工作区文件内容 hash 不变 |
| NFR-006 | 离线能力 | 不启用 AI 时，DeepAudit 和规则解析不需要主动访问外部网络 |
| NFR-007 | 可测试性 | 默认测试不调用付费模型、外网、Docker socket 或真实用户仓库 |
| NFR-008 | 兼容性 | 第一阶段支持 macOS/Linux、Python 3.12；Windows 后续验证 |

## 11. 成功指标

MVP 不启用静默产品遥测，指标通过版本化测试集、可选用户测试和 issue/反馈收集。

### 11.1 产品指标

- 首次使用者能在 5 分钟内完成一次真实审计；
- 用户能从结果列表准确打开并解释一条 Finding；
- Interactive 与 Command Mode 对同一输入得到相同 canonical Finding；
- 至少 5 个外部 Python/FastAPI 项目完成端到端试用并收集反馈；
- 在同一版本化任务集上，对比“原始 Semgrep”与“DeepAudit”，候选目标为 median 正确 triage 时间降低至少 30%；
- 试用者在第二个项目中再次主动使用 explain，作为重复价值信号。

### 11.2 质量护栏

- 支持测试集中 100% Finding 具有合法工作区内位置和 Evidence；
- 0 条无 Evidence 的 HIGH/CRITICAL 正式 Finding；
- Scanner 失败被误报为 clean 的次数为 0；
- 目标工作区非授权写入次数为 0；
- API key、秘密和绝对主目录路径进入快照报告的次数为 0；
- AI 关闭或失败时，静态审计成功率不受影响；
- 由安全标注者评估的 AI 代码事实引用准确率候选目标 ≥ 90%；
- AI 无 Evidence 支持的安全断言率候选目标 ≤ 5%；
- AI 将未验证结果描述为 verified/confirmed 的次数为 0；
- 含 sanitizer、参数化查询、授权守卫和死代码的负样本进入 HIGH/CRITICAL 正式结果的回归必须可见并计入误报率。

## 12. 发布阶段

### Phase 0：Feasibility Gate

- 在干净 Python 3.12 环境验证当前 Runtime 可以在不启动 Web、数据库、Redis、Chroma 和 Docker 的情况下导入与运行；
- 决定 Semgrep 二进制和固定/本地规则的供应方式，并完成离线 fixture 扫描；
- 生成最小 canonical JSON Finding/Evidence；
- 测量安装体积、启动时间、首次扫描时间和内存；
- 验证 Scanner 环境清理、二进制解析、输出上限和工作区只读约束；
- 确认 Phase 0–2 不调用云模型；首个 provider、endpoint 信任策略和 session consent 在进入 Phase 3 前单独过 Gate。

该阶段没有通过时，不进入 TUI/REPL 实现。Fake Runtime 只作为后续测试夹具，不单独构成里程碑。

### Phase 1：Evidence Core / Real Audit Vertical Slice

- Semgrep ScannerProtocol adapter；
- 真实 Finding/Evidence、Evidence Level、Finding State 和反向证据容器；
- 终端和 JSON；
- `doctor`、状态和退出码；
- 路径、命令、终端注入、脱敏与输出安全测试；
- 原始 Semgrep 基准数据集和 triage 对照任务。

### Phase 2：Interactive Triage

- Typer/Rich/prompt_toolkit 产品入口；
- Session、slash command 和有限的本地自然语言意图；
- 选择 Finding；
- Interactive 与 Command Mode 共享同一 `AuditApplication`；
- Ctrl+C、状态、错误与 graceful shutdown。

### Phase 3：AI Explanation / Product Validation

- 一个模型 provider 接入；
- Context Manager、ReasoningArtifact 与结构化解释；
- endpoint/data consent、秘密过滤、模型失败和 prompt injection 测试；
- 与原始 Semgrep 的对照评估和安全标注者评审。

完成 Phase 3 且达到质量护栏后才构成对外 MVP。

### Phase 4：产品验证后扩展

根据用户反馈选择 Bandit/Gitleaks/OSV、正式 CI/SARIF/baseline 或 Fix proposal，不能默认同时进入下一阶段。

### Phase 5：Runtime Extraction 决策

CLI 使用需求稳定后，记录 Extraction ADR，决定是否提取 `deepaudit-runtime` 和建立独立 `deepaudit` 产品仓库。提取前不复制当前 Runtime。

## 13. 验收测试

MVP 发布必须满足：

1. `deepaudit` 可以在有效项目中进入交互模式，且不启动 Web/数据库服务。
2. 干净安装环境可以使用固定/本地规则离线完成 `deepaudit audit fixture/ --format json`，并通过真实 Semgrep 产生预期 Finding。
3. Interactive 中 `/audit` 与 Command Mode 使用同一 Application service，并产生相同 fingerprint。
4. `/finding FND-001` 能展示规则、文件、行号、Evidence Level、Finding State 和未验证状态。
5. “解释第二个问题”准确映射当前视图中的 Finding，不因列表重排引用错误对象。
6. 首次云模型调用明确告知 provider、endpoint 和发送范围；拒绝后不发送代码。
7. 模型超时、拒绝授权或 schema 错误时，静态 Finding 保留。
8. Semgrep 缺失、超时、输出损坏时不得返回 clean 状态。
9. traversal、根目录外 symlink、恶意规则参数和 shell metacharacter 测试均被拒绝。
10. 审计前后 fixture 工作区 hash 一致。
11. JSON stdout 能直接被 parser 消费，不混入进度文本。
12. 默认执行不为 Scanner 发起网络请求、不执行项目代码、不修改文件。
13. Scanner 子进程没有继承测试注入的 API key、云凭据和代理环境变量。
14. 恶意文件名、规则消息和 Scanner 输出不能向终端注入 ANSI/OSC 控制序列。
15. 模型不能把 E0/E1 结果升级为 corroborated/verified，模型输出只保存为 ReasoningArtifact。
16. 含参数化查询、sanitizer、授权守卫和死代码的负样本被纳入对照评估。
17. 原始 Semgrep 与 DeepAudit 的对照试验达到预设 triage 效率和事实准确率护栏。

## 14. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| CLI 和 Runtime 漂移 | 双模型、双逻辑、长期不可维护 | 当前仓库孵化，复用领域对象和 Application；稳定后再抽包 |
| Fake CLI 骨架产生虚假进展 | 有界面但没有产品价值 | Fake Runtime 仅作测试夹具；先通过 Feasibility Gate 和真实 Semgrep 纵切 |
| LLM 幻觉或提示注入 | 误导用户、越权 | Evidence 锚点、结构化输出、最小上下文、策略不可由代码修改 |
| Semgrep 规则需要网络 | 离线失败、不可复现 | 本地缓存/固定规则版本，远程 URL 不进入 MVP |
| 后端依赖过重 | 安装慢、启动慢 | CLI 延迟导入；验证后再划分 optional dependencies/Runtime 包 |
| 用户把静态结果理解为已确认 | 错误安全决策 | 明示 verification 状态、限制产品措辞 |
| “普通开发者”范围过宽 | UX 和规则无法聚焦 | MVP fixture 优先 Python/FastAPI，Semgrep 可覆盖其他语言但不承诺同等体验 |
| 本地 Scanner 继承开发者高权限环境 | 凭据泄露、越权访问 | 环境 allowlist、固定二进制、cwd/输出/时间限制；不夸大未实现的 OS 隔离 |
| 恶意仓库内容攻击终端或模型 | 终端注入、提示注入、错误解释 | 控制字符清理、不可信数据分层、工具权限隔离、ReasoningArtifact 边界 |
| 交互体验掩盖未提升安全判断 | 产品看似好用但无真实增益 | 与原始 Semgrep 对照，衡量正确 triage 时间和无证据断言率 |

## 15. 已决项与待决产品问题

以下问题按 Gate 管理，不能无限推迟：

1. **已决 / Phase 1：** Semgrep 不进入核心 CLI 依赖；开发期使用独立工具环境或显式本地路径，固定为 `1.173.0`，由 `doctor` 校验解析后的绝对路径和版本。产品化的托管安装器另行设计。
2. **已决 / Phase 1：** 官方规则随 DeepAudit 版本化发布并校验 hash；MVP 不接受远程规则 URL。用户规则目录不是首个纵切的一部分。
3. **进入 Phase 2 前：** `deepaudit [PATH]` 是否与 `deepaudit` 一样进入 Interactive Mode？本 PRD 暂定为是。
4. **进入 Phase 3 前：** MVP 官方支持的第一个模型 provider、允许的 endpoint 和 consent 文案是什么？
5. **进入 Phase 3 前：** 安全标注数据集、对照实验任务和阈值由谁维护？
6. 默认严重度阈值是 `medium` 还是只让 `high` 影响自动化退出码？本 PRD 暂定为 `medium`。
7. canonical JSON schema 是否直接复用 Web API 字段，还是提供独立、版本化 CLI 信封？本 PRD 建议后者。
8. Phase 4 优先做正式 CI/SARIF/baseline，还是 Fix proposal？应由 MVP 用户反馈决定。

## 16. Go / No-Go 标准

### Go

- Gate 0 证明 CLI 可以轻量导入 Runtime，并通过固定/本地规则离线运行真实 Semgrep；
- Phase 1 的 Evidence Core 和真实 Semgrep 纵切稳定；
- 用户能在交互模式中完成 audit → findings → explain；
- AI 失败不影响静态结果；
- 无证据高危、静默 clean、越权读取/写入和秘密泄露护栏全部通过；
- 至少 5 个 Python/FastAPI 试用项目反馈表明交互解释有实际价值；
- 相比原始 Semgrep，正确 triage 时间达到预设改善，事实准确率和无证据断言率达到质量护栏。

### No-Go / 需要调整方向

- 用户只把它当 Semgrep 的彩色壳，不使用 explain；
- AI 解释无法稳定引用正确 Evidence；
- 对照实验不能证明 DeepAudit 比原始 Semgrep 提升正确安全判断或降低 triage 时间；
- 安装和首次运行成本显著高于得到的价值；
- Runtime 复用导致 CLI 无法脱离 Web 基础设施运行；
- 为保持交互体验必须放宽默认执行、网络或文件写入权限。

出现 No-Go 不要求合并到 `main`。`cli-agent` 分支可以继续作为实验分支、调整方向或停止开发。
