# Swarm Mix — Codex 三级分流与跨家族互审细则

在 Codex 中需要并行拆分子任务或对重要代码改动做独立验收时，按以下三级分流与跨家族互审协议执行（调用 `spawn_agent` 时关闭父会话全文继承 `fork_context=false` / `fork_turns="none"`，传入自包含任务卡）：

## 0. 动态目录发现与能力路由预检（支持纯 Codex 独立运行）
拆分多代理任务前，可执行一次零依赖能力探测（自动解析 `~/.codex/config.toml` 与本地模型目录如 `gpt-6-astra/sol/luna` 的真实上下文窗口与优先级）：
```bash
python3 "<本SKILL所在目录>/scripts/model_router.py" --host codex -m
```
- **纯 Codex 独立环境**：本机未安装 `claude` 或 `agy` CLI 时静默跳过外部通道（退出码 `0`），自动在 Codex 本地可用目录模型（L1 旗舰 / L2 均衡 / L3 轻量）间完成原生分流与同系独立上下文复核。
- **多厂共存专长分流**：检测到本机 `claude`（或 `agy`）可用时，`cross_verify` 自动路由至异构家族审查；验收闭环后可选执行 `model_router.py record` 驱动 L3 贝叶斯权重校准。

## 1. 角色三级算力分流

| 层级 | 执行载体 (`agent_type`) | 模型与思考档位 | 适用职责与边界 |
| :--- | :--- | :--- | :--- |
| **L1 核心攻坚** | 当前主会话 | `~/.codex/config.toml` 主模型（如 `high`） | 跨模块架构重构、核心算法、复杂根因定位与最终统筹 |
| **L2 常规编码** | 本插件 `std-author`（或 `luna-worker`） | L2 均衡模型（如 `gpt-6.1-sol` / `gpt-6-sol`）+ `medium` | 边界清晰的模块开发、接口适配、定向单测编写与局部修复（并行时严格划分互不重叠的文件所有权） |
| **Cross-Verify** | 本机 `claude -p` 只读审查（见第 2 节） | `Claude Opus`（默认 `medium`，高危 `high`；自动回退 `sonnet`） | 强制跨模型家族 Diff 审查与边界找茬，消除 GPT 同源思维盲区（只报问题不改代码） |
| **L3 轻量与巡检** | 本插件 `fast-scout` / `monitor` | L3 轻量模型（如 `gpt-6-luna`）+ `low` 思考档位 | 日志提取、只读符号梳理、文档同步及后台进度巡检（**严禁省略 `agent_type`/`model` 致使 `monitor` 继承 `config.toml` 的 `gpt-6-astra` 旗舰**） |

## 2. 跨家族独立互审协议（GPT 编码 → Claude 审查）

完成非琐碎代码改动（多文件修改、复杂逻辑或高回归风险）后，执行只读非交互调用让 **Claude Opus** 独立审阅改动：

- **默认日常审查（`--effort medium`）**：
  ```bash
  git diff HEAD | env -u CLAUDECODE claude -p \
    --model opus --effort medium --fallback-model sonnet \
    --tools Read,Grep,Glob --permission-prompts none \
    "审查 stdin 中的 git diff（必要时只读查看关联源码核对上下文），仅输出可证实的逻辑缺陷、边界遗漏或回归风险（附文件:行号与触发条件）；若无实质缺陷直说 LGTM。"
  ```
  *(注：若存在未追踪新文件，先将其与 `git diff HEAD` 一并传入 stdin 或在提示词中列出新文件路径供 `Read` 读取。)*

- **高危场景升 `high`**：
  涉及**并发/异步竞态与状态机、跨模块核心架构契约变更、安全鉴权与边界审计**（或常规修复两轮仍未收敛）时，将 `--effort medium` 改为 `--effort high`。

- **配额 / 认证兜底**：
  - `--fallback-model sonnet` 已原生兜底 Opus 过载或限流；
  - 若本机未装 `claude` 或返回 `OAuth session expired` 等认证错误，不反复重试、不自行提取凭据：若 `gemini` CLI 可用可回退只读审查，否则由独立上下文 L2 子代理执行只读复核，并在交付末尾用一行提示用户执行 `claude` 刷新登录态。

## 3. 验收闭环纪律
- 跨家族审查返回的意见视为**待核验证据**：主模型核对真实代码与单测结果后采纳有效项修复，剔除脱离上下文的误报，不盲从也不自评全对。
