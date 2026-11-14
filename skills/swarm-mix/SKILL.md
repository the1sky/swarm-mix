---
name: swarm-mix
description: 跨平台多代理三级算力分流与跨模型家族强制互审规则（支持 Antigravity / Claude Code / Codex 同源安装）。每次调用多代理工具（Antigravity swarm、Claude Code Agent/Task、Codex spawn_agent）或执行跨模型代码验收前必须读取。
---

# Swarm Mix：多端统一异构编排入口 (`swarm-mix` v2.2)

本技能将「**L1 旗舰攻坚 / L2 均衡编码 / L3 轻量检索**」三级算力分流、「**跨模型家族强制互审（默认 Medium，并发/架构/安全高危升 High）**」与「**多端分模型 Token 消耗审计 (`swarm-tokens`)**」统一封装为单包多宿主规范。简单问答与单点明确小改由主会话直接完成（子代理数 = 0）；用户单次对话口头指定的模型要求始终高于本规则。

## 按当前宿主环境按需读取唯一对应细则（禁止全读）

识别你当前所处的运行环境，**仅读取对应的那一份协议文件**并严格执行：

1. **Antigravity 环境**（当前工具列表含原生 `swarm` 工具）：
   → 读取同目录下的 [references/antigravity.md](references/antigravity.md)
2. **Claude Code 环境**（当前工具列表含 `Agent` / `Task` 工具，不含原生 `swarm`）：
   → 读取同目录下的 [references/claude-code.md](references/claude-code.md)
3. **Codex 环境**（当前工具列表含 `spawn_agent` / `start_delegated_task` 或处于 OpenAI Codex 会话）：
   → 读取同目录下的 [references/codex.md](references/codex.md)

## Token 消耗审计与实时盯盘 (`swarm-tokens`)

当用户询问当前或历史 Swarm / 多代理任务的 Token 消耗、缓存命中率或分模型算力占比时，直接运行同目录下的零依赖脚本 [scripts/swarm_tokens.py](scripts/swarm_tokens.py)（安装后亦注册为全局命令 `swarm-tokens`）：

```bash
python3 "<本SKILL所在目录>/scripts/swarm_tokens.py" --markdown          # 查看最近一次多代理会话账单（可选 --host agy|claude|codex 或 --id <前缀>）
python3 "<本SKILL所在目录>/scripts/swarm_tokens.py" --list -n 10        # 列出近期跨端 Swarm / 子代理会话总览
swarm-tokens --watch 3                                                  # 终端每 3 秒实时刷新运行中 Swarm 的各 Worker 消耗
```
