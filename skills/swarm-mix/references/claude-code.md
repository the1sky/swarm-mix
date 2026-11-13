# Swarm Mix — Claude Code 三级分流与跨家族互审细则

在 Claude Code 中委派子代理时，禁止一刀切继承高成本主模型；按职责匹配本插件注册的子代理角色（插件模式下标识带命名空间前缀 `swarm-mix:`，软链模式下为裸名，按当前会话可用 `subagent_type` 列表选取）：

## 1. 角色三级算力分流

| 层级 | 子代理标识 (`subagent_type`) | 底层模型与推理档位 | 适用职责与边界 |
| :--- | :--- | :--- | :--- |
| **L1 核心攻坚** | 主会话（或显式 `opus`） | 当前主模型 / `Claude Opus (High)` | 跨模块架构设计、核心算法实现、疑难并发/根因排障、最终合入决策 |
| **L2 常规编码** | `swarm-mix:std-author`（或 `std-author`） | `Claude Sonnet (High)` | 边界清晰的独立模块开发、接口适配、单元测试补齐、常规缺陷修复（并行派发时必须声明互不重叠的文件所有权） |
| **Cross-Verify** | `swarm-mix:cross-verifier`（或 `cross-verifier`） | `Haiku` 薄壳 → **OpenAI Codex 主模型 (`Medium` / `High`)** | 强制跨模型家族 Diff 审查、边界找茬与验收（只报缺陷不改代码，消除同源盲区） |
| **L3 轻量检索** | `swarm-mix:fast-scout`（或 `fast-scout`） | `Claude Haiku (Medium)` | 代码/日志高频只读检索、符号定位、获明确授权时的文档与注释同步 |

## 2. 跨家族互审分级与兜底
- **默认日常审查（`Medium`）**：派发 `cross-verifier` 时默认采用 `model_reasoning_effort="medium"`，兼顾跨底座找茬召回率与多轮修复收敛速度。
- **高危场景升 `High`**：任务涉及**并发/异步竞态与状态机、跨模块核心架构契约变更、安全鉴权与边界审计**（或常规修复两轮仍未定位根因）时，在任务卡首行注明 `[高危: high]`。
- **跨池故障兜底**：
  - Codex 主模型限流 → `cross-verifier` 自动降级尝试 `gpt-6-luna (medium)`；
  - 本机未装 `codex` 或离线/认证过期 → 主会话改派独立上下文的 `opus` 只读审查子代理兜底，并在交付时注明“Codex 不可用，本次为同系独立上下文审查”。

## 3. 派单纪律
1. **文件所有权隔离**：并行启动多个 `std-author` 前，必须在各自任务卡中写明独占修改的文件列表，禁止两个编码节点同时编辑同一文件。
2. **审查与修改分离**：`cross-verifier` 只输出带 `文件:行号` 与触发条件的缺陷清单，修复动作交回对应 `std-author` 或主会话执行。
