<h1>
  <img src="./assets/logo.svg" align="left" width="44" height="44" alt="Swarm Mix Logo" />
  Swarm Mix (<code>swarm-mix</code>)
</h1>

[English](./README.md) | **简体中文**

一套同时兼容 **Google Antigravity**、**Claude Code** 与 **OpenAI Codex** 的多宿主插件，为多代理协作注入默认的**三级算力分流**与**跨模型家族强制交叉审查**。

## 核心作用

- **三级算力分流**：核心攻坚用旗舰（`L1`）、常规模块编码用均衡档（`L2`）、只读检索/文档/守护用高速轻量档（`L3`），避免杂务空耗旗舰配额。
- **跨家族强制互审**：独立验收节点（`verifier`）强制切换至异构模型家族（Gemini ↔ Claude、Claude ↔ OpenAI Codex），日常用 `Medium`，并发/核心架构/安全变更自动升 `High`。
- **多端 Token 透明账单 (`swarm-tokens`)**：补齐官方 Swarm 面板无 Token 统计的空白，支持按 Worker / 模型梯队查看新增输入、缓存命中率、输出消耗及空转守护进程告警。

---

## 安装方式

### 1. 自然语言安装（直接复制发给 Agent）

在 **Antigravity**、**Claude Code** 或 **Codex** 对话框中直接发送（统一克隆至 `~/.local/share/swarm-mix`，不污染当前项目目录）：

**自动识别本机客户端一键安装 / 更新（推荐）**
```text
请帮我从 https://github.com/the1sky/swarm-mix.git 安装或更新 swarm-mix（克隆到 ~/.local/share/swarm-mix，若已存在则先 git pull），执行 ./install.sh 完成本机客户端配置，并运行 ~/.local/bin/swarm-router --host auto -m 验证路由表生效。
```

**仅在当前客户端独立安装（不改动其它工具目录）**
```text
请从 https://github.com/the1sky/swarm-mix.git 仅为我当前正在使用的这个客户端安装 swarm-mix 插件与技能（不要修改本机其它 Agent 客户端配置），装好后运行 model_router.py --host auto -m 验证。
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
| **跨家族互审** | Gemini ↔ `Opus 5.5` / `Carbon` *(支持可选本机 `Codex` GPT 桥接)* | `cross-verifier` → `codex exec review` *(`Medium` / `High`)* | `claude -p --model opus` *(`medium` / `high`，回退 `sonnet`)* |
| **L3 轻量与守护** | `Gemini 4 Hydrogen (Medium)` *(含 `narrator` / `monitor`)* | `fast-scout` → `Haiku (Medium)` | `fast-scout`（`Low`） |

## Token 消耗审计（`Swarm Mix` 侧边栏面板 & `swarm-tokens`）

- **3 层动态能力路由（`swarm-router`）**：零 Token 探测本机硬目录与实时配额（L1）+ 五维基准能力卡与未知新模型自动继承（L2）+ 历史看板通过率贝叶斯自校准（L3）；单装任意一端 100% 独立零依赖运行，多厂共存时自动按专长跨厂调度。
- **Antigravity 原生侧边栏**：点击对话右上角 `+` 菜单选择 **Swarm Mix**（或对话内按钮 `sidecar://swarm-mix/swarm_board/`），实时查看四档模型算力色带、各成员内联消耗条、跨会话大盘切换、看板检索与 **Models** 能力路由面板。
- **跨端命令行（`swarm-tokens` & `swarm-router`）**：直接对 Agent 说「查看最近一次 Swarm 的 token 消耗」，或在终端运行：

```bash
swarm-router --host auto -m    # 查看当前宿主 3 层能力画像与各子任务最优派单表
swarm-tokens                   # 查看最近一次 Swarm / 多代理会话的分成员与分模型账单
swarm-tokens --watch 3         # 每 3 秒实时刷新运行中 Swarm 的消耗与缓存命中率
swarm-tokens --list -n 10      # 列出跨 Antigravity / Claude Code / Codex 的近期会话
swarm-tokens --host agy -m     # 输出 Markdown 表格（支持 --json 导出结构化数据）
```

## 使用说明

- **口头指令优先**：对话中显式点名模型时，优先级始终高于本规则。
- **Antigravity 前置项**：新装客户端需确认已在插件面板开启官方内置 **Swarm** 插件。
- **自动兜底**：自动适配账号已灰度型号；配额归 `0%` 时跨桶切换；未安装对端 CLI 时自动降级为独立上下文同系审查并注明。

## License

[MIT](./LICENSE)
