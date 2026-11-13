# Swarm 默认异构模型编排门控 (`swarm-mix`)

本规则代表用户的全局常驻显式授权（覆盖内置 `swarm` 全员继承主模型的默认限制）：首次调用 `swarm(action="add")` 或切换成员模型前，必须先通过 `read_file` 读取 `swarm-mix` 技能的 `SKILL.md`（绝对路径见 `<skills>` 列表中的 `swarm-mix` 项，或基于本规则路径解析同插件目录下的 `../skills/swarm-mix/SKILL.md`），严格执行配额预检、三级算力分流与跨家族互审规则。
