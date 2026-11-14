#!/usr/bin/env python3
"""
swarm-tokens — Multi-host multi-agent & Swarm token usage inspector for Swarm Mix.

Supports:
  - Google Antigravity (`agy`): Native Swarm (`coordinator` + `worker` roster) & Subagents
  - Claude Code (`claude`): Main session + `~/.claude/projects/*/<sid>/subagents/agent-*.jsonl`
  - OpenAI Codex (`codex`): Root session + `spawn_agent` child rollout threads

Zero external dependencies (Python 3.8+ stdlib only).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclasses.dataclass
class AgentUsage:
    name: str
    role: str
    model: str
    state: str
    turns: int = 0
    input_tokens: int = 0       # Uncached input (+ cache creation where applicable)
    cache_read_tokens: int = 0  # Cached prefix tokens read
    output_tokens: int = 0      # Output + reasoning tokens
    agent_id: str = ""

    @property
    def total_context_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens

    @property
    def cache_hit_pct(self) -> float:
        denom = self.total_context_tokens
        return (self.cache_read_tokens * 100.0 / denom) if denom > 0 else 0.0


@dataclasses.dataclass
class SessionReport:
    host: str
    session_id: str
    title: str
    workspace: str
    updated_at: float
    agents: List[AgentUsage]
    is_swarm: bool = True

    @property
    def total_turns(self) -> int:
        return sum(a.turns for a in self.agents)

    @property
    def total_input(self) -> int:
        return sum(a.input_tokens for a in self.agents)

    @property
    def total_cache(self) -> int:
        return sum(a.cache_read_tokens for a in self.agents)

    @property
    def total_output(self) -> int:
        return sum(a.output_tokens for a in self.agents)

    @property
    def cache_hit_pct(self) -> float:
        denom = self.total_input + self.total_cache
        return (self.total_cache * 100.0 / denom) if denom > 0 else 0.0


def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_ts(ts: float) -> str:
    if ts <= 0:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def disp_width(s: str) -> int:
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad_col(s: str, width: int, align_right: bool = False) -> str:
    cur = disp_width(s)
    if cur > width:
        out: List[str] = []
        acc = 0
        for ch in s:
            cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            if acc + cw > width - 1:
                break
            out.append(ch)
            acc += cw
        s = "".join(out) + "…"
        cur = disp_width(s)
    pad = " " * max(0, width - cur)
    return (pad + s) if align_right else (s + pad)


def _safe_int(val: Any) -> int:
    if val is None or isinstance(val, bool):
        return 0
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _strip_json_quotes(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            return str(json.loads(s))
        except Exception:
            return s[1:-1]
    return s


def _clean_state(raw: str) -> str:
    s = (raw or "").strip().lower()
    for prefix in ("cascade_run_status_", "cascade_status_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s or "done"


# ---------------------------------------------------------------------------
# 1. Google Antigravity (`agy`) Adapter
# ---------------------------------------------------------------------------

def _agy_root() -> Path:
    return Path(os.environ.get("ANTIGRAVITY_DATA_DIR", str(Path.home() / ".gemini" / "antigravity")))


def _sum_agy_transcript(
    transcript_path: Path,
    min_step_index: Optional[int] = None,
    max_step_index: Optional[int] = None,
) -> Tuple[int, int, int, int]:
    turns = in_tok = cache_tok = out_tok = 0
    if not transcript_path.is_file():
        return 0, 0, 0, 0
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"PLANNER_RESPONSE"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get("type") == "PLANNER_RESPONSE":
                    sidx = d.get("step_index")
                    if isinstance(sidx, int):
                        if min_step_index is not None and sidx < min_step_index:
                            continue
                        if max_step_index is not None and sidx > max_step_index:
                            continue
                    turns += 1
                    in_tok += _safe_int(d.get("input_tokens"))
                    cache_tok += _safe_int(d.get("cache_read_tokens"))
                    out_tok += _safe_int(d.get("output_tokens"))
    except OSError:
        pass
    return turns, in_tok, cache_tok, out_tok


def _extract_codex_bridge_usages(
    transcript_path: Path,
    min_step_index: Optional[int] = None,
    max_step_index: Optional[int] = None,
) -> Dict[str, Tuple[int, int, int, int]]:
    """
    Extract bridged OpenAI Codex CLI token telemetry emitted by `codex_bridge.py`
    (`[CODEX_BRIDGE_USAGE] {"model": ..., "turns": ..., "input_tokens": ..., ...}`)
    inside Antigravity GENERIC tool-result steps. Returns {model: (turns, in, cache, out)}.
    """
    agg: Dict[str, Tuple[int, int, int, int]] = {}
    if not transcript_path.is_file():
        return agg
    tag = "[CODEX_BRIDGE_USAGE] {"
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "[CODEX_BRIDGE_USAGE]" not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict) or d.get("type") != "GENERIC":
                    continue
                sidx = d.get("step_index")
                if isinstance(sidx, int):
                    if min_step_index is not None and sidx < min_step_index:
                        continue
                    if max_step_index is not None and sidx > max_step_index:
                        continue
                content = d.get("content")
                if not isinstance(content, str):
                    continue
                for raw_line in content.splitlines():
                    s = raw_line.strip()
                    if not (s.startswith(tag) and s.endswith("}")):
                        continue
                    try:
                        payload = json.loads(s[len("[CODEX_BRIDGE_USAGE] "):])
                    except Exception:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    mname = payload.get("model")
                    if not isinstance(mname, str) or not mname.strip():
                        continue
                    t = _safe_int(payload.get("turns"))
                    i = _safe_int(payload.get("input_tokens"))
                    c = _safe_int(payload.get("cache_read_tokens"))
                    o = _safe_int(payload.get("output_tokens"))
                    if t <= 0 and i <= 0 and o <= 0:
                        continue
                    prev = agg.get(mname, (0, 0, 0, 0))
                    agg[mname] = (prev[0] + t, prev[1] + i, prev[2] + c, prev[3] + o)
    except OSError:
        pass
    return agg


def _ingest_members_json_text(content: str, members: Dict[str, Dict[str, str]]) -> Optional[str]:
    found_title: Optional[str] = None
    if '"members"' not in content:
        return None
    for raw_line in content.splitlines():
        raw_line = raw_line.strip()
        if raw_line.startswith("{") and '"members"' in raw_line:
            try:
                obj = json.loads(raw_line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("title"):
                found_title = str(obj["title"])
            raw_members = obj.get("members")
            if not isinstance(raw_members, list):
                continue
            for m in raw_members:
                if not isinstance(m, dict):
                    continue
                mname = m.get("name")
                if not mname:
                    continue
                entry = members.setdefault(str(mname), {})
                if m.get("model"):
                    entry["model"] = str(m["model"])
                if m.get("role"):
                    entry["role"] = str(m["role"])
                if m.get("killed"):
                    entry["state"] = "killed"
                elif m.get("state"):
                    entry["state"] = str(m["state"])
    return found_title


def _parse_agy_swarm_metadata(
    transcript_path: Path, meta_out: Optional[Dict[str, Any]] = None
) -> Tuple[str, Dict[str, Dict[str, str]], Dict[str, str]]:
    """
    Extract (swarm_title, member_meta_by_name, subagent_model_by_cid)
    from a parent conversation's transcript.jsonl (and specific truncated lines in transcript_full.jsonl).
    """
    swarm_title = ""
    members: Dict[str, Dict[str, str]] = {}
    pending_subagent_models: List[Tuple[str, str]] = []
    subagent_cid_models: Dict[str, str] = {}
    truncated_member_steps: Set[int] = set()
    first_swarm_step: Optional[int] = None
    last_swarm_tool_step: Optional[int] = None
    first_user_after_last_swarm: Optional[int] = None
    last_user_step: Optional[int] = None

    if not transcript_path.is_file():
        return swarm_title, members, subagent_cid_models

    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if (
                    "swarm" not in line
                    and "members" not in line
                    and "invoke_subagent" not in line
                    and "conversationId" not in line
                    and '"USER_INPUT"' not in line
                    and "read_board" not in line
                    and "post_message" not in line
                ):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue

                step_idx = d.get("step_index")
                if d.get("type") == "USER_INPUT" and isinstance(step_idx, int):
                    last_user_step = step_idx
                    if (
                        last_swarm_tool_step is not None
                        and first_user_after_last_swarm is None
                        and step_idx > last_swarm_tool_step
                    ):
                        first_user_after_last_swarm = step_idx
                    continue

                tcalls = d.get("tool_calls")
                if isinstance(tcalls, list):
                    for tc in tcalls:
                        if not isinstance(tc, dict):
                            continue
                        tname = tc.get("name")
                        raw_args = tc.get("args")
                        args = raw_args if isinstance(raw_args, dict) else {}
                        if tname == "swarm":
                            action = _strip_json_quotes(args.get("action"))
                            if action in ("add", "title"):
                                if isinstance(step_idx, int) and first_swarm_step is None:
                                    first_swarm_step = (
                                        last_user_step if last_user_step is not None else step_idx
                                    )
                            if first_swarm_step is not None and isinstance(step_idx, int):
                                last_swarm_tool_step = step_idx
                                first_user_after_last_swarm = None
                            if action == "title":
                                t = _strip_json_quotes(args.get("title"))
                                if t:
                                    swarm_title = t
                            elif action in ("add", "model"):
                                wname = _strip_json_quotes(args.get("name"))
                                wmodel = _strip_json_quotes(args.get("model"))
                                if wname and wmodel:
                                    members.setdefault(wname, {})["model"] = wmodel
                        elif tname in ("read_board", "post_message", "swarm_wait"):
                            if first_swarm_step is not None and isinstance(step_idx, int):
                                last_swarm_tool_step = step_idx
                                first_user_after_last_swarm = None
                        elif tname == "invoke_subagent":
                            stype = _strip_json_quotes(args.get("TypeName")) or "subagent"
                            smodel = _strip_json_quotes(args.get("Model")) or "inherit"
                            pending_subagent_models.append((stype, smodel))

                if d.get("type") == "GENERIC":
                    content = d.get("content")
                    if not isinstance(content, str):
                        content = ""
                    if '"members"' in content and (
                        first_swarm_step is None
                        or not isinstance(step_idx, int)
                        or step_idx >= first_swarm_step
                    ):
                        tfields = d.get("truncated_fields")
                        if isinstance(tfields, list) and "content" in tfields:
                            if isinstance(step_idx, int):
                                truncated_member_steps.add(step_idx)
                        t = _ingest_members_json_text(content, members)
                        if t:
                            swarm_title = t
                    if '"conversationId"' in content and pending_subagent_models:
                        for raw_line in content.splitlines():
                            if '"conversationId"' in raw_line:
                                idx = raw_line.find("{")
                                if idx != -1:
                                    try:
                                        obj = json.loads(raw_line[idx:])
                                        if isinstance(obj, dict):
                                            cid = obj.get("conversationId")
                                            if cid and pending_subagent_models:
                                                _, smodel = pending_subagent_models.pop(0)
                                                subagent_cid_models[str(cid)] = smodel
                                    except Exception:
                                        pass
    except OSError:
        pass

    # If any swarm `members` status block was truncated in compact transcript.jsonl,
    # inspect only those exact step_index lines in transcript_full.jsonl.
    if truncated_member_steps:
        full_tp = transcript_path.with_name("transcript_full.jsonl")
        if full_tp.is_file():
            try:
                with full_tp.open("r", encoding="utf-8", errors="replace") as ff:
                    for line in ff:
                        if '\\"members\\":[' not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(d, dict):
                            continue
                        if d.get("step_index") in truncated_member_steps:
                            content_full = d.get("content")
                            if isinstance(content_full, str):
                                t = _ingest_members_json_text(content_full, members)
                                if t:
                                    swarm_title = t
            except OSError:
                pass

    if meta_out is not None:
        meta_out["first_swarm_step"] = first_swarm_step
        meta_out["end_swarm_step"] = (
            first_user_after_last_swarm - 1 if first_user_after_last_swarm is not None else None
        )

    return swarm_title, members, subagent_cid_models


def discover_agy_sessions(
    limit: int = 20, target_id: Optional[str] = None, full_session: bool = False
) -> List[SessionReport]:
    limit = max(1, int(limit))
    agy = _agy_root()
    db_path = agy / "conversation_summaries.db"
    brain_dir = agy / "brain"
    if not db_path.is_file():
        return []

    try:
        db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
        con = sqlite3.connect(db_uri, uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return []

    try:
        if target_id:
            roots = con.execute(
                """
                SELECT conversation_id, title, workspace_uris, status, last_modified_time
                FROM conversation_summaries
                WHERE conversation_id LIKE ?
                ORDER BY last_modified_time DESC
                LIMIT ?
                """,
                (f"{target_id}%", limit),
            ).fetchall()
        else:
            roots = con.execute(
                """
                SELECT p.conversation_id, p.title, p.workspace_uris, p.status,
                       MAX( COALESCE(c.last_modified_time, p.last_modified_time) ) AS latest_mod
                FROM conversation_summaries p
                JOIN conversation_summaries c ON c.parent_conversation_id = p.conversation_id
                WHERE p.parent_conversation_id = ''
                GROUP BY p.conversation_id
                ORDER BY latest_mod DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        reports: List[SessionReport] = []
        for root_cid, root_title, ws_raw, root_status, _ in roots:
            descendants = con.execute(
                """
                WITH RECURSIVE tree(cid, agent_name, status, killed, parent_id, depth) AS (
                    SELECT conversation_id, agent_name, status, killed, parent_conversation_id, 1
                    FROM conversation_summaries
                    WHERE parent_conversation_id = ?
                    UNION ALL
                    SELECT cs.conversation_id, cs.agent_name, cs.status, cs.killed, cs.parent_conversation_id, t.depth + 1
                    FROM conversation_summaries cs
                    JOIN tree t ON cs.parent_conversation_id = t.cid
                    WHERE t.depth < 5
                )
                SELECT cid, agent_name, status, killed, parent_id, depth FROM tree
                """,
                (root_cid,),
            ).fetchall()

            parent_tp = brain_dir / root_cid / ".system_generated" / "logs" / "transcript.jsonl"
            meta_extra: Dict[str, Any] = {}
            swarm_title, member_meta, subagent_models = _parse_agy_swarm_metadata(parent_tp, meta_out=meta_extra)

            coord_model = "-"
            coord_state = _clean_state(root_status)
            for mname, mdata in member_meta.items():
                if mdata.get("role") == "coordinator" or mname == "coordinator":
                    coord_model = mdata.get("model") or coord_model
                    coord_state = _clean_state(mdata.get("state") or coord_state)
                    break

            swarm_worker_rows = [
                row for row in descendants
                if (row[1] in member_meta) or (row[1] not in ("self", "research", "browser", "image-generator", ""))
            ]
            has_swarm_child = bool(member_meta) or bool(swarm_worker_rows)
            all_swarm_workers_killed = bool(swarm_worker_rows) and all(bool(row[3]) for row in swarm_worker_rows)

            min_coord_step = (
                None if full_session or not has_swarm_child else meta_extra.get("first_swarm_step")
            )
            max_coord_step = (
                meta_extra.get("end_swarm_step")
                if (not full_session and has_swarm_child and all_swarm_workers_killed)
                else None
            )
            p_turns, p_in, p_cache, p_out = _sum_agy_transcript(
                parent_tp, min_step_index=min_coord_step, max_step_index=max_coord_step
            )
            latest_mtime = _safe_mtime(parent_tp) if parent_tp.is_file() else 0.0

            agents: List[AgentUsage] = [
                AgentUsage(
                    name="coordinator",
                    role="coordinator",
                    model=coord_model,
                    state=coord_state,
                    turns=p_turns,
                    input_tokens=p_in,
                    cache_read_tokens=p_cache,
                    output_tokens=p_out,
                    agent_id=root_cid[:8],
                )
            ]
            for cb_model, (cb_t, cb_in, cb_cache, cb_out) in _extract_codex_bridge_usages(
                parent_tp, min_step_index=min_coord_step, max_step_index=max_coord_step
            ).items():
                agents.append(
                    AgentUsage(
                        name="coordinator[codex]",
                        role="codex-bridge",
                        model=cb_model,
                        state=coord_state,
                        turns=cb_t,
                        input_tokens=cb_in,
                        cache_read_tokens=cb_cache,
                        output_tokens=cb_out,
                        agent_id=root_cid[:8],
                    )
                )

            for cid, agent_name, st, killed, parent_id, depth in descendants:
                tp = brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
                if tp.is_file():
                    latest_mtime = max(latest_mtime, _safe_mtime(tp))
                w_turns, w_in, w_cache, w_out = _sum_agy_transcript(tp)

                m_info = member_meta.get(agent_name, {})
                is_swarm_worker = bool(m_info) or agent_name not in ("self", "research", "browser", "image-generator", "")
                role = m_info.get("role") or ("worker" if is_swarm_worker else "subagent")
                if depth > 1:
                    role = f"helper(d{depth})"

                raw_model = m_info.get("model") or subagent_models.get(cid) or "inherit"
                if raw_model == "inherit" and coord_model != "-":
                    model = f"{coord_model}*"
                elif raw_model == "flash":
                    model = "gemini-flash"
                else:
                    model = raw_model

                state = "killed" if killed else _clean_state(m_info.get("state") or st)
                display_name = agent_name if is_swarm_worker else f"{agent_name or 'subagent'}-{cid[:4]}"

                agents.append(
                    AgentUsage(
                        name=display_name,
                        role=role,
                        model=model,
                        state=state,
                        turns=w_turns,
                        input_tokens=w_in,
                        cache_read_tokens=w_cache,
                        output_tokens=w_out,
                        agent_id=cid[:8],
                    )
                )
                for cb_model, (cb_t, cb_in, cb_cache, cb_out) in _extract_codex_bridge_usages(tp).items():
                    agents.append(
                        AgentUsage(
                            name=f"{display_name}[codex]",
                            role="codex-bridge",
                            model=cb_model,
                            state=state,
                            turns=cb_t,
                            input_tokens=cb_in,
                            cache_read_tokens=cb_cache,
                            output_tokens=cb_out,
                            agent_id=cid[:8],
                        )
                    )

            ws = ""
            if ws_raw:
                try:
                    arr = json.loads(ws_raw) if ws_raw.startswith("[") else [ws_raw]
                    if arr:
                        ws = str(arr[0]).replace("file://", "")
                except Exception:
                    ws = ws_raw.replace("file://", "")

            has_swarm_workers = bool(member_meta) or any(a.role == "worker" for a in agents[1:])
            title = swarm_title or root_title or (Path(ws).name if ws else root_cid[:12])
            agents_sorted = [agents[0]] + sorted(
                agents[1:], key=lambda a: (a.input_tokens + a.output_tokens, a.cache_read_tokens), reverse=True
            )
            reports.append(
                SessionReport(
                    host="agy",
                    session_id=root_cid,
                    title=title,
                    workspace=ws,
                    updated_at=latest_mtime,
                    agents=agents_sorted,
                    is_swarm=has_swarm_workers,
                )
            )
        return reports
    except sqlite3.Error:
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 2. Claude Code (`claude`) Adapter
# ---------------------------------------------------------------------------

def _sum_claude_jsonl(path: Path) -> Tuple[str, int, int, int, int]:
    """
    Deduplicate streaming chunks by `message.id` (taking last seen usage per message ID).
    Returns (dominant_model, turns, uncached_plus_write_input, cache_read, output).
    """
    by_msg: Dict[str, Tuple[str, int, int, int]] = {}
    anon_idx = 0
    if not path.is_file():
        return "-", 0, 0, 0, 0

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"assistant"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict) or d.get("type") != "assistant":
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                mid = msg.get("id")
                if not mid:
                    anon_idx += 1
                    mid = f"_anon_{anon_idx}"
                model = str(msg.get("model") or "-")
                in_tok = _safe_int(usage.get("input_tokens")) + _safe_int(usage.get("cache_creation_input_tokens"))
                cache_tok = _safe_int(usage.get("cache_read_input_tokens"))
                out_tok = _safe_int(usage.get("output_tokens"))
                by_msg[str(mid)] = (model, in_tok, cache_tok, out_tok)
    except OSError:
        pass

    if not by_msg:
        return "-", 0, 0, 0, 0

    model_counts: Dict[str, int] = {}
    tot_in = tot_cache = tot_out = 0
    for model, i, c, o in by_msg.values():
        if model != "-":
            model_counts[model] = model_counts.get(model, 0) + 1
        tot_in += i
        tot_cache += c
        tot_out += o
    dom_model = max(model_counts.items(), key=lambda kv: kv[1])[0] if model_counts else "-"
    return dom_model, len(by_msg), tot_in, tot_cache, tot_out


def discover_claude_sessions(limit: int = 20, target_id: Optional[str] = None) -> List[SessionReport]:
    limit = max(1, int(limit))
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.is_dir():
        return []

    candidates: List[Tuple[float, Path, Path]] = []
    for sub_dir in projects_root.glob("*/*/subagents"):
        if not sub_dir.is_dir():
            continue
        session_dir = sub_dir.parent
        sid = session_dir.name
        if target_id and not sid.startswith(target_id):
            continue
        sub_files = [f for f in sub_dir.glob("agent-*.jsonl") if f.is_file()]
        if not sub_files:
            continue
        main_jsonl = session_dir.with_suffix(".jsonl")
        mtime = max((_safe_mtime(f) for f in sub_files), default=0.0)
        if main_jsonl.is_file():
            mtime = max(mtime, _safe_mtime(main_jsonl))
        candidates.append((mtime, session_dir, main_jsonl))

    candidates.sort(key=lambda x: x[0], reverse=True)
    reports: List[SessionReport] = []

    for mtime, session_dir, main_jsonl in candidates[:limit]:
        sid = session_dir.name
        proj_slug = session_dir.parent.name
        agents: List[AgentUsage] = []

        if main_jsonl.is_file():
            m_model, m_turns, m_in, m_cache, m_out = _sum_claude_jsonl(main_jsonl)
            agents.append(
                AgentUsage(
                    name="main",
                    role="coordinator",
                    model=m_model,
                    state="done",
                    turns=m_turns,
                    input_tokens=m_in,
                    cache_read_tokens=m_cache,
                    output_tokens=m_out,
                    agent_id=sid[:8],
                )
            )

        sub_usages: List[AgentUsage] = []
        for sf in sorted((session_dir / "subagents").glob("agent-*.jsonl")):
            if not sf.is_file():
                continue
            aid = sf.stem.replace("agent-", "")
            meta_file = sf.with_suffix(".meta.json")
            atype = "subagent"
            desc = ""
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(meta, dict):
                        atype = str(meta.get("agentType") or "subagent")
                        desc = str(meta.get("description") or "")
                except Exception:
                    pass
            s_model, s_turns, s_in, s_cache, s_out = _sum_claude_jsonl(sf)
            label = desc if desc else f"{atype}-{aid[:6]}"
            sub_usages.append(
                AgentUsage(
                    name=label,
                    role=atype,
                    model=s_model,
                    state="done",
                    turns=s_turns,
                    input_tokens=s_in,
                    cache_read_tokens=s_cache,
                    output_tokens=s_out,
                    agent_id=aid[:8],
                )
            )

        sub_usages.sort(key=lambda a: (a.input_tokens + a.output_tokens, a.cache_read_tokens), reverse=True)
        agents.extend(sub_usages)
        reports.append(
            SessionReport(
                host="claude",
                session_id=sid,
                title=proj_slug.lstrip("-").replace("-", "/"),
                workspace=proj_slug,
                updated_at=mtime,
                agents=agents,
            )
        )
    return reports


# ---------------------------------------------------------------------------
# 3. OpenAI Codex (`codex`) Adapter
# ---------------------------------------------------------------------------

def _parse_codex_rollout(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    sid = ""
    parent_id = ""
    nickname = ""
    role = "main"
    cwd = ""
    model = "-"
    turns = 0
    last_totals: Dict[str, Any] = {}

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                t = d.get("type")
                p = d.get("payload")
                if not isinstance(p, dict):
                    continue
                if t == "session_meta":
                    sid = str(p.get("id") or p.get("session_id") or "")
                    cwd = str(p.get("cwd") or "")
                    src = p.get("source")
                    if isinstance(src, dict):
                        sub = src.get("subagent")
                        if isinstance(sub, dict):
                            ts = sub.get("thread_spawn")
                            if isinstance(ts, dict):
                                parent_id = str(ts.get("parent_thread_id") or "")
                                nickname = str(ts.get("agent_nickname") or "")
                                role = str(ts.get("agent_role") or "subagent")
                elif t == "turn_context":
                    turns += 1
                    m = p.get("model")
                    eff = p.get("effort")
                    if m:
                        model = f"{m} ({eff})" if eff else str(m)
                elif t == "event_msg" and p.get("type") == "token_count":
                    info = p.get("info")
                    if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                        last_totals = info["total_token_usage"]
    except OSError:
        return None

    if not sid:
        return None

    raw_in = _safe_int(last_totals.get("input_tokens"))
    cached_in = _safe_int(last_totals.get("cached_input_tokens"))
    uncached_in = max(0, raw_in - cached_in) + _safe_int(last_totals.get("cache_write_input_tokens"))
    out_tok = _safe_int(last_totals.get("output_tokens"))

    return {
        "sid": sid,
        "parent_id": parent_id,
        "nickname": nickname,
        "role": role,
        "cwd": cwd,
        "model": model,
        "turns": turns,
        "uncached_in": uncached_in,
        "cached_in": cached_in,
        "out_tok": out_tok,
        "mtime": _safe_mtime(path),
    }


def discover_codex_sessions(limit: int = 20, target_id: Optional[str] = None) -> List[SessionReport]:
    limit = max(1, int(limit))
    sess_root = Path.home() / ".codex" / "sessions"
    if not sess_root.is_dir():
        return []

    valid_files = [p for p in sess_root.rglob("rollout-*.jsonl") if p.is_file()]
    files = sorted(valid_files, key=_safe_mtime, reverse=True)[:150]
    parsed: Dict[str, Dict[str, Any]] = {}
    children_by_parent: Dict[str, List[str]] = {}

    for f in files:
        info = _parse_codex_rollout(f)
        if not info:
            continue
        sid = info["sid"]
        parsed[sid] = info
        pid = info["parent_id"]
        if pid:
            children_by_parent.setdefault(pid, []).append(sid)

    reports: List[SessionReport] = []
    for sid, info in parsed.items():
        if info["parent_id"]:
            continue
        if target_id and not sid.startswith(target_id):
            continue
        child_ids = children_by_parent.get(sid, [])
        if not child_ids and not target_id:
            continue

        agents: List[AgentUsage] = [
            AgentUsage(
                name="main",
                role="coordinator",
                model=info["model"],
                state="done",
                turns=info["turns"],
                input_tokens=info["uncached_in"],
                cache_read_tokens=info["cached_in"],
                output_tokens=info["out_tok"],
                agent_id=sid[:8],
            )
        ]
        latest = info["mtime"]
        subs: List[AgentUsage] = []
        for cid in child_ids:
            c = parsed.get(cid)
            if not c:
                continue
            latest = max(latest, c["mtime"])
            subs.append(
                AgentUsage(
                    name=c["nickname"] or f"{c['role']}-{cid[:6]}",
                    role=c["role"],
                    model=c["model"],
                    state="done",
                    turns=c["turns"],
                    input_tokens=c["uncached_in"],
                    cache_read_tokens=c["cached_in"],
                    output_tokens=c["out_tok"],
                    agent_id=cid[:8],
                )
            )
        subs.sort(key=lambda a: (a.input_tokens + a.output_tokens, a.cache_read_tokens), reverse=True)
        agents.extend(subs)
        reports.append(
            SessionReport(
                host="codex",
                session_id=sid,
                title=Path(info["cwd"]).name if info["cwd"] else sid[:12],
                workspace=info["cwd"],
                updated_at=latest,
                agents=agents,
            )
        )

    reports.sort(key=lambda r: r.updated_at, reverse=True)
    return reports[:limit]


# ---------------------------------------------------------------------------
# Rendering (Terminal Table / Markdown / JSON)
# ---------------------------------------------------------------------------

def build_model_rollups(report: SessionReport) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    tot_in = max(1, report.total_input)
    for a in report.agents:
        if a.turns == 0 and a.input_tokens == 0 and a.output_tokens == 0:
            continue
        m = a.model.rstrip("*") if a.model else "-"
        b = buckets.setdefault(
            m,
            {"model": m, "agents": 0, "turns": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0},
        )
        b["agents"] += 1
        b["turns"] += a.turns
        b["input_tokens"] += a.input_tokens
        b["cache_read_tokens"] += a.cache_read_tokens
        b["output_tokens"] += a.output_tokens

    rows = sorted(buckets.values(), key=lambda x: (x["input_tokens"] + x["output_tokens"]), reverse=True)
    for r in rows:
        r["input_share_pct"] = round(r["input_tokens"] * 100.0 / tot_in, 1)
    return rows


def detect_anomalies(report: SessionReport) -> List[str]:
    warnings: List[str] = []
    tot_in = report.total_input
    if tot_in <= 0:
        return warnings
    for a in report.agents:
        base = a.name.split("-")[0].lower()
        if base in ("monitor", "narrator"):
            share = a.input_tokens * 100.0 / tot_in
            if share >= 25.0 or a.turns >= 80:
                warnings.append(
                    f"Daemon `{a.name}` ran {a.turns} turns ({fmt_num(a.input_tokens)} uncached input, "
                    f"{share:.1f}% of swarm input). Ensure it stays on L3 lightweight tier and kill idle swarms promptly."
                )
    return warnings


def render_report_text(report: SessionReport, markdown: bool = False) -> str:
    lines: List[str] = []
    rollups = build_model_rollups(report)
    anomalies = detect_anomalies(report)

    if markdown:
        lines.append(f"### Swarm Mix Token Ledger — `{report.title}` (`{report.host}:{report.session_id[:8]}`)")
        lines.append(
            f"- **Updated**: `{fmt_ts(report.updated_at)}` | **Agents**: `{len(report.agents)}` | "
            f"**Turns**: `{report.total_turns}` | **Uncached Input**: `{fmt_num(report.total_input)}` | "
            f"**Cache Read**: `{fmt_num(report.total_cache)}` (`{report.cache_hit_pct:.1f}%`) | "
            f"**Output**: `{fmt_num(report.total_output)}`"
        )
        if rollups:
            lines.append("")
            lines.append("| Model Tier | Agents | Turns | Input (New) | Cache Read | Output | Input Share |")
            lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | ---: |")
            for r in rollups:
                lines.append(
                    f"| `{r['model']}` | {r['agents']} | {r['turns']} | {fmt_num(r['input_tokens'])} | "
                    f"{fmt_num(r['cache_read_tokens'])} | {fmt_num(r['output_tokens'])} | {r['input_share_pct']:.1f}% |"
                )
        for w in anomalies:
            lines.append(f"\n> ⚠️ **Hotspot**: {w}")

        def _agent_table(rows: List[AgentUsage]) -> List[str]:
            tbl = [
                "",
                "| Agent | Role | Model | State | Turns | Input (New) | Cache Read | Output | Cache% |",
                "| :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: |",
            ]
            for a in rows:
                tbl.append(
                    f"| `{a.name}` | {a.role} | `{a.model}` | {a.state} | {a.turns} | "
                    f"{fmt_num(a.input_tokens)} | {fmt_num(a.cache_read_tokens)} | {fmt_num(a.output_tokens)} | {a.cache_hit_pct:.0f}% |"
                )
            return tbl

        if len(report.agents) > 12:
            lines.extend(_agent_table(report.agents[:8]))
            lines.append("")
            lines.append(f"<details><summary>展开全部 {len(report.agents)} 个成员明细</summary>")
            lines.extend(_agent_table(report.agents))
            lines.append("\n</details>")
        else:
            lines.extend(_agent_table(report.agents))
        return "\n".join(lines)

    bar = "─" * 100
    lines.append(bar)
    lines.append(f" Swarm Mix Token Ledger  │  [{report.host.upper()}] {report.title}")
    lines.append(
        f" Session: {report.session_id}  │  Updated: {fmt_ts(report.updated_at)}  │  Agents: {len(report.agents)}  │  Turns: {report.total_turns}"
    )
    lines.append(
        f" Totals : Input (New) = {fmt_num(report.total_input)}  │  Cache Read = {fmt_num(report.total_cache)} "
        f"({report.cache_hit_pct:.1f}% hit)  │  Output = {fmt_num(report.total_output)}"
    )
    lines.append(bar)
    lines.append(
        " "
        + " ".join(
            [
                pad_col("AGENT", 22),
                pad_col("ROLE", 12),
                pad_col("MODEL", 27),
                pad_col("STATE", 8),
                pad_col("TURNS", 5, True),
                pad_col("INPUT", 7, True),
                pad_col("CACHE", 8, True),
                pad_col("OUTPUT", 7, True),
            ]
        )
    )
    lines.append(" " + "·" * 98)
    for a in report.agents:
        lines.append(
            " "
            + " ".join(
                [
                    pad_col(a.name, 22),
                    pad_col(a.role, 12),
                    pad_col(a.model, 27),
                    pad_col(a.state, 8),
                    pad_col(str(a.turns), 5, True),
                    pad_col(fmt_num(a.input_tokens), 7, True),
                    pad_col(fmt_num(a.cache_read_tokens), 8, True),
                    pad_col(fmt_num(a.output_tokens), 7, True),
                ]
            )
        )

    if rollups:
        lines.append(bar)
        lines.append(
            " "
            + " ".join(
                [
                    pad_col("BY MODEL (* = inherited coordinator)", 44),
                    pad_col("AGENTS", 6, True),
                    pad_col("TURNS", 6, True),
                    pad_col("INPUT", 8, True),
                    pad_col("CACHE", 9, True),
                    pad_col("OUTPUT", 8, True),
                    pad_col("SHARE", 7, True),
                ]
            )
        )
        lines.append(" " + "·" * 98)
        for r in rollups:
            lines.append(
                " "
                + " ".join(
                    [
                        pad_col(r["model"], 44),
                        pad_col(str(r["agents"]), 6, True),
                        pad_col(str(r["turns"]), 6, True),
                        pad_col(fmt_num(r["input_tokens"]), 8, True),
                        pad_col(fmt_num(r["cache_read_tokens"]), 9, True),
                        pad_col(fmt_num(r["output_tokens"]), 8, True),
                        pad_col(f"{r['input_share_pct']:.1f}%", 7, True),
                    ]
                )
            )

    lines.append(bar)
    for w in anomalies:
        lines.append(f" [!] Hotspot: {w}")
    return "\n".join(lines)


def collect_sessions(
    host: str,
    limit: int = 20,
    target_id: Optional[str] = None,
    include_subagent_only: bool = False,
    full_session: bool = False,
) -> List[SessionReport]:
    limit = max(1, int(limit))
    reports: List[SessionReport] = []
    if host in ("all", "agy"):
        agy_reps = discover_agy_sessions(
            limit=max(limit, 20), target_id=target_id, full_session=full_session
        )
        if not target_id and not include_subagent_only:
            swarms = [r for r in agy_reps if r.is_swarm]
            if swarms:
                agy_reps = swarms
        reports.extend(agy_reps)
    if host in ("all", "claude"):
        reports.extend(discover_claude_sessions(limit=limit, target_id=target_id))
    if host in ("all", "codex"):
        reports.extend(discover_codex_sessions(limit=limit, target_id=target_id))
    reports.sort(key=lambda r: r.updated_at, reverse=True)
    return reports[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="swarm-tokens",
        description="Inspect per-agent & per-model token consumption across Antigravity Swarm, Claude Code, and OpenAI Codex.",
    )
    parser.add_argument("--host", choices=["all", "agy", "claude", "codex"], default="all", help="Filter by agent host (default: all)")
    parser.add_argument("--id", dest="target_id", help="Inspect a specific root conversation/session ID (prefix match)")
    parser.add_argument("--list", action="store_true", help="List recent multi-agent / Swarm sessions")
    parser.add_argument("-n", "--limit", type=int, default=10, help="Number of sessions for --list (default: 10)")
    parser.add_argument("--full-session", action="store_true", help="Include pre-Swarm parent conversation turns in coordinator totals")
    parser.add_argument("--markdown", "-m", action="store_true", help="Format output as Markdown tables")
    parser.add_argument("--json", "-j", action="store_true", help="Format output as JSON")
    parser.add_argument("--watch", "-w", nargs="?", const=3.0, type=float, metavar="SEC", help="Live-refresh every SEC seconds (default: 3s)")
    args = parser.parse_args()

    if args.list:
        sessions = collect_sessions(
            args.host, limit=args.limit, target_id=args.target_id, full_session=args.full_session
        )
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "host": s.host,
                            "session_id": s.session_id,
                            "title": s.title,
                            "updated_at": fmt_ts(s.updated_at),
                            "agents": len(s.agents),
                            "turns": s.total_turns,
                            "input_tokens": s.total_input,
                            "cache_read_tokens": s.total_cache,
                            "output_tokens": s.total_output,
                        }
                        for s in sessions
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not sessions:
            print("No multi-agent or Swarm sessions found.")
            return 0
        print(
            " "
            + " ".join(
                [
                    pad_col("HOST", 6),
                    pad_col("ID", 9),
                    pad_col("UPDATED", 16),
                    pad_col("AGENTS", 6, True),
                    pad_col("TURNS", 6, True),
                    pad_col("INPUT", 8, True),
                    pad_col("CACHE", 9, True),
                    pad_col("OUTPUT", 8, True),
                    " TITLE",
                ]
            )
        )
        print(" " + "─" * 98)
        for s in sessions:
            print(
                " "
                + " ".join(
                    [
                        pad_col(s.host, 6),
                        pad_col(s.session_id[:8], 9),
                        pad_col(fmt_ts(s.updated_at), 16),
                        pad_col(str(len(s.agents)), 6, True),
                        pad_col(str(s.total_turns), 6, True),
                        pad_col(fmt_num(s.total_input), 8, True),
                        pad_col(fmt_num(s.total_cache), 9, True),
                        pad_col(fmt_num(s.total_output), 8, True),
                        " " + pad_col(s.title, 26),
                    ]
                )
            )
        return 0

    def render_once() -> int:
        sessions = collect_sessions(
            args.host, limit=5, target_id=args.target_id, full_session=args.full_session
        )
        if not sessions:
            print("No matching multi-agent or Swarm session found.")
            return 1
        rep = sessions[0]
        if args.json:
            payload = {
                "host": rep.host,
                "session_id": rep.session_id,
                "title": rep.title,
                "workspace": rep.workspace,
                "updated_at": fmt_ts(rep.updated_at),
                "totals": {
                    "turns": rep.total_turns,
                    "input_tokens": rep.total_input,
                    "cache_read_tokens": rep.total_cache,
                    "output_tokens": rep.total_output,
                    "cache_hit_pct": round(rep.cache_hit_pct, 1),
                },
                "agents": [dataclasses.asdict(a) for a in rep.agents],
                "by_model": build_model_rollups(rep),
                "anomalies": detect_anomalies(rep),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_report_text(rep, markdown=args.markdown))
        return 0

    if args.watch:
        try:
            while True:
                sys.stdout.write("\033[2J\033[H")
                render_once()
                sys.stdout.write(f"\n Watching every {args.watch:.1f}s — press Ctrl+C to stop.\n")
                sys.stdout.flush()
                time.sleep(max(0.5, args.watch))
        except KeyboardInterrupt:
            return 0
    return render_once()


if __name__ == "__main__":
    raise SystemExit(main())
