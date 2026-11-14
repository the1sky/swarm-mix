#!/usr/bin/env bash
# Swarm Mix Multi-Host Installer (macOS Bash 3.2+ & Linux compatible)
# Usage:
#   ./install.sh          # Copy/install into all detected hosts (Antigravity, Claude Code, Codex)
#   ./install.sh --link   # Symlink all detected hosts to this single source directory (live sync)

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="copy"
if [ "${1:-}" = "--link" ]; then
  MODE="link"
fi

sync_item() {
  local src="$1"
  local dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [ "$MODE" = "link" ]; then
    if [ "$src" = "$dst" ]; then
      return 0
    fi
    rm -rf "$dst"
    ln -s "$src" "$dst"
  else
    if [ "$src" != "$dst" ]; then
      rm -rf "$dst"
      cp -R "$src" "$dst"
    fi
  fi
}

echo "Installing swarm-mix ($MODE mode) from: $SRC_DIR"

# 1. Antigravity (~/.gemini/config/plugins/swarm-mix)
if [ -d "$HOME/.gemini" ] || command -v agy >/dev/null 2>&1; then
  AGY_DST="$HOME/.gemini/config/plugins/swarm-mix"
  sync_item "$SRC_DIR" "$AGY_DST"
  echo "  [✔] Antigravity -> $AGY_DST"
fi

# 2. Claude Code (Native Plugin Marketplace + fallback skill/agents)
if [ -d "$HOME/.claude" ] || command -v claude >/dev/null 2>&1; then
  if command -v claude >/dev/null 2>&1 && [ "$MODE" = "copy" ]; then
    claude plugin marketplace add "$SRC_DIR" >/dev/null 2>&1 || true
    if claude plugin install swarm-mix@swarm-mix-local >/dev/null 2>&1; then
      echo "  [✔] Claude Code -> plugin swarm-mix@swarm-mix-local enabled"
    else
      sync_item "$SRC_DIR/skills/swarm-mix" "$HOME/.claude/skills/swarm-mix"
      for f in "$SRC_DIR"/platforms/claude/agents/*.md; do
        sync_item "$f" "$HOME/.claude/agents/$(basename "$f")"
      done
      echo "  [✔] Claude Code -> synced ~/.claude/skills & ~/.claude/agents"
    fi
  else
    # --link mode: symlink directly to single source of truth for zero-restart live edits
    sync_item "$SRC_DIR/skills/swarm-mix" "$HOME/.claude/skills/swarm-mix"
    for f in "$SRC_DIR"/platforms/claude/agents/*.md; do
      sync_item "$f" "$HOME/.claude/agents/$(basename "$f")"
    done
    echo "  [✔] Claude Code -> symlinked to $SRC_DIR"
  fi
fi

# 3. OpenAI Codex (~/.codex/plugins/swarm-mix + skills & portable agents)
if [ -d "$HOME/.codex" ] || command -v codex >/dev/null 2>&1; then
  sync_item "$SRC_DIR" "$HOME/.codex/plugins/swarm-mix"
  sync_item "$SRC_DIR/skills/swarm-mix" "$HOME/.codex/skills/swarm-mix"
  mkdir -p "$HOME/.codex/agents"
  for f in "$SRC_DIR"/platforms/codex/agents/*.toml; do
    base="$(basename "$f")"
    sync_item "$f" "$HOME/.codex/agents/$base"
  done
  echo "  [✔] Codex       -> $HOME/.codex/{plugins,skills,agents}"
fi

# 4. CLI Inspectors (~/.local/bin/swarm-tokens & ~/.local/bin/swarm-router)
chmod +x \
  "$SRC_DIR/skills/swarm-mix/scripts/swarm_tokens.py" \
  "$SRC_DIR/skills/swarm-mix/scripts/codex_bridge.py" \
  "$SRC_DIR/skills/swarm-mix/scripts/model_router.py"
mkdir -p "$HOME/.local/bin"
if [ "$MODE" = "link" ]; then
  ln -sf "$SRC_DIR/skills/swarm-mix/scripts/swarm_tokens.py" "$HOME/.local/bin/swarm-tokens"
  ln -sf "$SRC_DIR/skills/swarm-mix/scripts/model_router.py" "$HOME/.local/bin/swarm-router"
else
  cp -f "$SRC_DIR/skills/swarm-mix/scripts/swarm_tokens.py" "$HOME/.local/bin/swarm-tokens"
  cp -f "$SRC_DIR/skills/swarm-mix/scripts/model_router.py" "$HOME/.local/bin/swarm-router"
  chmod +x "$HOME/.local/bin/swarm-tokens" "$HOME/.local/bin/swarm-router"
fi
echo "  [✔] CLI         -> $HOME/.local/bin/{swarm-tokens,swarm-router}"

echo "Done."
