# Swarm Mix — Antigravity 原生 Swarm 异构编排细则

本规则代表用户的全局常驻显式授权，**覆盖内置 `swarm` 技能中“未获要求时全员继承主模型（包括 `narrator` 与 `monitor`）”的默认限制**。

## 1. 启动前配额预检、3 层能力路由与跨桶兜底
首次调用 `swarm(action="add")` 前，先调用一次 `swarm(action="models")`，再将其返回的 JSON 传给零依赖路由引擎获取实时分流表（单次 `<150ms` 完成本机多厂商目录探测、5 维画像打分与历史看板战绩贝叶斯校准）：
```bash
python3 "<本SKILL所在目录>/scripts/model_router.py" --host agy --agy-models-json '<swarm_models返回的JSON>' -m
```
- **独立运行零依赖保障**：未安装或未登录 Codex / Claude CLI 时，`model_router.py` 始终以退出码 `0` 静默输出纯 Antigravity 原生双池（Gemini 桶 + Claude/GPT-OSS 桶）最优方案，严禁报错或阻塞流程。
- **按能力画像专长派单**：在配额充足（`≥15%`）时，优先参考 `model_router.py` 推荐表按子任务专长绑定模型（如前端/多模态倾向 `gemini-4-carbon-high`、复杂架构倾向 `claude-opus-5-5-high` / `gemini-4-carbon-high`、高密算法状态机在 Codex 就绪时可选走 `codex-bridge` 调度 `gpt-6-astra`、常规后端重构走 `argon` / `sonnet`、只读检索守护走 `hydrogen`）。
- **账号模型池自适应**：严禁向 `swarm(action="add")` 传入 `models` 返回列表之外的 ID；新发布未知型号由 `model_router.py` 按厂商与梯队先验自动继承能力分。
- **AI Credits 兜底**：若 `quota_note` 显示已由 AI Credits 接管计费，原定模型视作满额可继续正常使用。
- **低水位节流（`<15%` 告警但未归零）**：`model_router.py` 自动对 `<15%` 配额桶施加节流权重：同桶内降档（`Carbon` → `Argon`/`Hydrogen`；`Opus` → `Sonnet`），且当 Claude 桶 `<15%` 而本机 Codex 可用时，自动将跨家族 `cross_verify` 路由至 **GPT 桥接模式**以节省 Claude 余量。
- **配额归零（`0%` 且无 AI Credits）**：自动剔除归零配额桶模型并**跨桶/跨厂切换**，向用户一行说明：
  - **Claude 桶归零** → Codex 就绪时优先由 `codex-bridge` 接管 `verifier`，否则跨桶回退至 `gemini-4-carbon-medium`（审查/攻坚）或 `gemini-4-argon-high`（常规编码）；
  - **Gemini 桶归零** → 核心/编码节点跨桶回退至 `claude-opus-5-5-medium` / `claude-sonnet-5-5-high`，L3 轻量节点回退至 `claude-sonnet-5-5-low` 或 `gpt-oss-120b-medium`（若主控自身同属归零桶，同步调用 `swarm(action="model", model="...")` 切换主控自身）。

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
    - 被审 Author 为 Gemini 系 → `verifier` 默认绑定 `claude-opus-5-5-medium`（Claude 桶 `<15%` 时：若本机 Codex `available: true` 优先改用 `gemini-4-hydrogen-medium` 薄壳 + `codex_bridge.py --mode review` 调度 GPT 审查，否则降档 `claude-sonnet-5-5-high`；`0%` 且无 Codex 时跨桶退回 `gemini-4-carbon-medium`）；
    - 被审 Author 为 Claude 系 → `verifier` 默认绑定 `gemini-4-carbon-medium`（低水位节流降 `gemini-4-argon-high`；Gemini 桶 `0%` 时优先走 Codex 桥接或跨桶退回 `claude-opus-5-5-medium`）；
    - 混合多家族 Author 时 → 优先绑定与多数 Author 异构的旗舰模型（或本机已就绪的 Codex GPT 第三家族实现三方交叉验收）。
  - **高危场景按需升 `High`**：仅当任务涉及**并发/异步竞态与状态机、跨模块核心架构契约变更、安全鉴权与边界审计**（或常规修复两轮仍未定位深层根因）时，升至 `claude-opus-5-5-high` / `gemini-4-carbon-high` / `--effort high`（支持运行中在 `waiting` 状态下热升档）。
- **GPT / Codex 薄壳桥接节点（仅当 `model_router.py` 显示 `codex` 池可用或 `codex_bridge.py --probe` 返回 `available: true` 时启用）**
  - **适用场景**：`model_router.py` 将高密算法（`algorithm_state`）或低 Claude 配额下的 `cross_verify` 路由至 `codex-bridge`，或用户点名使用 GPT / Codex。
  - **执行方式**：成员绑定 L3 `gemini-4-hydrogen-medium` 作为看板收发薄壳，在 `brief` 中写明通过 `run_command` 调用（可按路由推荐传入 `--model <slug>`，省略则继承 Codex 默认旗舰）：
    - 只读审查：`python3 "<本SKILL所在目录>/scripts/codex_bridge.py" --mode review --cwd "<工作目录>" --effort <medium|high> "<审查重点>"`
    - 编码修改：`python3 "<本SKILL所在目录>/scripts/codex_bridge.py" --mode exec --sandbox workspace-write --cwd "<工作目录>" --effort <medium|high> "<编码任务>"`
  - **无损降级兜底**：若命令输出含 `[CODEX_BRIDGE_UNAVAILABLE]`，薄壳节点不得用 Hydrogen 自行冒充 GPT 结论，须立即发帖告知主控；主控在该成员 `waiting` 时调用 `swarm(action="model", ...)` 热切换为 `model_router.py` 给出的 `Fallback` 原生模型继续完成任务。
- **L3 只读检索、文档同步与后台守护（Lightweight & Daemons）**
  - **职责**：只读代码/日志检索（`research`）、文档与注释更新（`docs`），以及伴生守护节点 `narrator` 与 `monitor`。
  - **模型**：统一显式绑定 `gemini-4-hydrogen-medium`（复杂长链路检索可升至 `gemini-4-hydrogen-high`；未开放 Hydrogen 时回退 `gemini-3.8-flash-medium`，Gemini 桶 `0%` 时跨桶回退 `claude-sonnet-5-5-low`）。**严禁在 `monitor` / `narrator` / `research` 节点内调用 `codex_bridge.py` 或挂载旗舰模型**（即使 `codex_bridge.py` 被误赋予 `--role monitor/scout`，也会强制降级至 L3 轻量模型）。

## 3. 运行时模型热切换约束
- 升降级算力或触发低配额告警时，须在目标成员处于 `waiting` 状态时调用 `swarm(action="model", name="...", model="...")` 无损保留上下文切换；若处于 `working` 则等待当前轮次结束。
- **严禁通过 `kill` + 重新 `add` 更换模型**，以免销毁对话历史与 `share`/`branch` 隔离工作区。

## 4. Swarm Token 账单与空转监控
- 原生 Swarm 面板仅读取 `members.json` 的宿主薄壳模型（如 Codex 桥接节点的 `hydrogen` 壳）；启用 GPT/Codex 混编或需查看真实执行模型与分模型 Token 账单时，优先在回复中附上 `[Swarm Mix](sidecar://swarm-mix/swarm_board/)`（自动将桥接成员主模型提升显示为实际 `gpt-*` 型号并标注 `shell`，同时精确拆分双池 Token 消耗），或运行 `../scripts/swarm_tokens.py --host agy --markdown`（终端 `swarm-tokens --watch`），发现 `monitor`/`narrator` 空转占比过高时及时收敛或清理已完结 Swarm。
