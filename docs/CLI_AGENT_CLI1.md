# DeepAudit CLI-1 实现与使用记录

> 状态：Evidence Core 已实现并通过本地纵切验证  
> 日期：2026-08-19  
> 分支：`cli-agent`  
> 范围：非交互 `audit`、`doctor`、terminal/JSON；不含 REPL 与 AI

## 1. 当前能做什么

CLI-1 已经形成一条真实、只读、无模型的审计链路：

```text
CLI 参数
  → LocalAuditApplication
  → AgentRuntime / AuditRunner / LangGraph
  → 安全文件清单
  → 固定版本、本地规则 Semgrep
  → CandidateFinding
  → 聚合 / 去重 / 排序 / 报告
  → terminal 或 JSON + 明确退出码
```

当前支持 Python 源文件和随包的 Python/FastAPI 安全规则。Semgrep 命中从
`E0 / candidate / verification_status=not_run` 开始，不能表述为已验证漏洞。
非交互审计显式关闭模型调用、旧启发式分析工具、项目执行和网络权限。

## 2. 运行方式

要求 Python 3.12 和 Semgrep `1.173.0`。Semgrep 作为独立工具安装，不进入
DeepAudit 核心依赖：

```bash
uv tool install "semgrep==1.173.0"
export DEEPAUDIT_SEMGREP_PATH="$(command -v semgrep)"
```

从仓库根目录进入 `backend`，直接使用当前开发环境：

```bash
cd backend

.venv/bin/python -m app.cli doctor \
  --semgrep "$DEEPAUDIT_SEMGREP_PATH"

.venv/bin/python -m app.cli audit ../path/to/python-project \
  --semgrep "$DEEPAUDIT_SEMGREP_PATH"

.venv/bin/python -m app.cli audit ../path/to/python-project \
  --semgrep "$DEEPAUDIT_SEMGREP_PATH" \
  --format json
```

项目安装或 `uv run` 能访问 uv 缓存时，`backend/pyproject.toml` 已登记命令入口：

```bash
uv run --project backend deepaudit doctor
uv run --project backend deepaudit audit ./path/to/python-project --format json
```

常用参数：

```text
--severity info|low|medium|high|critical
--include RELATIVE_PATH      可重复
--exclude RELATIVE_PATH      可重复
--max-files N
--max-duration SECONDS
--max-results N
--max-target-bytes N
--strict
--out FILE                   必须位于被审计工作区外
```

## 3. 退出码

| Code | 含义 |
| --- | --- |
| `0` | 完整扫描且没有达到严重度阈值的 Finding |
| `1` | 完整扫描且存在达到阈值的 Finding |
| `2` | 参数、路径或输出配置错误 |
| `3` | 严格模式下 Scanner/运行环境不可用；`doctor` 失败 |
| `4` | 非严格模式下扫描或文件覆盖不完整 |
| `5` | 未处理的内部错误 |

完整性优先于 Finding gate。扫描器失败、大文件或不安全符号链接造成覆盖缺口时，
即使没有 Finding 也返回 partial/`4`，不会输出 clean。

## 4. 已实现的安全边界

- Scanner 只能通过已解析的绝对可执行文件和固定 argv 启动，`shell=False`；
- 只接受随包的本地 YAML 规则，固定并验证 Semgrep `1.173.0`；
- Scanner 使用临时 HOME/TMP、环境变量白名单、系统 CA 文件、超时、输出上限和进程组终止；
- 不向 Scanner 传递 API key、云凭据、代理变量或其他用户环境；
- manifest 与 Scanner 都校验路径 jail、普通文件、符号链接和文件大小；
- requested/scanned 使用精确路径集合比较，不只比较数量；
- Scanner 路径替换、解析错误、超时、结果上限和覆盖差异都会成为 error/partial；
- terminal 和报告中的不可信文本清理 ANSI、OSC 与控制字符；
- 报告只保存相对源码路径和工作区显示名，不保存用户主目录绝对路径；
- 默认不写目标工作区；`--out` 只允许工作区外的非符号链接目标并原子替换。

固定 argv 和本地规则不等于 OS 级网络隔离。当前准确承诺是 DeepAudit 不为
Scanner 配置远程规则或网络凭据；系统 sandbox 属于后续独立能力。

## 5. 验证结果

本机固定版本 Semgrep 真实纵切：

```text
audit status:       completed
scanner coverage:   complete (1/1)
findings:           1
finding state:      E0 / candidate / not_run
model calls:        0
stderr bytes:       0
exit code:          1
```

自动化覆盖包括：稳定 fingerprint、只读工作区、凭据不继承、缺失/错误版本 Scanner、
strict 与 partial 退出码、JSON stdout、工作区外原子输出、超大目标、符号链接、
终端控制字符、输出上限，以及“相同数量但不同路径”的覆盖替换。

## 6. 尚未实现

- `deepaudit` 默认进入交互 REPL；
- `/audit`、`/findings`、`/finding` 等会话命令；
- Finding 的本地上下文 triage；
- AI 解释、provider consent 和 `ReasoningArtifact` 生成；
- SARIF、baseline、正式 CI gate、多 Scanner、自动修复和动态验证；
- 面向发布的最小依赖 wheel/安装包验证。

下一阶段应进入 CLI-2 Interactive Triage，但在此之前可以先用 CLI-1 对真实
Python/FastAPI 项目收集扫描覆盖、误报和使用体验反馈。
