# Swarm Mix — Antigravity 原生 Swarm 异构编排细则

本规则代表用户的全局常驻显式授权，**覆盖内置 `swarm` 技能中“未获要求时全员继承主模型”以及“只要存在 `swarm_monitor` 技能就默认创建 `monitor`”的两项默认行为**。

## 1. 启动前配额预检、3 层能力路由与跨桶兜底
首次调用 `swarm(action="add")` 前，先调用一次 `swarm(action="models")`，再将其返回的 JSON 传给零依赖路由引擎获取实时分流表（单次 `<150ms` 完成本机多厂商目录探测、5 维画像打分与历史看板战绩贝叶斯校准）：
```bash
python3 "<本SKILL所在目录>/scripts/model_router.py" --host agy --agy-models-json '<swarm_models返回的JSON>' -m
```
- **独立运行零依赖保障**：未安装或未登录 Codex / Claude CLI 时，`model_router.py` 始终以退出码 `0` 静默输出纯 Antigravity 原生双池（Gemini 桶 + Claude/GPT-OSS 桶）最优方案，严禁报错或阻塞流程。
- **按能力画像专长派单**：在配额充足（`≥15%`）时，优先参考 `model_router.py` 推荐表按子任务专长绑定模型（如前端/多模态默认 `gemini-4-carbon-medium`、复杂架构与并发攻坚用 `claude-opus-5-5-high` / `gemini-4-carbon-high`、高密算法状态机在 Codex 就绪时可选走 `codex-bridge` 调度 `gpt-6-astra`、常规后端重构与单测默认 `gemini-4-argon-medium` / `claude-sonnet-5-5-medium`、只读检索守护走 `hydrogen`），避免常规编码全员继承 `High` 档引发超长 `thinking` 空转。
- **账号模型池自适应**：严禁向 `swarm(action="add")` 传入 `models` 返回列表之外的 ID；新发布未知型号由 `model_router.py` 按厂商与梯队先验自动继承能力分。
- **AI Credits 兜底**：若 `quota_note` 显示已由 AI Credits 接管计费，原定模型视作满额可继续正常使用。
- **同宿主同桶共享认知（防伪节流）**：Antigravity 原生池按厂商桶共享配额——所有 `Gemini` 型号共用同一配额桶，所有 `Claude` 与 `GPT-OSS` 型号共用同一配额桶；同桶内换型号只能降低单轮消耗速率、**不能换到新配额池**。唯有通过桥接切入本机独立订阅的 **Codex CLI**（`codex-bridge`）或 **Claude CLI**（`claude-cli`），才是真正零消耗宿主低水位桶的跨池分流（薄壳仅消耗极微量 `hydrogen` 收发看板）。
- **低水位节流与强制跨池分流（`<15%` 告警但未归零）**：`model_router.py` 自动对 `<15%` 原生桶施加 `-0.18` 节流权重，并解锁外部 `codex-bridge` 与 `claude-cli` 独立池：
  - 当路由表将 **`architecture` / `frontend_ui` / `backend_refactor`（Author 编码）** 或 **`cross_verify`（审查）** 推荐为 `codex-bridge`（如 `gpt-6.1-sol (medium)` / `gpt-6-astra`）时，主控**必须**将对应 Worker 设为 `gemini-4-hydrogen-medium` 薄壳并按推荐 `--model` 调用桥接，**严禁无视路由表仍给 Author 绑 `<15%` 的原生 `argon`/`carbon`**；
  - 若 `codex_bridge.py` 因单次扫描文件过多或 `high` 档耗时触发超时，桥接脚本会自动在满额 Codex 池内降档重试高吞吐 `gpt-6.1-sol (medium)`；若仍超时，主控应拆分文件范围重派桥接，仅在外部 CLI 确实离线/无认证时才回退原生模型。
- **配额归零（`0%` 且无 AI Credits）**：自动剔除归零配额桶模型并**跨桶/跨厂切换**，向用户一行说明：
  - **Claude 桶归零** → Codex 就绪时优先由 `codex-bridge` 接管 `verifier`，否则跨桶回退至 `gemini-4-carbon-medium`（审查/攻坚）或 `gemini-4-argon-high`（常规编码）；
  - **Gemini 桶归零** → 核心/编码节点跨桶回退至 `claude-opus-5-5-medium` / `claude-sonnet-5-5-high`，L3 轻量节点回退至 `claude-sonnet-5-5-low` 或 `gpt-oss-120b-medium`（若主控自身同属归零桶，同步调用 `swarm(action="model", model="...")` 切换主控自身）。

## 2. 角色三级算力分流与跨家族互审

调用 `swarm(action="add", ...)` 创建成员时，按职责显式传入 `model`：

- **L1 核心架构与复杂攻坚（Complex Author）**
  - **职责**：跨模块架构重构、核心算法实现、复杂并发/根因排障。
  - **模型**：默认省略 `model` 以继承主会话模型；若主会话当前为轻量级模型（如 `Gemini 4 Hydrogen` 或 `Gemini 3.8 Flash`），显式升级绑定 `gemini-4-carbon-high`。
- **L2 常规模块编码、UI 实现与单测（Standard Author · 默认 `Medium`）**
  - **职责**：边界清晰的模块开发、前端 UI 组件实现、接口适配、单元测试编写、常规缺陷修复。
  - **模型**：原生桶充足（`≥15%`）时，Gemini 系绑定 `gemini-4-argon-medium`（高视觉多模态 UI 可选 `gemini-4-carbon-medium`），Claude 系绑定 `claude-sonnet-5-5-medium`；**当原生桶处于 `<15%` 低水位且 `model_router.py` 推荐 `codex-bridge`（如 `gpt-6.1-sol (medium)`）时，必须改用 `gemini-4-hydrogen-medium` 薄壳 + `codex_bridge.py --mode exec --model gpt-6.1-sol --effort medium` 执行跨池编码**。仅在连续两轮修复未果或涉及复杂并发状态机时升 `High`。
  - **防空转纪律**：在 `brief` 中明确要求 Worker 优先通过 `grep`、单测与类型检查实测收敛，严禁在 `thinking` 中逐行预写完整文件草稿或人肉模拟长调用链。
- **Cross-Verify 独立审查与验收（`verifier`）——强制跨家族异构（默认 `Medium`，高危升 `High`）**
  - **职责**：Diff 审查、边界找茬、测试运行与验收（只报问题不直接改代码）。
  - **默认日常审查（`Medium`）**：必须与被审 Author **跨模型家族绑定**以消除同源思维盲区：
    - 被审 Author 为 Gemini 系 → `verifier` 默认绑定 `claude-opus-5-5-medium`（Claude 桶 `<15%` 时：若本机 Codex `available: true` 优先改用 `gemini-4-hydrogen-medium` 薄壳 + `codex_bridge.py --mode review` 调度 GPT 审查，否则降档 `claude-sonnet-5-5-high`；`0%` 且无 Codex 时跨桶退回 `gemini-4-carbon-medium`）；
    - 被审 Author 为 Claude 系 → `verifier` 默认绑定 `gemini-4-carbon-medium`（低水位节流降 `gemini-4-argon-high`；Gemini 桶 `0%` 时优先走 Codex 桥接或跨桶退回 `claude-opus-5-5-medium`）；
    - 混合多家族 Author 时 → 优先绑定与多数 Author 异构的旗舰模型（或本机已就绪的 Codex GPT 第三家族实现三方交叉验收）。
  - **高危场景按需升 `High`**：仅当任务涉及**并发/异步竞态与状态机、跨模块核心架构契约变更、安全鉴权与边界审计**（或常规修复两轮仍未定位深层根因）时，升至 `claude-opus-5-5-high` / `gemini-4-carbon-high` / `--effort high`（支持运行中在 `waiting` 状态下热升档）。
- **GPT / Codex 薄壳桥接节点（仅当 `model_router.py` 显示 `codex` 池可用或 `codex_bridge.py --probe` 返回 `available: true` 时启用）**
  - **适用场景**：`model_router.py` 将高密算法（`algorithm_state`）、低水位 `<15%` 下的 `architecture` / `frontend_ui` / `backend_refactor`（如 `gpt-6.1-sol`）或 `cross_verify`（如 `gpt-6-astra`）路由至 `codex-bridge`，或用户点名使用 GPT / Codex。
  - **执行方式**：成员绑定 L3 `gemini-4-hydrogen-medium`（或 `low`）作为看板收发薄壳，在 `brief` 中写明通过 `run_command` 调用（**显式传入路由表推荐的 `--model <slug>`**）：
    - 只读审查：`python3 "<本SKILL所在目录>/scripts/codex_bridge.py" --mode review --cwd "<工作目录>" --model <slug> --effort <medium|high> "<审查重点>"`
    - 编码修改：`python3 "<本SKILL所在目录>/scripts/codex_bridge.py" --mode exec --sandbox workspace-write --cwd "<工作目录>" --model <slug> --effort <medium|high> "<编码任务>"`
  - **同池降档与无损兜底**：若旗舰型号超时或限流，`codex_bridge.py` 会自动在同一 Codex 池内降档重试高吞吐 `gpt-6.1-sol (medium)`；仅当同池重试仍输出 `[CODEX_BRIDGE_UNAVAILABLE]` 时，薄壳节点不得自行冒充 GPT 结论，须发帖告知主控拆分任务范围或在该成员 `waiting` 时热切换至 `Fallback` 模型。
- **L3 只读检索、文档同步与按需守护（Lightweight & On-Demand Daemons）**
  - **默认禁用常驻 `monitor` 轮询守护**：常规 Swarm（`≤ 4` 个业务 Worker 且非无人值守过夜任务）**严禁创建 `monitor`**（其 `*/3` Cron 唤醒卡在 5 分钟缓存 TTL 边缘，极易引发连续整窗缓存失配与深水区空耗）；仅在 `≥ 5` 个业务 Worker 的超长无人值守任务（如 `/goal`）或用户显式要求盯盘时，在 `brief` 中附 `[ALLOW_MONITOR]` 标记按需创建，且一旦业务 Worker 收口必须同轮立即 `kill` `monitor`。
  - **模型**：只读检索（`research`）、文档同步（`docs`）及按需启用的 `narrator` / `monitor` 统一显式绑定 `gemini-4-hydrogen-medium`（复杂长链路检索可升至 `gemini-4-hydrogen-high`；未开放 Hydrogen 时回退 `gemini-3.8-flash-medium`，Gemini 桶 `0%` 时跨桶回退 `claude-sonnet-5-5-low`）。**严禁在 `monitor` / `narrator` / `research` 节点内调用 `codex_bridge.py` 或挂载旗舰模型**（即使 `codex_bridge.py` 被误赋予 `--role monitor/scout`，也会强制降级至 L3 轻量模型）。

## 3. 运行时模型热切换与防击穿收束约束
- **初始一步到位，禁 `>30K` 随意切模**：Prompt Cache 与具体模型强绑定——创建成员时须按 `model_router.py` 一步到位绑定目标模型，**严禁非配额原因对已累积 `>30K` 上下文的活跃成员调用 `swarm(action="model")` 做横向或降档试探切模**。仅限以下三种合法场景在目标成员 `waiting` 时无损热切：① 触发 `429` / 配额归零（`0%`）跨桶兜底；② 连续两轮修复未果需升 `-high` 攻坚；③ 用户口头明确要求换模。
- **严禁仅因“换模型”而 `kill` + 重新 `add`**，以免无谓销毁低水位工作区。
- **Worker `≥145K` 自动落盘换血（Swarm 子节点的等效 `/compact`）**：后台 Worker 无 `/compact` 入口；当某成员上下文达 `145K`（`Context-Guard` 会自动向主控告警并拦截新轮次派单），若仍需后续开发或修复，主控**必须执行落盘换血**：确认该节点代码已 `git commit`（`share`/`branch` 工作区先合流至主仓或目标分支；若尚有未提交改动，可在 `post_message` 附 `[ROTATE_LAND]` 让其先提交落盘）→ `swarm(action="kill", name="<name>")` → `swarm(action="add", name="<name>-2", ...)`，`brief` 仅传「当前 commit SHA + 待处理问题摘要 + `read_board(since=<当前最新#seq>)`」（严禁 `since: 0` 拉取历史旧帖）。
- **跨轮次收束与防看板堆积**：同一会话完结一轮 Swarm 后，若主控上下文已超 `120K`，须提示用户先执行 `/compact`；若开启**全新独立需求的新一轮 Swarm**，主动建议用户**新开会话**（`/compact` 不会清空旧轮次留言板历史 `#seq`，单会话连跑多轮会导致新成员拉取百条旧帖直冲深水区）。

## 4. Swarm Token 账单与空转监控
- 原生 Swarm 面板仅读取 `members.json` 的宿主薄壳模型（如 Codex 桥接节点的 `hydrogen` 壳）；启用 GPT/Codex 混编或需查看真实执行模型与分模型 Token 账单时，优先在回复中附上 `[Swarm Mix](sidecar://swarm-mix/swarm_board/)`（自动将桥接成员主模型提升显示为实际 `gpt-*` 型号并标注 `shell`，同时精确拆分双池 Token 消耗），或运行 `../scripts/swarm_tokens.py --host agy --markdown`（终端 `swarm-tokens --watch`），发现 `monitor`/`narrator` 空转占比过高时及时收敛或清理已完结 Swarm。
