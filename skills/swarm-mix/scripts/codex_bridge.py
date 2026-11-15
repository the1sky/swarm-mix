#!/usr/bin/env python3
"""
codex_bridge.py — Optional OpenAI Codex CLI bridge for swarm-mix (Zero dependencies).

Safely probes or invokes a local `codex` CLI subscription from thin-shell Swarm workers,
emitting structured `[CODEX_BRIDGE_USAGE]` telemetry for `swarm-tokens` and the Sidecar UI.
Degrades cleanly with exit code 0 if `codex` is not installed, logged out, or unreachable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

USAGE_TAG = "[CODEX_BRIDGE_USAGE]"
UNAVAILABLE_TAG = "[CODEX_BRIDGE_UNAVAILABLE]"
MODEL_RE = re.compile(r'^\s*model\s*=\s*"([^"]+)"')


def _codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME", "").strip()
    return Path(env_home) if env_home else (Path.home() / ".codex")


def _find_codex_bin() -> Optional[str]:
    override = os.environ.get("SWARM_MIX_CODEX_BIN", "").strip()
    if override:
        return override if (os.path.isfile(override) and os.access(override, os.X_OK)) else None
    return shutil.which("codex")


def _read_default_model() -> str:
    cfg = _codex_home() / "config.toml"
    if cfg.is_file():
        try:
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s.startswith("["):
                    break
                m = MODEL_RE.match(line)
                if m:
                    return m.group(1).strip()
        except OSError:
            pass
    return "codex-default"


def _pick_lightweight_model() -> str:
    """Resolve an L3 lightweight Codex model (e.g. gpt-6-luna) so scout/monitor never burn flagship quota."""
    cat = _codex_home() / "native-router-models.json"
    if cat.is_file():
        try:
            doc = json.loads(cat.read_text(encoding="utf-8", errors="replace"))
            for item in doc.get("models") or []:
                slug = str(item.get("slug") or "").strip()
                if "/" in slug or item.get("visibility") == "hide":
                    continue
                if any(k in slug.lower() for k in ("luna", "mini", "nano", "flash")):
                    return slug
        except Exception:
            pass
    return "gpt-6-luna"


def probe_codex() -> Dict[str, Any]:
    bin_path = _find_codex_bin()
    if not bin_path:
        return {"available": False, "model": None, "reason": "codex_not_installed"}

    has_api_key = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if not has_api_key:
        try:
            proc = subprocess.run(
                [bin_path, "login", "status"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            out = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
            if proc.returncode != 0 or ("not logged in" in out and "logged in" not in out.replace("not logged in", "")):
                return {"available": False, "model": None, "reason": "codex_not_authenticated"}
        except (OSError, subprocess.SubprocessError):
            return {"available": False, "model": None, "reason": "codex_probe_failed"}

    return {
        "available": True,
        "model": _read_default_model(),
        "binary": bin_path,
        "reason": "ok",
    }


def _is_git_repo(cwd: Path) -> bool:
    cur = cwd.resolve()
    while True:
        if (cur / ".git").exists():
            return True
        if cur.parent == cur:
            return False
        cur = cur.parent


def _safe_int(val: Any) -> int:
    if val is None or isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return 0


def _build_cmd(
    bin_path: str,
    mode: str,
    effort: str,
    sandbox: str,
    cwd: Path,
    prompt: str,
    model_override: Optional[str] = None,
    base: Optional[str] = None,
    commit: Optional[str] = None,
) -> List[str]:
    in_git = _is_git_repo(cwd)
    if mode == "review" and in_git and (commit or base or not prompt):
        cmd = [bin_path, "exec", "review"]
        if commit:
            cmd.extend(["--commit", commit])
        elif base:
            cmd.extend(["--base", base])
        else:
            cmd.append("--uncommitted")
        cmd.extend(["--ephemeral", "--json", "-c", f'model_reasoning_effort="{effort}"'])
        if model_override:
            cmd.extend(["-m", model_override])
        if prompt and (commit or base):
            cmd.append(prompt)
        return cmd

    cmd = [
        bin_path,
        "exec",
        "-C",
        str(cwd),
        "--skip-git-repo-check",
        "--ephemeral",
        "-s",
        "read-only" if mode == "review" else sandbox,
        "--json",
        "-c",
        f'model_reasoning_effort="{effort}"',
    ]
    if model_override:
        cmd.extend(["-m", model_override])
    cmd.append(prompt or "Review the repository changes and report concrete bugs.")
    return cmd


def _run_and_parse(cmd: List[str], cwd: Path, timeout_sec: int) -> Tuple[int, str, Dict[str, int], str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return 124, "", {"turns": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0}, "timeout"
    except OSError as exc:
        return 127, "", {"turns": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0}, str(exc)

    messages: List[str] = []
    errors: List[str] = []
    turns = uncached_in = cached_in = out_tok = 0

    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "item.completed":
            item = ev.get("item")
            if isinstance(item, dict):
                itype = item.get("type")
                if itype == "agent_message" and item.get("text"):
                    messages.append(str(item["text"]))
                elif itype == "error" and item.get("message"):
                    errors.append(str(item["message"]))
        elif etype in ("turn.completed", "turn.failed"):
            if etype == "turn.failed" and isinstance(ev.get("error"), dict):
                errors.append(str(ev["error"].get("message") or "turn_failed"))
            usage = ev.get("usage")
            if isinstance(usage, dict):
                turns += 1
                raw_in = _safe_int(usage.get("input_tokens"))
                c_in = _safe_int(usage.get("cached_input_tokens"))
                cw_in = _safe_int(usage.get("cache_write_input_tokens"))
                uncached_in += max(0, raw_in - c_in) + cw_in
                cached_in += c_in
                out_tok += _safe_int(usage.get("output_tokens"))
        elif etype == "error" and ev.get("message"):
            errors.append(str(ev["message"]))

    text = "\n\n".join(m.strip() for m in messages if m.strip())
    err_summary = "; ".join(errors) or (proc.stderr or "").strip()
    stats = {
        "turns": turns,
        "input_tokens": uncached_in,
        "cache_read_tokens": cached_in,
        "output_tokens": out_tok,
    }
    return proc.returncode, text, stats, err_summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Optional OpenAI Codex CLI bridge for swarm-mix.")
    parser.add_argument("--probe", action="store_true", help="Check whether local Codex CLI is installed and logged in.")
    parser.add_argument("--mode", choices=["review", "exec"], default="review", help="Execution mode (default: review).")
    parser.add_argument(
        "--role",
        choices=["auto", "review", "author", "scout", "monitor"],
        default="auto",
        help="Worker role tier; scout/monitor automatically downgrade to lightweight L3 model.",
    )
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="medium", help="Reasoning effort.")
    parser.add_argument("--sandbox", choices=["read-only", "workspace-write"], default="read-only", help="Sandbox mode for exec.")
    parser.add_argument("--cwd", default=".", help="Target working directory.")
    parser.add_argument("--base", default=None, help="Git base branch for review mode.")
    parser.add_argument("--commit", default=None, help="Git commit SHA for review mode.")
    parser.add_argument("--model", default=None, help="Optional explicit Codex model override.")
    parser.add_argument("--timeout", type=int, default=600, help="Subprocess timeout in seconds.")
    parser.add_argument("prompt", nargs="?", default="", help="Instruction or review focus.")
    args = parser.parse_args(argv)

    status = probe_codex()
    if args.probe:
        print(json.dumps(status, ensure_ascii=False))
        return 0

    if not status.get("available"):
        print(f"{UNAVAILABLE_TAG} {json.dumps(status, ensure_ascii=False)}")
        return 0

    bin_path = str(status["binary"])
    env_role = os.environ.get("SWARM_MIX_ROLE", "").strip().lower()
    eff_role = env_role if args.role == "auto" and env_role else args.role
    resolved_model_override = args.model
    active_effort = args.effort
    if eff_role in ("scout", "monitor") and not resolved_model_override:
        resolved_model_override = _pick_lightweight_model()
        active_effort = "low"

    active_model = resolved_model_override or str(status.get("model") or "codex-default")
    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        cwd = Path.cwd()

    cmd = _build_cmd(
        bin_path=bin_path,
        mode=args.mode,
        effort=active_effort,
        sandbox=args.sandbox,
        cwd=cwd,
        prompt=args.prompt,
        model_override=resolved_model_override,
        base=args.base,
        commit=args.commit,
    )
    rc, text, stats, err_msg = _run_and_parse(cmd, cwd, args.timeout)

    # Automatic quota / rate-limit fallback to lightweight Codex tier
    if (rc != 0 or not text) and not args.model:
        low_err = err_msg.lower()
        if any(k in low_err for k in ("quota", "rate limit", "429", "usage limit", "capacity", "not found")):
            fb_model = "gpt-6-luna"
            fb_cmd = _build_cmd(
                bin_path=bin_path,
                mode=args.mode,
                effort="medium",
                sandbox=args.sandbox,
                cwd=cwd,
                prompt=args.prompt,
                model_override=fb_model,
                base=args.base,
                commit=args.commit,
            )
            rc2, text2, stats2, err_msg2 = _run_and_parse(fb_cmd, cwd, args.timeout)
            if rc2 == 0 and text2:
                rc, text, err_msg = rc2, f"[Fallback: {fb_model}]\n{text2}", err_msg2
                active_model = fb_model
                active_effort = "medium"
                for k in stats:
                    stats[k] += stats2[k]

    if rc != 0 and not text:
        fail_payload = {
            "available": False,
            "model": active_model,
            "reason": "codex_exec_failed",
            "detail": err_msg[:300],
        }
        print(f"{UNAVAILABLE_TAG} {json.dumps(fail_payload, ensure_ascii=False)}")
        if stats["turns"] > 0 or stats["input_tokens"] > 0:
            telemetry = {"model": f"{active_model} ({active_effort})", **stats}
            print(f"{USAGE_TAG} {json.dumps(telemetry, ensure_ascii=False)}")
        return 0

    if text:
        print(text)
    telemetry = {"model": f"{active_model} ({active_effort})", **stats}
    print(f"\n{USAGE_TAG} {json.dumps(telemetry, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
