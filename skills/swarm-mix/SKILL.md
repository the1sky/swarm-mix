---
name: swarm-mix
description: 跨平台多代理三级算力分流与跨模型家族强制互审规则（支持 Antigravity / Claude Code / Codex 同源安装）。每次调用多代理工具（Antigravity swarm、Claude Code Agent/Task、Codex spawn_agent）或执行跨模型代码验收前必须读取。
---

# Swarm Mix：多端统一异构编排入口 (`swarm-mix` v2.0)

本技能将「**L1 旗舰攻坚 / L2 均衡编码 / L3 轻量检索**」三级算力分流与「**跨模型家族强制互审（默认 Medium，并发/架构/安全高危升 High）**」统一封装为单包多宿主规范。简单问答与单点明确小改由主会话直接完成（子代理数 = 0）；用户单次对话口头指定的模型要求始终高于本规则。

## 按当前宿主环境按需读取唯一对应细则（禁止全读）

识别你当前所处的运行环境，**仅读取对应的那一份协议文件**并严格执行：

1. **Antigravity 环境**（当前工具列表含原生 `swarm` 工具）：
   → 读取同目录下的 [references/antigravity.md](references/antigravity.md)
2. **Claude Code 环境**（当前工具列表含 `Agent` / `Task` 工具，不含原生 `swarm`）：
   → 读取同目录下的 [references/claude-code.md](references/claude-code.md)
3. **Codex 环境**（当前工具列表含 `spawn_agent` / `start_delegated_task` 或处于 OpenAI Codex 会话）：
   → 读取同目录下的 [references/codex.md](references/codex.md)
