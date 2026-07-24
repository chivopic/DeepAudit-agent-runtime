# DeepAudit - 人人拥有的 AI 审计战队，让漏洞挖掘触手可及 🦸‍♂️

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
  <strong>简体中文</strong> | <a href="README_EN.md">English</a> | <a href="README_JA.md">日本語</a>
</p>

> **本仓库说明**：基于上游 [lintsinghua/DeepAudit](https://github.com/lintsinghua/DeepAudit) v3.0.0 的 **Agent Runtime 重构实验分支**（M0–M11）。  
> 生产 ReAct 路径保持兼容；新增 LangGraph 双路径与协议化运行时。旧版 README 见 [`backup/`](backup/)。

</div>

<div align="center">
  <img src="frontend/public/DeepAudit.gif" alt="DeepAudit Demo" width="90%">
</div>

---

## 📸 界面预览

<div align="center">

### 🤖 Agent 审计入口

<img src="frontend/public/images/README-show/Agent审计入口（首页）.png" alt="Agent审计入口" width="90%">

*首页快速进入 Multi-Agent 深度审计*

</div>

<table>
<tr>
<td width="50%" align="center">
<strong>📋 审计流日志</strong><br/><br/>
<img src="frontend/public/images/README-show/审计流日志.png" alt="审计流日志" width="95%"><br/>
<em>实时查看 Agent 思考与执行过程</em>
</td>
<td width="50%" align="center">
<strong>🎛️ 智能仪表盘</strong><br/><br/>
<img src="frontend/public/images/README-show/仪表盘.png" alt="仪表盘" width="95%"><br/>
<em>一眼掌握项目安全态势</em>
</td>
</tr>
<tr>
<td width="50%" align="center">
<strong>⚡ 即时分析</strong><br/><br/>
<img src="frontend/public/images/README-show/即时分析.png" alt="即时分析" width="95%"><br/>
<em>粘贴代码 / 上传文件，秒出结果</em>
</td>
<td width="50%" align="center">
<strong>🗂️ 项目管理</strong><br/><br/>
<img src="frontend/public/images/README-show/项目管理.png" alt="项目管理" width="95%"><br/>
<em>GitHub/GitLab/Gitea 导入，多项目协同管理</em>
</td>
</tr>
</table>

<div align="center">

### 📊 专业报告

<img src="frontend/public/images/README-show/审计报告示例.png" alt="审计报告" width="90%">

*一键导出 PDF / Markdown / JSON*（图中为快速模式，非 Agent 模式报告）

</div>

---

## 🏆 CVE 漏洞发现

<div align="center">

### **DeepAudit（闭源版本） 已成功发现并获得 49 个 CVE 编号 和 6 个 GHSA 安全公告🦞**
### **涉及 17 个知名开源项目**

</div>

> 完整列表与 GHSA/CVE 表格请见上游 [CVEList.md](CVEList.md) 与 [backup/README.md](backup/README.md)。  
> 漏洞发现归功于 DeepAudit 团队与社区；本仓库聚焦 **Agent 运行时架构演进**。

---

## ⚡ 项目概述

**DeepAudit** 是一个基于 **Multi-Agent 协作架构**的下一代代码安全审计平台。它不仅仅是一个静态扫描工具，而是模拟安全专家的思维模式，通过多个智能体的自主协作，实现对代码的深度理解、漏洞挖掘和（可选的）沙箱验证。

本分支在保持前端兼容的前提下，完成了 **Agent Runtime 重构（M0–M11）**：

| 支柱 | 决策 |
|------|------|
| 编排 | **LangGraph** 负责状态 / checkpoint / 中断 |
| 领域模型 | **Pydantic** 统一 API、工具、沙箱 I/O |
| 持久化 | Checkpoint ≠ 业务库 ≠ Artifact Store |
| 安全 | Phase 1：无默认不可信执行；`verification_status=NOT_RUN` |
| 迁移 | **双路径**：生产 ReAct 冻结；LangGraph 增量上线 |

用户仍可导入项目后自动完成：识别技术栈 → 分析潜在风险 → 汇总发现 → 生成报告。

> **核心理念**: 让 AI 像黑客一样攻击，像专家一样防御。

## 💡 为什么选择 DeepAudit？

<div align="center">

| 😫 传统审计的痛点 | 💡 DeepAudit 解决方案 |
| :--- | :--- |
| **人工审计效率低**<br>跨不上 CI/CD 代码迭代速度 | **🤖 Multi-Agent 自主审计**<br>AI 自动编排审计策略 |
| **传统工具误报多**<br>缺乏语义理解 | **🧠 RAG + 领域模型**<br>结合代码语义与结构化 Finding |
| **数据隐私担忧**<br>源码上云合规难 | **🔒 支持 Ollama 本地部署**<br>数据可不出内网 |
| **无法确认真实性**<br>不知漏洞是否可利用 | **💥 沙箱 / 验证子图（可开关）**<br>Phase 1 默认不跑不可信代码 |

</div>

---

## 🏗️ 系统架构

### 双路径 Agent 运行时（本仓库重点）

```text
┌─────────────────────────────────────────────────────────────┐
│  Frontend (React)  ·  /api/v1/*                             │
├────────────────────────────┬────────────────────────────────┤
│  生产路径（冻结兼容）         │  LangGraph 双路径（本分支新增）   │
│  /api/v1/agent-tasks/*     │  /api/v1/graph-audits/*        │
│  ReAct Multi-Agent         │  AuditRunner + StateGraph      │
└────────────────────────────┴────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   domain/ (Pydantic)   graph/ (LangGraph)   persistence/
   tooling/  sandbox/   harness/             context/
   observability/       application façade
```

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0 | 架构评估 / ADR | ✅ |
| M1 | 领域模型 | ✅ |
| M2 | LangGraph 骨架 + FakeLLM | ✅ |
| M3 | Checkpoint + 业务/制品存储 | ✅ |
| M4 | API 门面 / 事件 / cancel / resume | ✅ |
| M5 | Context Manager | ✅ |
| M6 | Sandbox 策略 + 执行器 | ✅ |
| M7 | Verification 子图 | ✅ |
| M8 | MCP / ToolRegistry | ✅ |
| M9 | 可观测性 / 脱敏 | ✅ |
| M10 | Evals + CI | ✅ |
| M11 | Agent Harness（包装 LangGraph） | ✅ |

详情：[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) · [复盘与代码审计](docs/implementation/m0-m11-retro-and-audit.md) · [目标架构](docs/implementation/target-architecture.md)

### 整体产品架构图

<div align="center">
<img src="frontend/public/images/README-show/架构图.png" alt="DeepAudit 架构图" width="90%">
</div>

### 🔄 审计工作流（生产 Multi-Agent）

| 步骤 | 阶段 | 负责 Agent | 主要动作 |
|:---:|:---:|:---:|:---|
| 1 | **策略规划** | **Orchestrator** | 接收任务，制定审计计划 |
| 2 | **信息收集** | **Recon Agent** | 扫描结构，提取攻击面 |
| 3 | **漏洞挖掘** | **Analysis Agent** | RAG + 语义分析，产出候选漏洞 |
| 4 | **PoC 验证** | **Verification Agent** | （可选）沙箱验证；Phase 1 默认可跳过 |
| 5 | **报告生成** | **Orchestrator** | 汇总发现，生成专业报告 |

### 📂 项目代码结构

```text
DeepAudit/
├── backend/                        # Python FastAPI 后端
│   ├── app/
│   │   ├── api/v1/                 # REST：agent-tasks + graph-audits
│   │   ├── services/
│   │   │   ├── agent/              # ★ Agent Runtime（本分支核心）
│   │   │   │   ├── domain/         # M1 领域模型
│   │   │   │   ├── graph/          # M2 LangGraph 节点 / 路由
│   │   │   │   ├── persistence/    # M3 checkpoint / store / artifacts
│   │   │   │   ├── application/    # M3–M4 runner / 门面 / 事件
│   │   │   │   ├── context/        # M5 Context Manager
│   │   │   │   ├── sandbox/        # M6 策略 + 执行器
│   │   │   │   ├── tooling/        # M8 ToolProtocol / MCP
│   │   │   │   ├── observability/  # M9 Tracer / 脱敏
│   │   │   │   ├── harness/        # M11 AgentSpec / Runtime
│   │   │   │   └── core/           # 既有 ReAct 生产路径
│   │   │   └── llm/                # LLM 网关
│   │   └── ...
│   └── tests/                      # M1–M11 确定性单测 + evals
├── frontend/                       # React + TypeScript
├── docker/                         # 部署与沙箱镜像
├── docs/
│   ├── architecture/               # ADR-001/002/003
│   └── implementation/             # M0–M11 实现文档
├── backup/                         # 旧版 README 备份
└── IMPLEMENTATION_STATUS.md        # 里程碑状态
```

---

## 🚀 快速开始

### 方式一：一行命令部署（推荐 · 上游镜像）

使用上游预构建 Docker 镜像：

```bash
curl -fsSL https://raw.githubusercontent.com/lintsinghua/DeepAudit/v3.0.0/docker-compose.prod.yml | docker compose -f - up -d
```

## 🇨🇳 国内加速部署

```bash
curl -fsSL https://raw.githubusercontent.com/lintsinghua/DeepAudit/v3.0.0/docker-compose.prod.cn.yml | docker compose -f - up -d
```

> 🎉 **启动成功！** 访问 http://localhost:3000

---

### 方式二：克隆本仓库（Agent Runtime 开发）

```bash
# 1. 克隆本仓库（推送后替换为你的新仓库地址）
git clone https://github.com/chenzh659/DeepAudit-agent-runtime.git
cd DeepAudit-agent-runtime

# 2. 配置环境变量
cp backend/env.example backend/.env
# 编辑 backend/.env 填入 LLM API Key

# 3. 启动依赖与全栈
docker compose up -d
```

---

## 🔧 源码开发指南

### 环境要求

- Python 3.11+
- Node.js 20+
- PostgreSQL 15+
- Docker（沙箱 / 本地依赖）
- [uv](https://github.com/astral-sh/uv)（后端包管理，推荐）

### 1. 数据库

```bash
docker compose up -d redis db adminer
```

### 2. 后端

```bash
cd backend
cp env.example .env
uv sync --extra dev
# Windows Git Bash
source .venv/Scripts/activate   # 或 .venv/bin/activate
uvicorn app.main:app --reload
```

### 3. 前端

```bash
cd frontend
cp .env.example .env
pnpm install
pnpm dev
```

### 4. Agent Runtime 测试（本分支）

```bash
cd backend
uv run pytest \
  tests/test_agent_domain.py \
  tests/test_agent_graph_m2.py \
  tests/test_agent_persistence_m3.py \
  tests/test_agent_facade_m4.py \
  tests/test_agent_context_m5.py \
  tests/test_agent_sandbox_m6.py \
  tests/test_agent_verification_m7.py \
  tests/test_agent_tooling_m8.py \
  tests/test_agent_observability_m9.py \
  tests/test_agent_evals_m10.py \
  tests/test_agent_harness_m11.py \
  -q
# 期望：121 passed

uv run python -m tests.evals.runner --ci
```

### 5. 沙箱镜像（可选）

```bash
docker pull ghcr.io/lintsinghua/deepaudit-sandbox:latest
# 国内：ghcr.nju.edu.cn/lintsinghua/deepaudit-sandbox:latest
```

---

## 🤖 Multi-Agent 智能审计

### 支持的漏洞类型

<table>
<tr>
<td>

| 漏洞类型 | 描述 |
|---------|------|
| `sql_injection` | SQL 注入 |
| `xss` | 跨站脚本攻击 |
| `command_injection` | 命令注入 |
| `path_traversal` | 路径遍历 |
| `ssrf` | 服务端请求伪造 |
| `xxe` | XML 外部实体注入 |

</td>
<td>

| 漏洞类型 | 描述 |
|---------|------|
| `insecure_deserialization` | 不安全反序列化 |
| `hardcoded_secret` | 硬编码密钥 |
| `weak_crypto` | 弱加密算法 |
| `authentication_bypass` | 认证绕过 |
| `authorization_bypass` | 授权绕过 |
| `idor` | 不安全直接对象引用 |

</td>
</tr>
</table>

> 📖 产品侧 Agent 指南：[docs/AGENT_AUDIT.md](docs/AGENT_AUDIT.md)  
> 📖 运行时架构深潜：[docs/AGENT_AUDIT_ARCHITECTURE.md](docs/AGENT_AUDIT_ARCHITECTURE.md)

### Phase 1 安全不变量

1. 默认 Finding：`verification_status = not_run`（未经验证不得当已确认）
2. 禁止模型任意 shell；沙箱 **allowlist** 动作
3. 路径 jail：`SourceLocation` / 工具 / analyze 读文件 / ArtifactStore
4. Checkpoint 中不落大源码，使用 `ArtifactRef`
5. Agent Harness **包装** LangGraph，不另起第二套 agent loop

---

## 🔌 支持的 LLM 平台

<table>
<tr>
<td align="center" width="33%">
<h3>🌍 国际平台</h3>
<p>
OpenAI GPT-4o / GPT-4<br/>
Claude 3.5 Sonnet / Opus<br/>
Google Gemini Pro<br/>
DeepSeek V3
</p>
</td>
<td align="center" width="33%">
<h3>🇨🇳 国内平台</h3>
<p>
通义千问 Qwen<br/>
智谱 GLM-4<br/>
Moonshot Kimi<br/>
文心一言 · MiniMax · 豆包
</p>
</td>
<td align="center" width="33%">
<h3>🏠 本地部署</h3>
<p>
<strong>Ollama</strong><br/>
Llama3 · Qwen2.5 · CodeLlama<br/>
DeepSeek-Coder · Codestral<br/>
<em>代码不出内网</em>
</p>
</td>
</tr>
</table>

💡 支持 API 中转站 | 详细配置 → [LLM 平台支持](docs/LLM_PROVIDERS.md)

---

## 🎯 功能矩阵

| 功能 | 说明 | 模式 |
|------|------|------|
| 🤖 **Agent 深度审计** | Multi-Agent 协作 / LangGraph 双路径 | Agent |
| 🧠 **RAG 知识增强** | 代码语义与知识库检索 | Agent |
| 🔒 **沙箱策略化执行** | Allowlist + NullSandbox（Phase 1） | Agent |
| 🧩 **协议化运行时** | Domain / Graph / Store / Tools / Harness | 本分支 |
| 📊 **确定性评测** | FakeLLM + evals + GitHub Actions | 本分支 |
| 🗂️ **项目管理** | GitHub/GitLab/Gitea / ZIP | 通用 |
| ⚡ **即时分析** | 代码片段秒级分析 | 通用 |
| 📋 **审计规则 / 提示词** | OWASP + 自定义 | 通用 |
| 📊 **报告导出** | PDF / Markdown / JSON | 通用 |

---

## 🦖 发展路线图

- [x] 基础静态分析，集成 Semgrep
- [x] RAG 知识库 + Docker 安全沙箱
- [x] Multi-Agent 协作架构（生产 ReAct）
- [x] **Agent Runtime M0–M11**（LangGraph + 协议边界 + Harness）
- [ ] `/graph-audits` 与生产一致的鉴权 / 多租户
- [ ] 将 ToolRegistry / Tracer / Budget **接入图节点**
- [ ] 真·中断恢复（非 re-drive）
- [ ] 沙箱 Worker 进程化（ADR-003）
- [ ] 自动修复 (Auto-Fix) / 增量 PR 审计 / CI 集成

---

## 🤝 贡献与社区

### 贡献指南

欢迎 Issue / PR / 文档改进。请阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)。

本仓库为个人实验分支：架构讨论与运行时缺陷反馈优先开 Issue。

### 上游与致谢

- 产品与社区上游：[@lintsinghua/DeepAudit](https://github.com/lintsinghua/DeepAudit)
- 本分支 Agent Runtime 重构与审计 hardening：[@chenzh659](https://github.com/chenzh659)

## 📄 许可证

本项目采用 [AGPL-3.0 License](LICENSE) 开源（继承上游许可）。

---

<div align="center">
  <strong>Based on DeepAudit · Agent Runtime refactor by <a href="https://github.com/chenzh659">chenzh659</a></strong>
</div>

---

## 致谢

感谢以下开源项目：

[FastAPI](https://fastapi.tiangolo.com/) · [LangChain](https://langchain.com/) · [LangGraph](https://langchain-ai.github.io/langgraph/) · [ChromaDB](https://www.trychroma.com/) · [LiteLLM](https://litellm.ai/) · [Tree-sitter](https://tree-sitter.github.io/) · [React](https://react.dev/) · [Vite](https://vitejs.dev/) · [shadcn/ui](https://ui.shadcn.com/) · 以及上游 DeepAudit 社区

---

## ⚠️ 重要安全声明

### 法律合规声明

1. 禁止**任何未经授权的漏洞测试、渗透测试或安全评估**
2. 本项目仅供网络空间安全学术研究、教学和学习使用
3. 严禁将本项目用于任何非法目的或未经授权的安全测试

### 使用限制

- 仅限在授权环境下用于教育和研究目的
- 禁止用于对未授权系统进行安全测试
- 使用者需对自身行为承担全部法律责任

### 免责声明

作者不对任何因使用本项目而导致的直接或间接损失负责，使用者需对自身行为承担全部法律责任。

详细政策见 [DISCLAIMER.md](DISCLAIMER.md) · [SECURITY.md](SECURITY.md)。

### 快速参考

- **代码隐私**：源码可能发送到所选 LLM 服务商
- **敏感代码**：优先本地模型（Ollama）
- **漏洞报告**：通过合法渠道上报
