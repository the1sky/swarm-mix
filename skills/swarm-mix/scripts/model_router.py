#!/usr/bin/env python3
"""
model_router.py — 3-Layer Capability Discovery & Adaptive Router for swarm-mix.

Zero external dependencies (Python 3.9+ stdlib). Works standalone in any single
host (Antigravity, Claude Code, or OpenAI Codex) and automatically unlocks
cross-company routing when peer CLIs/catalogs are detected on the machine:
  - Layer 1 (Hard Catalog Facts): Context window, effort levels, modalities, live quota %.
  - Layer 2 (Benchmark Priors): 5D capability card (`data/model_priors.json`) + unknown-model inheritance.
  - Layer 3 (Empirical Feedback): Passive Swarm board outcome harvesting + explicit `record` hook
    with Bayesian shrinkage residual updates (activated at n >= 3, bounded to +-0.15).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PRIORS_PATH = SCRIPT_DIR.parent / "data" / "model_priors.json"

DIMS = (
    "architecture_refactor",
    "algorithm_state",
    "frontend_multimodal",
    "bug_hunting",
    "throughput_efficiency",
)

EFFORT_SUFFIX_RE = re.compile(r"-(low|medium|high|xhigh|max|ultra)$", re.IGNORECASE)
PAREN_EFFORT_RE = re.compile(r"\s*\((low|medium|high|xhigh|max|ultra)\)\s*$", re.IGNORECASE)
TOML_STR_RE = re.compile(r'^\s*([a-zA-Z0-9_]+)\s*=\s*"([^"]+)"')
TOML_INT_RE = re.compile(r"^\s*([a-zA-Z0-9_]+)\s*=\s*(\d+)")

DEFECT_RE = re.compile(
    r"\b(bug|bugs|defect|blocking|needs\s*fix|fail|failed|crash|vulnerability|race\s+condition)\b"
    r"|阻塞|缺陷|报错|修复|不通过|越界|漏洞",
    re.IGNORECASE,
)
PASS_RE = re.compile(
    r"\b(lgtm|pass|passed|0\s+blocking|no\s+blocking|approved|all\s+clear)\b"
    r"|验收通过|全部通过|零阻塞|无阻塞",
    re.IGNORECASE,
)


def _state_dir() -> Path:
    override = os.environ.get("SWARM_MIX_STATE_DIR", "").strip()
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".cache")
    return base / "swarm-mix"


def _telemetry_path() -> Path:
    return _state_dir() / "telemetry.json"


def _agy_cache_path() -> Path:
    return _state_dir() / "agy_catalog_cache.json"


def _agy_root() -> Path:
    return Path(os.environ.get("ANTIGRAVITY_DATA_DIR", str(Path.home() / ".gemini" / "antigravity")))


def _codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME", "").strip()
    return Path(env_home) if env_home else (Path.home() / ".codex")


def _claude_home() -> Path:
    env_home = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(env_home) if env_home else (Path.home() / ".claude")


def load_priors() -> Dict[str, Any]:
    candidates = [
        PRIORS_PATH,
        Path.home() / ".gemini" / "config" / "plugins" / "swarm-mix" / "skills" / "swarm-mix" / "data" / "model_priors.json",
        _claude_home() / "skills" / "swarm-mix" / "data" / "model_priors.json",
        _codex_home() / "skills" / "swarm-mix" / "data" / "model_priors.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {"bayesian_config": {}, "task_profiles": {}, "models": {}, "vendor_tier_defaults": {}}


def canonicalize_model_id(raw_model: str) -> Tuple[str, str]:
    """Split a full model identifier into (base_model_key, effort_level)."""
    s = str(raw_model or "").strip().rstrip("*")
    effort = "medium"
    pm = PAREN_EFFORT_RE.search(s)
    if pm:
        effort = pm.group(1).lower()
        s = s[: pm.start()].strip()
    sm = EFFORT_SUFFIX_RE.search(s)
    if sm:
        effort = sm.group(1).lower()
        s = s[: sm.start()].strip()
    low = s.lower()
    alias_map = {
        "opus": "claude-opus-5-5",
        "sonnet": "claude-sonnet-5-5",
        "haiku": "claude-haiku-4-5",
        "gemini-pro-orchestrator": "gemini-4-carbon",
        "gemini-pro-orchestrator-argon": "gemini-4-argon",
        "gemini-flash": "gemini-3.8-flash",
    }
    return alias_map.get(low, low), effort


def infer_vendor_and_tier(base_id: str, priority: Optional[int] = None) -> Tuple[str, str]:
    m = base_id.lower()
    if "gemini" in m:
        vendor = "google"
    elif "claude" in m or m in ("opus", "sonnet", "haiku"):
        vendor = "anthropic"
    elif "gpt-oss" in m:
        vendor = "openai-oss"
    elif m.startswith(("gpt-", "o1", "o3", "o4", "codex")):
        vendor = "openai"
    else:
        vendor = "other"

    if any(k in m for k in ("hydrogen", "flash", "mini", "nano", "haiku", "luna", "lite", "oss")):
        tier = "l3"
    elif any(k in m for k in ("argon", "sonnet", "sol", "terra")):
        tier = "l2"
    elif any(k in m for k in ("carbon", "opus", "astra", "pro", "o3")):
        tier = "l1"
    elif priority is not None:
        tier = "l1" if priority <= 1 else ("l3" if priority == 3 else "l2")
    else:
        tier = "l2"
    return vendor, tier


# ---------------------------------------------------------------------------
# Layer 1: Multi-Host Zero-Token Catalog Discovery (Isolated & Safe)
# ---------------------------------------------------------------------------

def _find_bin(env_var: str, default_name: str) -> Optional[str]:
    override = os.environ.get(env_var, "").strip()
    if override:
        if override.lower() in ("none", "off", "false", "0"):
            return None
        return override if (os.path.isfile(override) and os.access(override, os.X_OK)) else None
    return shutil.which(default_name)


def _parse_quota_pct(raw: Any, note: str = "") -> Optional[float]:
    if "credit" in str(note or "").lower():
        return 1.0
    if raw is None:
        return None
    s = str(raw).strip().rstrip("%")
    try:
        val = float(s)
        return max(0.0, min(1.0, val / 100.0 if val > 1.0 or "%" in str(raw) else val))
    except ValueError:
        return None


def _save_agy_catalog_cache(source: str, entries: List[Dict[str, Any]]) -> None:
    try:
        cpath = _agy_cache_path()
        cpath.parent.mkdir(parents=True, exist_ok=True)
        clean_models = [{k: v for k, v in e.items() if k != "channel"} for e in entries]
        cpath.write_text(
            json.dumps({"updated_at": time.time(), "source": source, "models": clean_models}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _load_agy_catalog_cache(is_native: bool) -> Optional[Dict[str, Any]]:
    cpath = _agy_cache_path()
    if not cpath.is_file():
        return None
    try:
        doc = json.loads(cpath.read_text(encoding="utf-8"))
        age = time.time() - float(doc.get("updated_at") or 0)
        if age > 21600:  # 6h catalog TTL
            return None
        raw_models = doc.get("models") or []
        if not raw_models:
            return None
        keep_quota = age < 900  # 15m live quota TTL
        entries = []
        for item in raw_models:
            e = dict(item)
            if not keep_quota:
                e["quota_pct"] = None
            e["channel"] = "native" if is_native else "agy-cli"
            entries.append(e)
        return {"available": True, "source": f"{doc.get('source', 'agy')}_cached", "models": entries}
    except Exception:
        return None


def discover_agy_pool(agy_models_json: Optional[str], is_native: bool, refresh_agy: bool = False) -> Dict[str, Any]:
    """Discover Antigravity model pool from swarm(models) JSON, local cache, or `agy models` CLI."""
    entries: List[Dict[str, Any]] = []
    if agy_models_json:
        try:
            data = json.loads(agy_models_json)
            models = data.get("models") or []
            dnames = data.get("display_names") or {}
            quotas = data.get("quotas") or {}
            for mid in models:
                qinfo = quotas.get(mid) if isinstance(quotas, dict) else {}
                if not isinstance(qinfo, dict):
                    qinfo = {}
                qpct = _parse_quota_pct(qinfo.get("quota_remaining"), str(qinfo.get("quota_note") or ""))
                base_id, eff = canonicalize_model_id(mid)
                entries.append({
                    "id": str(mid),
                    "display_name": str(dnames.get(mid) or mid),
                    "base_id": base_id,
                    "effort": eff,
                    "quota_pct": qpct,
                    "channel": "native" if is_native else "agy-cli",
                })
            if entries:
                _save_agy_catalog_cache("swarm_models_json", entries)
                return {"available": True, "source": "swarm_models_json", "models": entries}
        except Exception:
            pass

    agy_bin = _find_bin("SWARM_MIX_AGY_BIN", "agy")
    if not is_native and not agy_bin:
        return {"available": False, "source": "agy_not_installed", "models": []}

    if refresh_agy and agy_bin:
        try:
            proc = subprocess.run([agy_bin, "models"], capture_output=True, text=True, timeout=6.0)
            if proc.returncode == 0 and proc.stdout.strip():
                for raw in proc.stdout.splitlines():
                    clean = raw.rsplit("\r", 1)[-1].strip()
                    if not clean or "fetching" in clean.lower():
                        continue
                    parts = re.split(r"\s{2,}|\t", clean, maxsplit=1)
                    mid = parts[0].strip()
                    if not mid or " " in mid:
                        continue
                    dname = parts[1].strip() if len(parts) > 1 else mid
                    base_id, eff = canonicalize_model_id(mid)
                    entries.append({
                        "id": mid,
                        "display_name": dname,
                        "base_id": base_id,
                        "effort": eff,
                        "quota_pct": None,
                        "channel": "native" if is_native else "agy-cli",
                    })
                if entries:
                    _save_agy_catalog_cache("agy_cli", entries)
                    return {"available": True, "source": "agy_cli", "models": entries}
        except (OSError, subprocess.SubprocessError):
            pass

    cached = _load_agy_catalog_cache(is_native=is_native)
    if cached:
        return cached

    if is_native or agy_bin:
        for mid in (
            "gemini-4-carbon-high",
            "gemini-4-carbon-medium",
            "gemini-4-argon-high",
            "gemini-4-argon-medium",
            "gemini-4-hydrogen-medium",
            "claude-opus-5-5-medium",
            "claude-opus-5-5-high",
            "claude-sonnet-5-5-high",
            "claude-sonnet-5-5-medium",
        ):
            base_id, eff = canonicalize_model_id(mid)
            entries.append({
                "id": mid,
                "display_name": mid,
                "base_id": base_id,
                "effort": eff,
                "quota_pct": None,
                "channel": "native" if is_native else "agy-cli",
            })
        return {
            "available": True,
            "source": "agy_installed" if agy_bin else "agy_builtin_fallback",
            "models": entries,
        }

    return {"available": False, "source": "unavailable", "models": []}


def discover_codex_pool(is_native: bool) -> Dict[str, Any]:
    """Discover OpenAI Codex models from ~/.codex/config.toml & model_catalog_json."""
    codex_bin = _find_bin("SWARM_MIX_CODEX_BIN", "codex")
    if not is_native and not codex_bin:
        return {"available": False, "source": "codex_not_installed", "models": []}

    if not is_native and codex_bin and not os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            proc = subprocess.run([codex_bin, "login", "status"], capture_output=True, text=True, timeout=2.5)
            out = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
            if proc.returncode != 0 or ("not logged in" in out and "logged in" not in out.replace("not logged in", "")):
                return {"available": False, "source": "codex_not_authenticated", "models": []}
        except (OSError, subprocess.SubprocessError):
            return {"available": False, "source": "codex_probe_failed", "models": []}

    chome = _codex_home()
    cfg_path = chome / "config.toml"
    default_model = "gpt-6-astra"
    cfg_ctx: Optional[int] = None
    catalog_path = chome / "native-router-models.json"

    if cfg_path.is_file():
        try:
            for line in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("["):
                    break
                sm = TOML_STR_RE.match(line)
                if sm:
                    k, v = sm.group(1), sm.group(2).strip()
                    if k == "model":
                        default_model = v
                    elif k == "model_catalog_json":
                        catalog_path = Path(v)
                im = TOML_INT_RE.match(line)
                if im and im.group(1) == "model_context_window":
                    cfg_ctx = int(im.group(2))
        except OSError:
            pass

    entries: List[Dict[str, Any]] = []
    seen_slugs = set()

    if catalog_path.is_file():
        try:
            cat = json.loads(catalog_path.read_text(encoding="utf-8", errors="replace"))
            raw_list = cat if isinstance(cat, list) else (cat.get("models") if isinstance(cat, dict) else [])
            if isinstance(raw_list, list):
                for item in raw_list:
                    if not isinstance(item, dict):
                        continue
                    if item.get("visibility") == "hide":
                        continue
                    slug = str(item.get("slug") or item.get("id") or "").strip()
                    if not slug or slug in seen_slugs:
                        continue
                    seen_slugs.add(slug)
                    efforts = [
                        str(e.get("effort"))
                        for e in (item.get("supported_reasoning_levels") or [])
                        if isinstance(e, dict) and e.get("effort")
                    ] or ["low", "medium", "high"]
                    ctx_win = item.get("context_window")
                    if slug == default_model and cfg_ctx:
                        ctx_win = max(int(ctx_win or 0), cfg_ctx)
                    prio = item.get("priority") if isinstance(item.get("priority"), int) else None
                    base_id, _ = canonicalize_model_id(slug)
                    # Expose medium and high variants for top-tier/balanced models, medium for lightweight
                    _, inferred_tier = infer_vendor_and_tier(base_id, prio)
                    target_effs = ["medium", "high"] if inferred_tier in ("l1", "l2") else ["medium"]
                    for eff in target_effs:
                        if eff not in efforts:
                            eff = efforts[-1]
                        entries.append({
                            "id": f"{slug} ({eff})",
                            "raw_slug": slug,
                            "display_name": f"{item.get('display_name') or slug} ({eff})",
                            "base_id": base_id,
                            "effort": eff,
                            "priority": prio,
                            "context_window": int(ctx_win) if isinstance(ctx_win, int) else None,
                            "modalities": item.get("input_modalities") if isinstance(item.get("input_modalities"), list) else None,
                            "quota_pct": None,
                            "channel": "native" if is_native else "codex-bridge",
                        })
        except Exception:
            pass

    if not entries:
        # Standalone Codex fallback when no catalog file exists on disk
        base_id, _ = canonicalize_model_id(default_model)
        for eff in ("medium", "high"):
            entries.append({
                "id": f"{default_model} ({eff})",
                "raw_slug": default_model,
                "display_name": f"{default_model} ({eff})",
                "base_id": base_id,
                "effort": eff,
                "priority": 1,
                "context_window": cfg_ctx or 272000,
                "modalities": ["text", "image"],
                "quota_pct": None,
                "channel": "native" if is_native else "codex-bridge",
            })

    return {"available": True, "source": "codex_catalog", "default_model": default_model, "models": entries}


def discover_claude_pool(is_native: bool) -> Dict[str, Any]:
    """Discover Claude Code native subagent models or standalone `claude` CLI availability."""
    if not is_native:
        claude_bin = _find_bin("SWARM_MIX_CLAUDE_BIN", "claude")
        if not claude_bin:
            return {"available": False, "source": "claude_not_installed", "models": []}
        chome = _claude_home()
        has_auth = (
            bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
            or (chome / ".credentials.json").is_file()
            or Path.home().joinpath(".claude.json").is_file()
            or chome.is_dir()
        )
        if not has_auth:
            return {"available": False, "source": "claude_not_authenticated", "models": []}

    specs = [
        ("opus", "claude-opus-5-5", "high", "Claude Opus (High)"),
        ("opus", "claude-opus-5-5", "medium", "Claude Opus (Medium)"),
        ("sonnet", "claude-sonnet-5-5", "high", "Claude Sonnet (High)"),
        ("sonnet", "claude-sonnet-5-5", "medium", "Claude Sonnet (Medium)"),
        ("haiku", "claude-haiku-4-5", "medium", "Claude Haiku (Medium)"),
    ]
    entries = []
    for alias, base_id, eff, dname in specs:
        entries.append({
            "id": f"{alias} ({eff})",
            "raw_slug": alias,
            "display_name": dname,
            "base_id": base_id,
            "effort": eff,
            "quota_pct": None,
            "channel": "native" if is_native else "claude-cli",
        })
    return {"available": True, "source": "claude_code", "models": entries}


def detect_current_host(requested_host: str) -> str:
    if requested_host in ("agy", "claude", "codex"):
        return requested_host
    if os.environ.get("ANTIGRAVITY_LS_ADDRESS") or os.environ.get("ANTIGRAVITY_AGENT"):
        return "agy"
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    if _find_bin("SWARM_MIX_AGY_BIN", "agy"):
        return "agy"
    if _find_bin("SWARM_MIX_CLAUDE_BIN", "claude"):
        return "claude"
    return "codex"


# ---------------------------------------------------------------------------
# Layer 3: Local Empirical Outcome Store & Passive Swarm Board Harvester
# ---------------------------------------------------------------------------

def load_telemetry() -> Dict[str, Any]:
    path = _telemetry_path()
    if path.is_file():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                obj.setdefault("models", {})
                obj.setdefault("ingested_sessions", [])
                return obj
        except Exception:
            pass
    return {"schema_version": "1.0", "last_sync_ts": 0.0, "models": {}, "ingested_sessions": []}


def save_telemetry(data: Dict[str, Any]) -> None:
    path = _telemetry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _ensure_model_stat(store: Dict[str, Any], base_id: str) -> Dict[str, Any]:
    models = store.setdefault("models", {})
    return models.setdefault(
        base_id,
        {
            "author_tasks": 0,
            "author_first_pass": 0,
            "author_reworked": 0,
            "verifier_reviews": 0,
            "verifier_bug_catches": 0,
            "total_tokens": 0,
            "updated_at": "",
        },
    )


def record_explicit_outcome(
    author_model: Optional[str] = None,
    first_pass: Optional[bool] = None,
    tokens: int = 0,
    verifier_model: Optional[str] = None,
    caught_bug: Optional[bool] = None,
) -> Dict[str, Any]:
    store = load_telemetry()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if author_model and first_pass is not None:
        base_a, _ = canonicalize_model_id(author_model)
        st = _ensure_model_stat(store, base_a)
        st["author_tasks"] = int(st.get("author_tasks", 0)) + 1
        if first_pass:
            st["author_first_pass"] = int(st.get("author_first_pass", 0)) + 1
        else:
            st["author_reworked"] = int(st.get("author_reworked", 0)) + 1
        if tokens > 0:
            st["total_tokens"] = int(st.get("total_tokens", 0)) + int(tokens)
        st["updated_at"] = now_iso

    if verifier_model and caught_bug is not None:
        base_v, _ = canonicalize_model_stat_id(verifier_model)
        stv = _ensure_model_stat(store, base_v)
        stv["verifier_reviews"] = int(stv.get("verifier_reviews", 0)) + 1
        if caught_bug:
            stv["verifier_bug_catches"] = int(stv.get("verifier_bug_catches", 0)) + 1
        stv["updated_at"] = now_iso

    save_telemetry(store)
    return store


def canonicalize_model_stat_id(raw: str) -> Tuple[str, str]:
    return canonicalize_model_id(raw)


def sync_agy_swarm_outcomes(force: bool = False) -> Dict[str, Any]:
    """
    Passively harvest idempotent Author/Verifier outcomes from completed Antigravity
    Swarm boards (`members.json` + `board.jsonl`) with zero LLM calls.
    """
    store = load_telemetry()
    now = time.time()
    if not force and now - float(store.get("last_sync_ts") or 0.0) < 45.0:
        return store

    brain_dir = _agy_root() / "brain"
    if not brain_dir.is_dir():
        store["last_sync_ts"] = now
        return store

    ingested = set(store.get("ingested_sessions") or [])
    changed = False
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    try:
        entries = list(brain_dir.iterdir())
    except OSError:
        entries = []

    for conv_dir in entries:
        cid = conv_dir.name
        if cid in ingested or not conv_dir.is_dir():
            continue
        swarm_dir = conv_dir / ".system_generated" / "swarm"
        members_file = swarm_dir / "members.json"
        board_file = swarm_dir / "board.jsonl"
        if not (members_file.is_file() and board_file.is_file()):
            continue

        try:
            roster = json.loads(members_file.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        members = roster.get("members") if isinstance(roster, dict) else None
        if not isinstance(members, dict) or not members:
            continue

        workers = {k: v for k, v in members.items() if isinstance(v, dict) and v.get("role") != "coordinator" and k != "coordinator"}
        if not workers or not all(bool(v.get("killed")) for v in workers.values()):
            # Only ingest finished Swarms so multi-round reviews are complete
            continue

        posts: List[Dict[str, Any]] = []
        try:
            for line in board_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                m = json.loads(line)
                if isinstance(m, dict) and m.get("agent") and m.get("text"):
                    posts.append(m)
        except Exception:
            continue

        verifiers = [
            name for name in workers
            if "verifier" in name.lower() or "review" in name.lower()
        ]
        authors = [
            name for name in workers
            if name not in verifiers
            and not any(tag in name.lower() for tag in ("research", "scout", "monitor", "narrator"))
        ]

        if verifiers and posts:
            v_posts = [p for p in posts if p.get("agent") in verifiers]
            if v_posts:
                caught_defect = any(DEFECT_RE.search(str(p.get("text") or "")) and not PASS_RE.search(str(p.get("text") or "")) for p in v_posts)
                if len(v_posts) >= 2 and any(DEFECT_RE.search(str(p.get("text") or "")) for p in v_posts[:-1]):
                    caught_defect = True

                for vname in verifiers:
                    vmodel = str(workers[vname].get("applied_model") or workers[vname].get("model") or "")
                    # Check if verifier bridged to Codex in its transcript
                    v_cid = str(workers[vname].get("conversation_id") or "")
                    if v_cid:
                        v_tp = brain_dir / v_cid / ".system_generated" / "logs" / "transcript.jsonl"
                        bridged_m = _detect_bridged_codex_model(v_tp)
                        if bridged_m:
                            vmodel = bridged_m
                    if vmodel:
                        base_v, _ = canonicalize_model_id(vmodel)
                        stv = _ensure_model_stat(store, base_v)
                        stv["verifier_reviews"] += 1
                        if caught_defect:
                            stv["verifier_bug_catches"] += 1
                        stv["updated_at"] = now_iso
                        changed = True

                for aname in authors:
                    if not any(p.get("agent") == aname for p in posts):
                        continue
                    amodel = str(workers[aname].get("applied_model") or workers[aname].get("model") or "")
                    a_cid = str(workers[aname].get("conversation_id") or "")
                    if a_cid:
                        a_tp = brain_dir / a_cid / ".system_generated" / "logs" / "transcript.jsonl"
                        bridged_a = _detect_bridged_codex_model(a_tp)
                        if bridged_a:
                            amodel = bridged_a
                    if amodel:
                        base_a, _ = canonicalize_model_id(amodel)
                        sta = _ensure_model_stat(store, base_a)
                        sta["author_tasks"] += 1
                        if caught_defect:
                            sta["author_reworked"] += 1
                        else:
                            sta["author_first_pass"] += 1
                        sta["updated_at"] = now_iso
                        changed = True

        ingested.add(cid)
        changed = True

    store["ingested_sessions"] = sorted(ingested)[-200:]
    store["last_sync_ts"] = now
    if changed:
        save_telemetry(store)
    return store


def _detect_bridged_codex_model(tp_path: Path) -> Optional[str]:
    tasks_dir = tp_path.parent.parent / "tasks"
    if tasks_dir.is_dir():
        try:
            for tf in sorted(tasks_dir.glob("task-*.log")):
                txt = tf.read_text(encoding="utf-8", errors="replace")
                if "[CODEX_BRIDGE_USAGE]" not in txt:
                    continue
                for raw in txt.splitlines():
                    s = raw.strip()
                    if s.startswith("[CODEX_BRIDGE_USAGE] {") and s.endswith("}"):
                        payload = json.loads(s[len("[CODEX_BRIDGE_USAGE] "):])
                        if isinstance(payload, dict) and isinstance(payload.get("model"), str):
                            return payload["model"]
        except Exception:
            pass
    if not tp_path.is_file():
        return None
    try:
        with tp_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "[CODEX_BRIDGE_USAGE]" not in line:
                    continue
                d = json.loads(line)
                if not isinstance(d, dict) or d.get("type") not in ("GENERIC", "SYSTEM_MESSAGE"):
                    continue
                content = str(d.get("content") or "")
                if d.get("type") == "GENERIC" and '"messages":[' in content:
                    continue
                for raw in content.splitlines():
                    s = raw.strip()
                    if s.startswith("[CODEX_BRIDGE_USAGE] {") and s.endswith("}"):
                        payload = json.loads(s[len("[CODEX_BRIDGE_USAGE] "):])
                        if isinstance(payload, dict) and isinstance(payload.get("model"), str):
                            return payload["model"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Layer 1 + 2 + 3 Composite Capability Scoring & Task Routing
# ---------------------------------------------------------------------------

def score_candidate(
    cand: Dict[str, Any],
    priors_doc: Dict[str, Any],
    telemetry: Dict[str, Any],
) -> Dict[str, Any]:
    base_id = cand["base_id"]
    effort = cand.get("effort", "medium")
    prio = cand.get("priority")

    model_cards = priors_doc.get("models") or {}
    card = model_cards.get(base_id)
    if card:
        vendor = str(card.get("vendor") or "other")
        tier = str(card.get("tier") or "l2")
        ctx_window = int(cand.get("context_window") or card.get("context_window") or 200000)
        modalities = list(cand.get("modalities") or card.get("modalities") or ["text"])
        raw_scores = dict(card.get("scores") or {})
        prior_source = "card"
    else:
        vendor, tier = infer_vendor_and_tier(base_id, prio)
        vt_defaults = (priors_doc.get("vendor_tier_defaults") or {}).get(
            vendor if vendor in ("google", "anthropic", "openai") else "other", {}
        )
        raw_scores = dict(vt_defaults.get(tier) or {d: 0.78 for d in DIMS})
        ctx_window = int(cand.get("context_window") or (1000000 if vendor == "google" else 200000))
        modalities = list(cand.get("modalities") or ["text"])
        prior_source = f"inherited:{vendor}:{tier}"

    # Effort level adjustment
    eff_reason_delta = {"low": -0.05, "medium": 0.0, "high": 0.02, "xhigh": 0.035, "max": 0.04, "ultra": 0.04}.get(effort, 0.0)
    eff_speed_delta = {"low": 0.06, "medium": 0.0, "high": -0.04, "xhigh": -0.08, "max": -0.10, "ultra": -0.10}.get(effort, 0.0)

    scores: Dict[str, float] = {}
    for d in DIMS:
        base_val = float(raw_scores.get(d, 0.75))
        delta = eff_speed_delta if d == "throughput_efficiency" else eff_reason_delta
        scores[d] = max(0.0, min(1.0, base_val + delta))

    # Layer 3 Bayesian Shrinkage Residual Calibration
    bcfg = priors_doc.get("bayesian_config") or {}
    kappa = float(bcfg.get("prior_pseudocount", 8.0))
    min_n = int(bcfg.get("min_samples_to_adapt", 3))
    max_drift = float(bcfg.get("max_empirical_drift", 0.15))

    mstat = (telemetry.get("models") or {}).get(base_id) or {}
    a_tasks = int(mstat.get("author_tasks", 0))
    a_pass = int(mstat.get("author_first_pass", 0))
    v_revs = int(mstat.get("verifier_reviews", 0))
    v_catch = int(mstat.get("verifier_bug_catches", 0))

    empirical_delta_author = 0.0
    empirical_delta_verify = 0.0

    if a_tasks >= min_n:
        pass_rate = a_pass / max(1, a_tasks)
        raw_shift = (a_tasks / (kappa + a_tasks)) * (pass_rate - 0.75) * 0.45
        empirical_delta_author = max(-max_drift, min(max_drift, raw_shift))
        for d in ("architecture_refactor", "algorithm_state", "frontend_multimodal"):
            scores[d] = round(max(0.0, min(1.0, scores[d] + empirical_delta_author)), 4)

    if v_revs >= min_n:
        catch_rate = v_catch / max(1, v_revs)
        raw_vshift = (v_revs / (kappa + v_revs)) * (catch_rate - 0.35) * 0.40
        empirical_delta_verify = max(-max_drift, min(max_drift, raw_vshift))
        scores["bug_hunting"] = round(max(0.0, min(1.0, scores["bug_hunting"] + empirical_delta_verify)), 4)

    for d in DIMS:
        scores[d] = round(scores[d], 3)

    qpct = cand.get("quota_pct")
    quota_exhausted = qpct is not None and qpct <= 0.0

    return {
        "id": cand["id"],
        "display_name": cand.get("display_name") or cand["id"],
        "base_id": base_id,
        "vendor": vendor,
        "tier": tier,
        "effort": effort,
        "channel": cand["channel"],
        "context_window": ctx_window,
        "modalities": modalities,
        "quota_pct": qpct,
        "available": not quota_exhausted,
        "prior_source": prior_source,
        "scores": scores,
        "empirical": {
            "author_tasks": a_tasks,
            "author_first_pass": a_pass,
            "verifier_reviews": v_revs,
            "verifier_bug_catches": v_catch,
            "author_shift": round(empirical_delta_author, 4),
            "verify_shift": round(empirical_delta_verify, 4),
        },
    }


def compute_routing_plan(
    host: str,
    agy_models_json: Optional[str] = None,
    author_family: Optional[str] = None,
    min_context: int = 0,
    refresh_agy: bool = False,
) -> Dict[str, Any]:
    resolved_host = detect_current_host(host)
    priors_doc = load_priors()
    telemetry = sync_agy_swarm_outcomes(force=False)

    pools: Dict[str, Any] = {}
    raw_candidates: List[Dict[str, Any]] = []

    if resolved_host == "agy":
        p_agy = discover_agy_pool(agy_models_json, is_native=True, refresh_agy=refresh_agy)
        p_codex = discover_codex_pool(is_native=False)
        p_claude = discover_claude_pool(is_native=False)
        pools = {"agy": p_agy["source"], "codex": p_codex["source"], "claude_cli": p_claude["source"]}
        raw_candidates.extend(p_agy["models"])
        if p_codex["available"]:
            raw_candidates.extend(p_codex["models"])
        # Unlock external claude-cli pool whenever Antigravity's native Anthropic bucket is low (<15%) or exhausted
        has_healthy_native_claude = any(
            "claude" in m["base_id"] and (m.get("quota_pct") is None or m.get("quota_pct", 1.0) >= 0.15)
            for m in p_agy["models"]
        )
        if p_claude["available"] and not has_healthy_native_claude:
            raw_candidates.extend(p_claude["models"])
    elif resolved_host == "claude":
        p_claude = discover_claude_pool(is_native=True)
        p_codex = discover_codex_pool(is_native=False)
        p_agy = discover_agy_pool(None, is_native=False, refresh_agy=refresh_agy)
        pools = {"claude": p_claude["source"], "codex": p_codex["source"], "agy_cli": p_agy["source"]}
        raw_candidates.extend(p_claude["models"])
        if p_codex["available"]:
            raw_candidates.extend(p_codex["models"])
        if p_agy["available"]:
            raw_candidates.extend([m for m in p_agy["models"] if "gemini-4" in m["base_id"]])
    else:  # codex
        p_codex = discover_codex_pool(is_native=True)
        p_claude = discover_claude_pool(is_native=False)
        p_agy = discover_agy_pool(None, is_native=False, refresh_agy=refresh_agy)
        pools = {"codex": p_codex["source"], "claude_cli": p_claude["source"], "agy_cli": p_agy["source"]}
        raw_candidates.extend(p_codex["models"])
        if p_claude["available"]:
            raw_candidates.extend(p_claude["models"])
        if p_agy["available"]:
            raw_candidates.extend([m for m in p_agy["models"] if "gemini-4" in m["base_id"]])

    scored_all = [score_candidate(c, priors_doc, telemetry) for c in raw_candidates]
    eligible = [
        m for m in scored_all
        if m["available"] and m["context_window"] >= int(min_context or 0)
    ]

    # Infer default primary author vendor when not explicitly passed
    default_author_vendor = author_family
    if not default_author_vendor:
        default_author_vendor = {"agy": "google", "claude": "anthropic", "codex": "openai"}.get(resolved_host, "google")
    norm_author_vendor = {
        "gemini": "google",
        "google": "google",
        "claude": "anthropic",
        "anthropic": "anthropic",
        "gpt": "openai",
        "codex": "openai",
        "openai": "openai",
    }.get(str(default_author_vendor).lower(), str(default_author_vendor).lower())

    recommendations: Dict[str, Any] = {}
    task_profiles = priors_doc.get("task_profiles") or {}

    for task_key, tprof in task_profiles.items():
        weights = tprof.get("weights") or {}
        pref_eff = tprof.get("preferred_effort", "medium")
        target_tier = tprof.get("tier", "l2")
        need_hetero = bool(tprof.get("require_heterogeneous_family"))

        ranked: List[Tuple[float, Dict[str, Any], str]] = []
        for m in eligible:
            # Tier suitability guards
            if task_key == "scout_daemon" and m["tier"] == "l1":
                tier_pen = -0.35
            elif task_key == "backend_refactor" and m["tier"] == "l1":
                tier_pen = -0.05
            elif target_tier in ("l1", "verify") and m["tier"] == "l3":
                tier_pen = -0.25
            else:
                tier_pen = 0.0

            # Unverified custom LAN/local endpoints (e.g. `host/model`) should not outrank primary flagships automatically
            is_custom_ep = "/" in str(m["base_id"])
            local_ep_pen = -0.18 if is_custom_ep else 0.0

            cap_score = sum(float(weights.get(d, 0.0)) * float(m["scores"].get(d, 0.0)) for d in DIMS)
            if m["channel"] == "native":
                native_bonus = 0.05 if task_key in ("architecture", "frontend_ui", "backend_refactor") else 0.02
            elif m["channel"] == "claude-cli":
                native_bonus = -0.15 if task_key == "scout_daemon" else -0.05
            else:
                native_bonus = -0.15 if task_key == "scout_daemon" else 0.0

            effort_bonus = 0.03 if m["effort"] == pref_eff else 0.0

            qpct = m.get("quota_pct")
            quota_pen = -0.18 if (qpct is not None and qpct < 0.15) else 0.0

            hetero_pen = 0.0
            note_parts: List[str] = []
            if need_hetero:
                if (
                    is_custom_ep
                    or m["vendor"] == norm_author_vendor
                    or (norm_author_vendor == "openai" and m["vendor"] == "openai-oss")
                ):
                    hetero_pen = -0.35
                    note_parts.append("unverified-local-ep" if is_custom_ep else "same-family-fallback")
                else:
                    note_parts.append(f"cross-family vs {norm_author_vendor}")

            if qpct is not None and qpct < 0.15:
                note_parts.append(f"low-quota({int(qpct * 100)}%)")
            if m["channel"] != "native":
                note_parts.append(f"via {m['channel']}")

            raw_total = cap_score + native_bonus + effort_bonus + tier_pen + local_ep_pen + quota_pen + hetero_pen
            ranked.append((raw_total, m, ", ".join(note_parts) if note_parts else "native optimal"))

        ranked.sort(key=lambda x: x[0], reverse=True)
        if ranked:
            best_score, best_m, reason = ranked[0]
            alt = next(
                (
                    item[1]["id"]
                    for item in ranked[1:]
                    if item[1]["base_id"] != best_m["base_id"] or item[1]["channel"] != best_m["channel"]
                ),
                None,
            )
            recommendations[task_key] = {
                "task": task_key,
                "label": tprof.get("label", task_key),
                "recommended_model": best_m["id"],
                "vendor": best_m["vendor"],
                "channel": best_m["channel"],
                "match_score": round(min(1.0, max(0.0, best_score)), 3),
                "fallback_model": alt,
                "rationale": reason,
            }

    # Deduplicate model leaderboard by `id` for UI & CLI inspection
    seen_ids = set()
    unique_models = []
    for m in sorted(scored_all, key=lambda x: (x["available"], x["scores"]["architecture_refactor"] + x["scores"]["bug_hunting"]), reverse=True):
        if m["id"] in seen_ids:
            continue
        seen_ids.add(m["id"])
        unique_models.append(m)

    return {
        "host": resolved_host,
        "author_family_assumed": norm_author_vendor,
        "pools": pools,
        "recommendations": recommendations,
        "models": unique_models,
    }


def render_markdown(plan: Dict[str, Any]) -> str:
    lines = [
        f"### Swarm Mix Capability Routing (`host={plan['host']}`, pools: "
        + ", ".join(f"`{k}={v}`" for k, v in plan["pools"].items())
        + ")",
        "",
        "| Task Profile | Recommended Model | Channel | Score | Fallback | Rationale |",
        "| :--- | :--- | :--- | ---: | :--- | :--- |",
    ]
    for rec in plan["recommendations"].values():
        lines.append(
            f"| `{rec['task']}` | `{rec['recommended_model']}` | `{rec['channel']}` | "
            f"{rec['match_score']:.3f} | `{rec['fallback_model'] or '-'}` | {rec['rationale']} |"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "record":
        rp = argparse.ArgumentParser(prog="model_router.py record")
        rp.add_argument("--author-model", default=None)
        grp_a = rp.add_mutually_exclusive_group()
        grp_a.add_argument("--first-pass", action="store_true")
        grp_a.add_argument("--reworked", action="store_true")
        rp.add_argument("--tokens", type=int, default=0)
        rp.add_argument("--verifier-model", default=None)
        grp_v = rp.add_mutually_exclusive_group()
        grp_v.add_argument("--caught-bug", action="store_true")
        grp_v.add_argument("--clean-pass", action="store_true")
        ns = rp.parse_args(raw_args[1:])
        fp = True if ns.first_pass else (False if ns.reworked else None)
        cb = True if ns.caught_bug else (False if ns.clean_pass else None)
        out = record_explicit_outcome(
            author_model=ns.author_model,
            first_pass=fp,
            tokens=ns.tokens,
            verifier_model=ns.verifier_model,
            caught_bug=cb,
        )
        print(json.dumps({"recorded": True, "models": out.get("models", {})}, ensure_ascii=False))
        return 0

    if raw_args and raw_args[0] == "sync":
        out = sync_agy_swarm_outcomes(force=True)
        print(json.dumps({"synced": True, "ingested_count": len(out.get("ingested_sessions", [])), "models": out.get("models", {})}, ensure_ascii=False))
        return 0

    if raw_args and raw_args[0] in ("recommend", "route"):
        raw_args = raw_args[1:]

    p = argparse.ArgumentParser(description="3-layer capability discovery and task router for swarm-mix.")
    p.add_argument("--host", choices=["auto", "agy", "claude", "codex"], default="auto")
    p.add_argument("--agy-models-json", default=None, help="Raw JSON returned by swarm(action='models').")
    p.add_argument("--author-family", default=None, help="Author vendor family (google|anthropic|openai) for cross-verify routing.")
    p.add_argument("--min-context", type=int, default=0, help="Minimum context window filter.")
    p.add_argument("--refresh-agy", action="store_true", help="Refresh Antigravity model catalog via `agy models` CLI.")
    p.add_argument("--markdown", "-m", action="store_true", help="Format output as a Markdown routing table.")
    args = p.parse_args(raw_args)

    plan = compute_routing_plan(
        host=args.host,
        agy_models_json=args.agy_models_json,
        author_family=args.author_family,
        min_context=args.min_context,
        refresh_agy=args.refresh_agy,
    )
    if args.markdown:
        print(render_markdown(plan))
    else:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
