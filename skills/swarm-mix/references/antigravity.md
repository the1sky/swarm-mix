# Swarm Mix — Antigravity 原生 Swarm 异构编排细则

本规则代表用户的全局常驻显式授权，**覆盖内置 `swarm` 技能中“未获要求时全员继承主模型（包括 `narrator` 与 `monitor`）”的默认限制**。

## 1. 启动前配额预检与跨桶兜底原则
首次调用 `swarm(action="add")` 前，先调用一次 `swarm(action="models")` 检查实时 `quota_remaining` 与 `quota_note`（**Gemini 全系共享同一配额桶，Claude 全系与 GPT-OSS 共享另一独立配额桶**）：
- **账号模型池自适应**：若 `models` 返回列表中不含某指定 ID（如尚未灰度 `gemini-4-hydrogen-medium`），自动映射至列表内同家族同梯队的可用型号（如 `gemini-3.8-flash-medium`），严禁传入未列出的模型 ID。
- **AI Credits 兜底**：若 `quota_note` 显示已由 AI Credits 接管计费，原定模型可继续正常使用。
- **低水位节流（`<15%` 告警但未归零）**：同桶内降档（`Carbon` → `Argon`/`Hydrogen`；`Opus` → `Sonnet`）。
- **配额归零（`0%` 且无 AI Credits）**：同家族模型会同步归零，必须**跨配额桶切换**并向用户一行说明：
  - **Claude 桶归零** → `verifier` / Claude 节点跨桶回退至 `gemini-4-carbon-medium`（审查/攻坚）或 `gemini-4-argon-high`（常规编码）；
  - **Gemini 桶归零** → 核心/编码节点跨桶回退至 `claude-opus-5-5-medium` / `claude-sonnet-5-5-high`，L3 轻量节点回退至 `claude-sonnet-5-5-low` 或 `gpt-oss-120b-medium`（若主控自身同属归零桶，恢复时同步调用 `swarm(action="model", model="...")` 切换主控自身）。

## 2. 角色三级算力分流与跨家族互审

调用 `swarm(action="add", ...)` 创建成员时，按职责显式传入 `model`：

- **L1 核心架构与复杂攻坚（Complex Author）**
  - **职责**：跨模块架构重构、核心算法实现、复杂并发/根因排障。
  - **模型**：默认省略 `model` 以继承主会话模型；若主会话当前为轻量级模型（如 `Gemini 4 Hydrogen` 或 `Gemini 3.8 Flash`），显式升级绑定 `gemini-4-carbon-high`。
- **L2 常规模块编码与单测（Standard Author）**
  - **职责**：边界清晰的模块开发、接口适配、单元测试编写、常规缺陷修复。
  - **模型**：主会话属 Gemini 系时绑定 `gemini-4-argon-high`；属 Claude 系时绑定 `claude-sonnet-5-5-high`。
- **Cross-Verify 独立审查与验收（`verifier`）——强制跨家族异构（默认 `Medium`，高危升 `High`）**
  - **职责**：Diff 审查、边界找茬、测试运行与验收（只报问题不直接改代码）。
  - **默认日常审查（`Medium`）**：必须与被审 Author **跨模型家族绑定**以消除同源思维盲区：
    - 被审 Author 为 Gemini 系 → `verifier` 默认绑定 `claude-opus-5-5-medium`（低水位节流降 `claude-sonnet-5-5-high`；Claude 桶 `0%` 时跨桶退回 `gemini-4-carbon-medium`）；
    - 被审 Author 为 Claude 系 → `verifier` 默认绑定 `gemini-4-carbon-medium`（低水位节流降 `gemini-4-argon-high`；Gemini 桶 `0%` 时跨桶退回 `claude-opus-5-5-medium`）；
    - 混合多家族 Author 时 → 优先绑定与多数 Author 异构的旗舰模型（默认 `claude-opus-5-5-medium`）。
  - **高危场景按需升 `High`**：仅当任务涉及**并发/异步竞态与状态机、跨模块核心架构契约变更、安全鉴权与边界审计**（或常规修复两轮仍未定位深层根因）时，升至 `claude-opus-5-5-high` / `gemini-4-carbon-high`（支持运行中在 `waiting` 状态下热升档）。
- **L3 只读检索、文档同步与后台守护（Lightweight & Daemons）**
  - **职责**：只读代码/日志检索（`research`）、文档与注释更新（`docs`），以及伴生守护节点 `narrator` 与 `monitor`。
  - **模型**：统一显式绑定 `gemini-4-hydrogen-medium`（复杂长链路检索可升至 `gemini-4-hydrogen-high`；未开放 Hydrogen 时回退 `gemini-3.8-flash-medium`，Gemini 桶 `0%` 时跨桶回退 `claude-sonnet-5-5-low`）。

## 3. 运行时模型热切换约束
- 升降级算力或触发低配额告警时，须在目标成员处于 `waiting` 状态时调用 `swarm(action="model", name="...", model="...")` 无损保留上下文切换；若处于 `working` 则等待当前轮次结束。
- **严禁通过 `kill` + 重新 `add` 更换模型**，以免销毁对话历史与 `share`/`branch` 隔离工作区。

## 4. Swarm Token 账单与空转监控
- 原生 Swarm 面板仅展示模型与状态；若需核查各 Worker / Subagent 的 `input_tokens`、`cache_read_tokens`、`output_tokens` 及分模型占比，运行 `../scripts/swarm_tokens.py --host agy --markdown`（或终端 `swarm-tokens --watch`），发现 `monitor`/`narrator` 空转占比过高时及时收敛或清理已完结 Swarm。
