<h1>
  <img src="./assets/logo.svg" align="left" width="44" height="44" alt="Swarm Mix Logo" />
  Swarm Mix (<code>swarm-mix</code>)
</h1>

**English** | [简体中文](./README.zh-CN.md)

A unified multi-host plugin for **Google Antigravity**, **Claude Code**, and **OpenAI Codex** that enforces **3-tier model routing** and **mandatory cross-family code verification**.

## Why Swarm Mix?

- **Stop burning flagship quota on chores**: Routes complex architecture to flagship models (`L1`), module coding to balanced models (`L2`), and read-only search/docs/daemons to fast lightweight models (`L3`).
- **Eliminate single-family blind spots**: Forces independent code review (`verifier`) onto a **different model family** (Gemini ↔ Claude, Claude ↔ OpenAI Codex) at `Medium` effort by default, stepping up to `High` for concurrency, core architecture, or security changes.
- **Per-agent & per-model token ledger (`swarm-tokens`)**: Fills the missing token visibility gap in native Swarm UIs — inspect uncached input, cache hit rate, output tokens, model-tier shares, and runaway daemon hotspots across Antigravity, Claude Code, and Codex.

---

## Install

### 1. Natural Language Install (Copy & Paste to Your Agent)

Send directly in **Antigravity**, **Claude Code**, or **OpenAI Codex** (clones into `~/.local/share/swarm-mix` so your current workspace stays clean):

**Auto-install across all detected hosts (Recommended)**
```text
Install swarm-mix from https://github.com/the1sky/swarm-mix.git into ~/.local/share/swarm-mix (pull latest if it already exists), run ./install.sh, enable the plugin if needed, and run ~/.local/bin/swarm-router --host auto -m to verify.
```

**中文自然语言一键安装 / 更新（直接发给任意 Agent）**
```text
请帮我从 https://github.com/the1sky/swarm-mix.git 安装或更新 swarm-mix（克隆到 ~/.local/share/swarm-mix 避免污染当前工作区，若已存在则 git pull），执行 ./install.sh 完成配置，并运行 ~/.local/bin/swarm-router --host auto -m 验证生效。
```

**Install into current host only (Standalone)**
```text
Install swarm-mix from https://github.com/the1sky/swarm-mix.git into this agent client only (do not modify other agent tools on my machine), then verify with model_router.py --host auto -m.
```

### 2. One-Liner (Auto-Detects Antigravity, Claude Code & Codex)

```bash
git clone https://github.com/the1sky/swarm-mix.git && cd swarm-mix && ./install.sh
```

> Live-sync symlink mode for local customization: `./install.sh --link`

### 3. Host-Native Install

**Claude Code**
```bash
claude plugin marketplace add https://github.com/the1sky/swarm-mix.git
claude plugin install swarm-mix@swarm-mix-local
```

**Google Antigravity**
```bash
git clone https://github.com/the1sky/swarm-mix.git ~/.gemini/config/plugins/swarm-mix
# Or via CLI: agy plugin install ./swarm-mix
# Or per-repo team sharing: git submodule add https://github.com/the1sky/swarm-mix.git .agents/plugins/swarm-mix
```

**OpenAI Codex**
```bash
git clone https://github.com/the1sky/swarm-mix.git ~/.codex/plugins/swarm-mix
~/.codex/plugins/swarm-mix/install.sh
```

**Skills CLI Only (`npx skills`)**
```bash
npx skills add https://github.com/the1sky/swarm-mix --skill swarm-mix -g
```

---

## Default Routing Matrix

| Tier | Google Antigravity (`swarm`) | Claude Code (`Agent`) | OpenAI Codex (`spawn_agent` + CLI) |
| :--- | :--- | :--- | :--- |
| **L1 Core / Arch** | Inherit session model *(or `Carbon High` if on lightweight)* | Session model / `Opus (High)` | Session default model (`High`) |
| **L2 Standard Coding** | `Gemini 4 Argon (High)` / `Claude Sonnet 5.5 (High)` | `std-author` → `Sonnet (High)` | `std-author` (`Medium`) |
| **Cross-Family Review** | Gemini ↔ `Claude Opus 5.5` / `Gemini 4 Carbon` *(+ optional local `Codex` GPT bridge)* | `cross-verifier` → `codex exec review` *(`Medium` / `High`)* | `claude -p --model opus` *(`medium` / `high`, fallback `sonnet`)* |
| **L3 Scout / Docs / Daemons** | `Gemini 4 Hydrogen (Medium)` *(incl. `narrator` & `monitor`)* | `fast-scout` → `Haiku (Medium)` | `fast-scout` (`Low`) |

## Token Observability (`Swarm Mix` Sidecar UI & `swarm-tokens`)

- **3-Layer Adaptive Capability Routing (`swarm-router`)**: Zero-token local catalog discovery (context windows, effort levels, live quota `%`) + 5D benchmark priors + passive Bayesian outcome calibration — works 100% standalone in any single host and automatically unlocks cross-vendor routing when peer CLIs are present.
- **Native Antigravity Sidecar Panel**: Open **Swarm Mix** from the conversation `+` header menu (or `[Swarm Mix](sidecar://swarm-mix/swarm_board/)`) for live per-tier allocation bars, inline member token burn, session switching, board filtering, and the **Models** capability/routing tab.
- **Cross-Host CLIs (`swarm-tokens` & `swarm-router`)**: Ask your agent *"Show token usage for my latest Swarm"*, or run in your terminal:

```bash
swarm-router --host auto -m    # 3-layer capability discovery & optimal task routing table
swarm-tokens                   # Per-agent & per-model token ledger for the latest Swarm
swarm-tokens --watch 3         # Live auto-refreshing dashboard while a Swarm runs
swarm-tokens --list -n 10      # List recent multi-agent sessions across AGY / Claude / Codex
swarm-tokens --host agy -m     # Export Markdown tables (or --json for automation)
```

## Notes

- **Prompt overrides win**: Explicitly naming a model in chat always overrides this plugin.
- **Antigravity prerequisite**: Ensure the built-in **Swarm** plugin is enabled in *Settings → Customizations*.
- **Graceful fallbacks**: Automatically adapts to account model availability, switches across quota buckets at `0%`, and falls back to isolated same-family review if a peer CLI (`codex` / `claude`) is unavailable.

## License

[MIT](./LICENSE)
