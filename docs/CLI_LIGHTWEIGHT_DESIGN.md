# DeepAudit Lightweight CLI 设计与执行记录

> 状态：CLI-L1 纵切已实现，等待真实项目反馈
> 日期：2026-08-20
> 分支：`codex/cli-lightweight`
> 架构决策：[`ADR-004`](architecture/ADR-004-lightweight-cli-extraction.md)

## 1. 为什么从原型中做减法

`cli-agent` 上的第一版证明了审计链路可行，但其运行路径为：

```text
CLI -> AgentRuntime -> AuditRunner -> LangGraph -> manifest -> Semgrep
```

这条路径对服务器端是合理的，对本地静态扫描却不是最小必要系统。扫描的本质是：

```text
校验范围 -> 枚举文件 -> 调用受控分析器 -> 归一化证据 -> 报告覆盖率
```

因此轻量版复用审计原则和数据语义，不复用后端框架运行时。原型保留在
`cli-agent` 分支作为行为基准，避免在没有对照物时重写。

## 2. 当前架构

```text
argparse
  -> pipeline
     -> manifest (路径、类型、大小、数量、覆盖缺口)
     -> SemgrepScanner
        -> scanner.run_capped (固定 argv、环境白名单、超时、输出上限)
        -> JSON 校验与 workspace path jail
        -> Finding / Evidence 归一化与 fingerprint 去重
     -> 严重度过滤
     -> schema 1.0 envelope
  -> terminal / JSON / atomic --out
```

`cli/src/deepaudit_cli` 不导入 `backend/app`，基础安装不声明任何第三方 Python
运行依赖。Semgrep 是独立工具依赖，不被隐藏在“轻量 wheel”指标中。

## 3. 保留的审计语义

- `Finding` 使用稳定 fingerprint，而 `FND-001` 只是在本次运行中的显示编号；
- 静态规则命中是 `E0 / candidate / verification_status=not_run`；
- requested/scanned 比较精确路径集合，不能只比较数量；
- 文件过大、符号链接、权限失败、超时、输出截断和 Scanner 报错都必须披露；
- partial 比 finding gate 优先，避免把不完整扫描描述为 clean；
- JSON 只保存相对源码路径和工作区显示名，不泄露本机主目录；
- Scanner 不继承 API key、云凭证、代理或用户配置环境。

## 4. 基础命令

从源码运行：

```bash
PYTHONPATH=cli/src python -m deepaudit_cli doctor --semgrep /absolute/path/to/semgrep
PYTHONPATH=cli/src python -m deepaudit_cli audit /path/to/project --semgrep /absolute/path/to/semgrep
```

构建并安装后：

```bash
deepaudit doctor
deepaudit audit .
deepaudit audit . --format json
deepaudit audit . --out ../deepaudit-report.json
```

当前支持 `--severity`、可重复的 `--include/--exclude`、文件/时长/结果/单文件大小
上限。Semgrep 固定为 `1.173.0`；版本不符时 fail closed。

## 5. 退出码

| Code | 含义 |
|---:|---|
| 0 | 完整扫描且没有达到阈值的 Finding |
| 1 | 完整扫描且存在达到阈值的 Finding |
| 2 | 参数、路径、范围或输出配置错误 |
| 3 | Scanner 缺失、版本错误或执行失败 |
| 4 | 有结果但覆盖不完整 |
| 5 | 未分类的内部错误 |

## 6. 已验证结果

- Python 运行时第三方依赖：`0`；
- wheel：约 `17 KB`，包含本地规则 YAML，元数据无 `Requires-Dist`；
- 全新临时 venv 使用 `pip install --no-deps` 安装成功；
- 真实 Semgrep `1.173.0`：1/1 文件完整扫描，1 个 E0 candidate，退出码 1；
- 与 graph-backed 原型对照：scope、核心 coverage、Finding fingerprint、严重度、
  规则、位置、证据等级和 verification 状态保持一致；
- 自动化覆盖依赖边界、manifest、路径 jail、环境白名单、终端清理、进程超时、
  输出上限、JSON terminal、partial 优先级和工作区外原子输出。

## 7. 还没有做什么

- 还没有 TUI、交互会话和 AI 解释；
- 还没有 SARIF、baseline、CI 专用输出和多语言规则集；
- 还没有自动安装 Semgrep，也没有承诺 OS 级断网；
- 还没有发布到 PyPI，包名可用性和发布凭证需在发布前单独确认；
- 旧 graph-backed CLI 尚未删除，待轻量版真实项目反馈与契约测试稳定后再清理。

下一步应先拿 2–3 个真实 Python/FastAPI 仓库做 dogfood，记录覆盖缺口、误报和
命令体验；此后优先补 SARIF/baseline，而不是立刻引入 TUI 框架。
