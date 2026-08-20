# DeepAudit CLI Gate 0 可行性记录

> 结论：**Conditional Go — 可以进入 CLI-1 Evidence Core，不能直接发布当前后端环境，也暂不进入 TUI。**  
> 日期：2026-08-19  
> 分支：`cli-agent`  
> 环境：macOS 26.5.2 / arm64 / Python 3.12.13 / uv 0.12.3

## 1. 结论

真实纵切已经证明以下路径成立：

```text
本地 Python 3.12
  → 无 Web/DB/Redis/Chroma/Docker 服务运行 Agent Harness
  → 固定 Semgrep 1.173.0 + 本地规则扫描精确文件
  → 共享 Finding/Evidence 模型生成 E0/candidate canonical JSON
  → 工作区内容 hash 不变
```

但 Gate 同时否定了一个实现方向：**不能把当前完整 backend 环境原样作为 CLI 安装包。** 它约 983 MB，并包含 Web、数据库、Chroma、ONNX、Django、Kubernetes、报告和开发工具等与本地 CLI 核心无关的依赖。相同源码在仅含 Pydantic/LangGraph 闭包的 26 MB 临时环境中可以运行，所以 CLI 应复用领域与 Application 代码，并拆分部署依赖集合。

## 2. 验证结果

| Gate 条件 | 实测证据 | 结论 |
| --- | --- | --- |
| Runtime 无服务可用 | 只安装 Pydantic/LangGraph 闭包的 26 MB 环境完成一次 harness 审计；FastAPI、SQLAlchemy、Redis、Chroma、Docker、WeasyPrint 均未安装或加载 | Go |
| Runtime 轻量导入 | 修复包级 eager import 后，harness 冷导入由约 3.06 s 降至约 0.37 s；不再加载 RAG | Go，已修复 |
| 真实 Scanner 离线纵切 | Semgrep `1.173.0`、本地规则、metrics/version check 关闭，1 个文件产生 1 条预期命中 | Go |
| canonical 结果 | 生成稳定 `Finding`、`Evidence`、位置、snippet、CWE、规则 hash 和 fingerprint | Go；Evidence Level/State 暂存 metadata，CLI-1 扩展共享 domain |
| Scanner 执行边界 | 绝对路径、argv-only、`shell=false`、30 s timeout、4 MiB 组合输出上限 | Go |
| 环境凭据隔离 | 只传 9 个 allowlist 环境键；注入的 AWS credential 不进入子进程 | Go |
| 工作区只读 | 扫描前后 SHA-256 均为 `b432fb2188707094201bfe2b8b8404a5ac6f7ec5da3a18cf2d5ece85384d3509` | Go |
| 性能候选值 | 最小 Runtime 审计约 0.37 s；1 文件 Semgrep 探针端到端约 1.39 s，Scanner 报告峰值内存约 92 MB | Go；大仓库仍须 CLI-1 benchmark |
| 安装体积 | 完整 backend `.venv` 约 983 MB；独立 Semgrep 环境约 276 MB | 核心 CLI 原样打包 No-Go；Scanner 必须解耦 |

以上数字是当前开发机的方向性测量，不是发布 SLO。NFR 需要在 CLI-1 的 100 文件 fixture 和干净安装任务中重新测量。

## 3. Gate 中发现并处理的问题

### 3.1 Agent 包入口存在导入副作用

原 `app.services.agent.__init__` 在任何子模块导入前都会加载旧 Agents、RAG 知识和工具。一次 `from app.services.agent.harness import AgentRuntime` 因而加载 27 份知识文档并输出日志。

处理：包级公开 API 改为惰性解析，保持旧导入名称兼容；新增子进程回归测试，保证仅导入 harness 不会初始化 RAG 或 legacy orchestrator。

### 3.2 Semgrep 的目录发现不能作为覆盖真相

对 Git 工作区内的未跟踪 `tests/fixtures` 目录执行目录扫描时，Semgrep 返回 `paths.scanned=[]`；精确传入文件后正常命中。这说明即便使用 `--no-git-ignore`，也不能把 Scanner 自己的目录发现当作 DeepAudit 的覆盖证明。

决策：CLI-1 由 DeepAudit 先建立受控文件 manifest，再把精确文件传给 Scanner；报告同时记录 requested、scanned 和 skipped，二者不一致时状态为 partial，绝不能输出 clean。

### 3.3 Semgrep OSS JSON 不是完整 canonical 数据

当前 OSS JSON 把匹配源码和 Scanner fingerprint 标记为 `requires login`。因此 canonical 结果不能直接透传 Semgrep JSON。

决策：DeepAudit 在工作区 jail 内读取匹配行，计算 code hash 和稳定 fingerprint；Semgrep 只提供候选信号、位置、规则和 metadata。

### 3.4 进程组终止在受管环境中可能被拒绝

输出上限测试发现，部分受管环境允许终止直接子进程但拒绝向进程组发信号。

处理：优先终止进程组；遇到权限拒绝时回退到终止已知子进程。CLI-1 需要继续验证 Semgrep 子进程树和取消后的孤儿进程行为。

## 4. 已决架构选择

### Scanner 供应

- 核心 CLI 不依赖 Semgrep Python 包。
- CLI-1 固定并验证 Semgrep `1.173.0`；开发者使用独立工具环境或传入显式本地路径。
- 二进制只解析一次为绝对路径，并在扫描前验证精确版本。
- 官方规则随 DeepAudit 版本化发布，记录 rule ID 与 SHA-256；不使用远程 registry/URL。
- 面向普通用户的托管安装器可以后续实现，但必须有下载源、hash/signature、缓存和显式网络授权设计。

### Runtime 复用

- 继续复用 `backend/app/services/agent/domain`、Harness 治理概念、预算和 Application 边界。
- 不把 FastAPI、数据库、Chroma、Docker、报告系统和开发工具带入核心 CLI 依赖。
- CLI-1 在 monorepo 内先实现真实 adapter；发布前拆 optional dependency 或最小 runtime 包，并用干净 wheel 安装测试验证闭包。

### 模型与网络

- CLI-1 Evidence Core 和 CLI-2 本地交互不调用模型。
- 首个云模型 provider、固定 endpoint allowlist、发送范围与逐 session consent 是 CLI-3 的进入门槛。
- provider 未决定前不得通过任意 `base_url` 或继承代理环境来“临时接通”模型。

## 5. CLI-1 进入条件与范围

现在可以开始 CLI-1，但顺序应是：

1. 扩展共享 domain：Evidence Level、Finding State、ReasoningArtifact 和迁移默认值。
2. 把 Gate 探针中的执行逻辑收敛为 `ScannerProtocol` 与 Semgrep adapter，而不是让 Graph 节点直接调用 subprocess。
3. 建立 DeepAudit 文件 manifest、路径 jail、coverage/partial/error 语义。
4. 将静态扫描作为受治理阶段接入同一 Application/Runner。
5. 提供最小 JSON 与 terminal presenter、`doctor`、退出码和正/负 fixture。
6. 完成 scanner 缺失、损坏 JSON、超时、超大输出、环境凭据、symlink、控制字符和工作区 hash 回归。

在 Evidence Core 纵切稳定前，不开始 REPL/TUI；在 CLI-3 Gate 前，不接云模型。

## 6. 可重复命令

```bash
# 独立安装固定 Scanner；不会加入 backend 项目依赖
uv tool install 'semgrep==1.173.0'

cd backend

# 在临时最小环境验证 Runtime 不依赖后端基础设施栈
gate0_runtime_dir="$(mktemp -d /tmp/deepaudit-min-runtime.XXXXXX)"
uv venv --python 3.12 "$gate0_runtime_dir/.venv"
uv pip install --python "$gate0_runtime_dir/.venv/bin/python" \
  'pydantic==2.12.5' 'langgraph==1.0.5' 'langchain-core==1.2.1'
PYTHONPATH=. "$gate0_runtime_dir/.venv/bin/python" \
  scripts/cli_gate0_runtime_probe.py

# 完整开发环境用于仓库测试
uv sync --frozen

# 单元边界与现有 harness
.venv/bin/pytest -q \
  tests/test_agent_import_boundaries.py \
  tests/test_cli_gate0_probe.py \
  tests/test_agent_harness_m11.py

# 真实离线纵切；传入本机解析后的 Semgrep 路径
.venv/bin/python scripts/cli_gate0_probe.py --semgrep /absolute/path/to/semgrep
```

当前结果：14 个相关测试通过，Ruff 检查通过，真实探针返回 `status: go`。

全量后端回归另有已知基线问题：WeasyPrint 因本机缺少 `libgobject-2.0-0` 在收集阶段失败；排除该报告测试后为 1064 passed、8 skipped、8 failed。8 个失败在模拟旧 eager import 后完全相同，分别是旧 Agent 缺 `_cancel_callback`（5）和旧测试 patch 源码中不存在的 `get_agent_config`（3），不属于本次 CLI 改动。
