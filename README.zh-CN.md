# Swarm Mix (`swarm-mix`)

[English](./README.md) | 简体中文

一套同时兼容 **Google Antigravity**、**Claude Code** 与 **OpenAI Codex** 的多宿主插件，为多代理协作注入默认的**三级算力分流**与**跨模型家族强制交叉审查**。

## 核心作用

- **三级算力分流**：核心攻坚用旗舰（`L1`）、常规模块编码用均衡档（`L2`）、只读检索/文档/守护用高速轻量档（`L3`），避免杂务空耗旗舰配额。
- **跨家族强制互审**：独立验收节点（`verifier`）强制切换至异构模型家族（Gemini ↔ Claude、Claude ↔ OpenAI Codex），日常用 `Medium`，并发/核心架构/安全变更自动升 `High`。

---

## 安装方式

### 1. 发一句话让 Agent 自动装（最省事）

```text
请克隆 https://github.com/the1sky/swarm-mix.git 并执行 ./install.sh，为本机已装客户端配置 swarm-mix 插件。
```

### 2. 一行命令全平台安装（自动识别 Antigravity / Claude Code / Codex）

```bash
git clone https://github.com/the1sky/swarm-mix.git && cd swarm-mix && ./install.sh
```

> 本地维护软链同步模式：`./install.sh --link`

### 3. 各客户端原生命令安装

**Claude Code**
```bash
claude plugin marketplace add https://github.com/the1sky/swarm-mix.git
claude plugin install swarm-mix@swarm-mix-local
```

**Google Antigravity**
```bash
git clone https://github.com/the1sky/swarm-mix.git ~/.gemini/config/plugins/swarm-mix
# 或使用 CLI：agy plugin install ./swarm-mix
# 或项目仓库级共享：git submodule add https://github.com/the1sky/swarm-mix.git .agents/plugins/swarm-mix
```

**OpenAI Codex**
```bash
git clone https://github.com/the1sky/swarm-mix.git ~/.codex/plugins/swarm-mix
~/.codex/plugins/swarm-mix/install.sh
```

**仅安装技能层（`npx skills`）**
```bash
npx skills add https://github.com/the1sky/swarm-mix --skill swarm-mix -g
```

---

## 三端默认角色矩阵

| 层级 | Google Antigravity (`swarm`) | Claude Code (`Agent`) | OpenAI Codex (`spawn_agent` + CLI) |
| :--- | :--- | :--- | :--- |
| **L1 核心攻坚** | 继承主会话模型 *(轻量主会话升 `Carbon High`)* | 主会话模型 / `Opus (High)` | 主会话默认主模型（`High`） |
| **L2 常规编码** | `Gemini 4 Argon (High)` / `Claude Sonnet 5.5 (High)` | `std-author` → `Sonnet (High)` | `std-author`（`Medium`） |
| **跨家族互审** | Gemini ↔ `Opus 5.5` / `Carbon` *(日常 `Medium`，高危 `High`)* | `cross-verifier` → `codex exec review` *(`Medium` / `High`)* | `claude -p --model opus` *(`medium` / `high`，回退 `sonnet`)* |
| **L3 轻量与守护** | `Gemini 4 Hydrogen (Medium)` *(含 `narrator` / `monitor`)* | `fast-scout` → `Haiku (Medium)` | `fast-scout`（`Low`） |

## 使用说明

- **口头指令优先**：对话中显式点名模型时，优先级始终高于本规则。
- **Antigravity 前置项**：新装客户端需确认已在插件面板开启官方内置 **Swarm** 插件。
- **自动兜底**：自动适配账号已灰度型号；配额归 `0%` 时跨桶切换；未安装对端 CLI 时自动降级为独立上下文同系审查并注明。

## License

[MIT](./LICENSE)
