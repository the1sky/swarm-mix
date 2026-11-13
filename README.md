# Swarm Mix (`swarm-mix`)

English | [简体中文](./README.zh-CN.md)

A unified multi-host plugin for **Google Antigravity**, **Claude Code**, and **OpenAI Codex** that enforces **3-tier model routing** and **mandatory cross-family code verification**.

## Why Swarm Mix?

- **Stop burning flagship quota on chores**: Routes complex architecture to flagship models (`L1`), module coding to balanced models (`L2`), and read-only search/docs/daemons to fast lightweight models (`L3`).
- **Eliminate single-family blind spots**: Forces independent code review (`verifier`) onto a **different model family** (Gemini ↔ Claude, Claude ↔ OpenAI Codex) at `Medium` effort by default, stepping up to `High` for concurrency, core architecture, or security changes.

---

## Install

### 1. Ask Your Agent (Zero Manual Setup)

Paste into Antigravity, Claude Code, or Codex:

```text
Clone https://github.com/the1sky/swarm-mix.git and run ./install.sh to install swarm-mix across all detected agent hosts on my machine.
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
| **Cross-Family Review** | Gemini ↔ `Claude Opus 5.5` / `Gemini 4 Carbon` *(`Medium`, `High` for high-risk)* | `cross-verifier` → `codex exec review` *(`Medium` / `High`)* | `claude -p --model opus` *(`medium` / `high`, fallback `sonnet`)* |
| **L3 Scout / Docs / Daemons** | `Gemini 4 Hydrogen (Medium)` *(incl. `narrator` & `monitor`)* | `fast-scout` → `Haiku (Medium)` | `fast-scout` (`Low`) |

## Notes

- **Prompt overrides win**: Explicitly naming a model in chat always overrides this plugin.
- **Antigravity prerequisite**: Ensure the built-in **Swarm** plugin is enabled in *Settings → Customizations*.
- **Graceful fallbacks**: Automatically adapts to account model availability, switches across quota buckets at `0%`, and falls back to isolated same-family review if a peer CLI (`codex` / `claude`) is unavailable.

## License

[MIT](./LICENSE)
