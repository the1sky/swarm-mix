#!/usr/bin/env python3
"""
Self-contained unittest suite for swarm_tokens.py.

Covers:
  1. Antigravity compact transcript + transcript_full.jsonl truncated members model recovery
  2. Claude Code streaming message.id usage deduplication
  3. Codex cumulative token_count parsing
  4. is_swarm session classification & sorting/filtering priority
  5. Edge cases (malformed JSONL lines, missing SQLite DB, zero-token division, rollups & anomalies)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "swarm-mix" / "scripts" / "swarm_tokens.py"


def _load_swarm_tokens_module():
    spec = importlib.util.spec_from_file_location("swarm_tokens", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"Unable to load {SCRIPT_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["swarm_tokens"] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load_swarm_tokens_module()


class TestSwarmTokensSuite(unittest.TestCase):
    """Comprehensive unit tests for swarm_tokens.py using isolated tempfile directories."""

    # -----------------------------------------------------------------------
    # 1. Antigravity compact transcript + transcript_full.jsonl recovery
    # -----------------------------------------------------------------------
    def test_agy_truncated_members_model_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir) / "brain" / "root-conv-001" / ".system_generated" / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            compact_tp = logs_dir / "transcript.jsonl"
            full_tp = logs_dir / "transcript_full.jsonl"

            # Step 1: swarm title tool call
            step1 = {
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "input_tokens": 1200,
                "cache_read_tokens": 3000,
                "output_tokens": 150,
                "tool_calls": [
                    {"name": "swarm", "args": {"action": '"title"', "title": '"Initial Title"'}},
                    {"name": "invoke_subagent", "args": {"TypeName": '"research"', "Model": '"gemini-4-argon"'}},
                ],
            }
            # Step 2: subagent conversationId binding
            step2 = {
                "step_index": 2,
                "type": "GENERIC",
                "content": 'Subagent started: {"conversationId": "sub-cid-999"}',
            }
            # Step 3: truncated GENERIC members status in compact transcript.jsonl
            # Worker-b and worker-c models are cut off in compact transcript.jsonl
            truncated_inner = (
                '{"title":"Recovered Swarm Title","members":['
                '{"name":"coordinator","role":"coordinator","model":"gemini-4-carbon","state":"working"},'
                '{"name":"worker-a","role":"worker","model":"claude-opus-4-6...'
            )
            step3_compact = {
                "step_index": 3,
                "type": "GENERIC",
                "truncated_fields": ["content"],
                "content": truncated_inner,
            }

            # Step 3 full in transcript_full.jsonl (compact JSON inside content so \"members\":[ matches)
            full_inner_obj = {
                "title": "Recovered Swarm Title",
                "members": [
                    {"name": "coordinator", "role": "coordinator", "model": "gemini-4-carbon", "state": "working"},
                    {"name": "worker-a", "role": "worker", "model": "claude-opus-4-6", "state": "waiting"},
                    {"name": "worker-b", "role": "worker", "model": "gpt-5.4", "killed": True},
                ],
            }
            step3_full = {
                "step_index": 3,
                "type": "GENERIC",
                "content": json.dumps(full_inner_obj, separators=(",", ":")),
            }
            # Unrelated step in transcript_full.jsonl that should be ignored
            step4_full = {
                "step_index": 99,
                "type": "GENERIC",
                "content": json.dumps(
                    {
                        "title": "Should Not Be Read",
                        "members": [{"name": "worker-a", "role": "worker", "model": "wrong-model"}],
                    },
                    separators=(",", ":"),
                ),
            }

            compact_tp.write_text(
                "\n".join(json.dumps(x) for x in [step1, step2, step3_compact]) + "\n",
                encoding="utf-8",
            )
            full_tp.write_text(
                "\n".join(json.dumps(x) for x in [step1, step2, step3_full, step4_full]) + "\n",
                encoding="utf-8",
            )

            title, members, sub_models = st._parse_agy_swarm_metadata(compact_tp)
            self.assertEqual(title, "Recovered Swarm Title")
            self.assertEqual(members["coordinator"]["model"], "gemini-4-carbon")
            self.assertEqual(members["coordinator"]["state"], "working")
            self.assertEqual(members["worker-a"]["model"], "claude-opus-4-6")
            self.assertEqual(members["worker-a"]["state"], "waiting")
            self.assertEqual(members["worker-b"]["model"], "gpt-5.4")
            self.assertEqual(members["worker-b"]["state"], "killed")
            self.assertEqual(sub_models.get("sub-cid-999"), "gemini-4-argon")

            turns, in_tok, cache_tok, out_tok = st._sum_agy_transcript(compact_tp)
            self.assertEqual((turns, in_tok, cache_tok, out_tok), (1, 1200, 3000, 150))

    # -----------------------------------------------------------------------
    # 2. Claude Code streaming message.id usage deduplication
    # -----------------------------------------------------------------------
    def test_claude_streaming_message_id_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            session_dir = home / ".claude" / "projects" / "-Users-demo-project" / "sess-claude-001"
            sub_dir = session_dir / "subagents"
            sub_dir.mkdir(parents=True, exist_ok=True)
            main_jsonl = session_dir.with_suffix(".jsonl")

            # Main session: msg_1 streamed in 3 chunks, msg_2 streamed in 2 chunks, plus 1 anonymous message
            main_lines = [
                # Chunk 1 of msg_1 (partial output)
                json.dumps({
                    "type": "assistant",
                    "message": {
                        "id": "msg_1",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 50,
                            "cache_read_input_tokens": 500,
                            "output_tokens": 10,
                        },
                    },
                }),
                # Chunk 2 of msg_1 (intermediate output)
                json.dumps({
                    "type": "assistant",
                    "message": {
                        "id": "msg_1",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 50,
                            "cache_read_input_tokens": 500,
                            "output_tokens": 45,
                        },
                    },
                }),
                # Chunk 3 of msg_1 (final usage for msg_1: input=100+50=150, cache=500, out=90)
                json.dumps({
                    "type": "assistant",
                    "message": {
                        "id": "msg_1",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 50,
                            "cache_read_input_tokens": 500,
                            "output_tokens": 90,
                        },
                    },
                }),
                # Non-assistant line and malformed line (should be ignored)
                '{"type": "user", "message": {"id": "msg_1"}}',
                '{"type": "assistant", malformed json',
                # Chunk 1 of msg_2
                json.dumps({
                    "type": "assistant",
                    "message": {
                        "id": "msg_2",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input_tokens": 200,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 650,
                            "output_tokens": 15,
                        },
                    },
                }),
                # Chunk 2 of msg_2 (final usage for msg_2: input=200, cache=650, out=60)
                json.dumps({
                    "type": "assistant",
                    "message": {
                        "id": "msg_2",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input_tokens": 200,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 650,
                            "output_tokens": 60,
                        },
                    },
                }),
                # Anonymous message without id (input=50, cache=100, out=20)
                json.dumps({
                    "type": "assistant",
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 50,
                            "cache_read_input_tokens": 100,
                            "output_tokens": 20,
                        },
                    },
                }),
            ]
            main_jsonl.write_text("\n".join(main_lines) + "\n", encoding="utf-8")

            dom_model, turns, tot_in, tot_cache, tot_out = st._sum_claude_jsonl(main_jsonl)
            self.assertEqual(dom_model, "claude-opus-4-6")
            self.assertEqual(turns, 3)  # msg_1, msg_2, _anon_1
            self.assertEqual(tot_in, 150 + 200 + 50)
            self.assertEqual(tot_cache, 500 + 650 + 100)
            self.assertEqual(tot_out, 90 + 60 + 20)

            # Subagent file + meta
            sub_jsonl = sub_dir / "agent-sub01.jsonl"
            sub_meta = sub_dir / "agent-sub01.meta.json"
            sub_jsonl.write_text(
                "\n".join([
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "id": "sub_msg_1",
                            "model": "claude-haiku-4-5",
                            "usage": {"input_tokens": 80, "cache_read_input_tokens": 20, "output_tokens": 5},
                        },
                    }),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "id": "sub_msg_1",
                            "model": "claude-haiku-4-5",
                            "usage": {"input_tokens": 80, "cache_read_input_tokens": 20, "output_tokens": 35},
                        },
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            sub_meta.write_text(json.dumps({"agentType": "verifier", "description": "qa-checker"}), encoding="utf-8")

            with mock.patch.object(Path, "home", return_value=home):
                reports = st.discover_claude_sessions(limit=10)
                self.assertEqual(len(reports), 1)
                rep = reports[0]
                self.assertEqual(rep.host, "claude")
                self.assertEqual(rep.session_id, "sess-claude-001")
                self.assertEqual(len(rep.agents), 2)
                self.assertEqual(rep.agents[0].name, "main")
                self.assertEqual(rep.agents[0].turns, 3)
                self.assertEqual(rep.agents[0].output_tokens, 170)
                self.assertEqual(rep.agents[1].name, "qa-checker")
                self.assertEqual(rep.agents[1].role, "verifier")
                self.assertEqual(rep.agents[1].turns, 1)
                self.assertEqual(rep.agents[1].output_tokens, 35)

    # -----------------------------------------------------------------------
    # 3. Codex cumulative token_count parsing
    # -----------------------------------------------------------------------
    def test_codex_cumulative_token_count_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            sess_dir = home / ".codex" / "sessions" / "2026" / "11" / "14"
            sess_dir.mkdir(parents=True, exist_ok=True)

            root_rollout = sess_dir / "rollout-root.jsonl"
            child_rollout = sess_dir / "rollout-child.jsonl"

            # Root session with 2 turns and 2 cumulative token_count events (only last should be used)
            root_events = [
                {
                    "type": "session_meta",
                    "payload": {"id": "codex-root-100", "cwd": "/workspace/my-service"},
                },
                {
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.4", "effort": "high"},
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 600,
                                "cache_write_input_tokens": 50,
                                "output_tokens": 120,
                            }
                        },
                    },
                },
                {
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.4", "effort": "high"},
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 3500,
                                "cached_input_tokens": 2500,
                                "cache_write_input_tokens": 100,
                                "output_tokens": 400,
                            }
                        },
                    },
                },
            ]
            root_rollout.write_text("\n".join(json.dumps(e) for e in root_events) + "\n", encoding="utf-8")

            # Child rollout spawned from codex-root-100
            child_events = [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "codex-child-200",
                        "cwd": "/workspace/my-service",
                        "source": {
                            "subagent": {
                                "thread_spawn": {
                                    "parent_thread_id": "codex-root-100",
                                    "agent_nickname": "auditor",
                                    "agent_role": "verifier",
                                }
                            }
                        },
                    },
                },
                {
                    "type": "turn_context",
                    "payload": {"model": "o4-mini"},
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 800,
                                "cached_input_tokens": 300,
                                "cache_write_input_tokens": 0,
                                "output_tokens": 95,
                            }
                        },
                    },
                },
            ]
            child_rollout.write_text("\n".join(json.dumps(e) for e in child_events) + "\n", encoding="utf-8")

            parsed_root = st._parse_codex_rollout(root_rollout)
            self.assertIsNotNone(parsed_root)
            assert parsed_root is not None
            # uncached_in = max(0, 3500 - 2500) + 100 = 1100
            self.assertEqual(parsed_root["uncached_in"], 1100)
            self.assertEqual(parsed_root["cached_in"], 2500)
            self.assertEqual(parsed_root["out_tok"], 400)
            self.assertEqual(parsed_root["turns"], 2)
            self.assertEqual(parsed_root["model"], "gpt-5.4 (high)")

            with mock.patch.object(Path, "home", return_value=home):
                reports = st.discover_codex_sessions(limit=10)
                self.assertEqual(len(reports), 1)
                rep = reports[0]
                self.assertEqual(rep.session_id, "codex-root-100")
                self.assertEqual(rep.title, "my-service")
                self.assertEqual(len(rep.agents), 2)
                self.assertEqual(rep.agents[0].name, "main")
                self.assertEqual(rep.agents[0].input_tokens, 1100)
                self.assertEqual(rep.agents[0].cache_read_tokens, 2500)
                self.assertEqual(rep.agents[0].output_tokens, 400)
                self.assertEqual(rep.agents[1].name, "auditor")
                self.assertEqual(rep.agents[1].role, "verifier")
                self.assertEqual(rep.agents[1].model, "o4-mini")
                self.assertEqual(rep.agents[1].input_tokens, 500)
                self.assertEqual(rep.agents[1].cache_read_tokens, 300)
                self.assertEqual(rep.agents[1].output_tokens, 95)

    # -----------------------------------------------------------------------
    # 4. is_swarm session classification & sorting/filtering priority
    # -----------------------------------------------------------------------
    def test_is_swarm_session_sorting_and_filtering_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agy_dir = Path(tmpdir)
            db_path = agy_dir / "conversation_summaries.db"
            brain_dir = agy_dir / "brain"

            con = sqlite3.connect(str(db_path))
            con.execute(
                """
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    workspace_uris TEXT,
                    status TEXT,
                    last_modified_time REAL,
                    parent_conversation_id TEXT,
                    agent_name TEXT,
                    killed INTEGER DEFAULT 0
                )
                """
            )

            # Session A: older Swarm session (has real swarm worker 'test-author')
            # Session B: newer subagent-only session (only 'research' subagent, no swarm metadata)
            # Session C: newer Swarm session (has swarm metadata + worker)
            con.executemany(
                """
                INSERT INTO conversation_summaries
                (conversation_id, title, workspace_uris, status, last_modified_time, parent_conversation_id, agent_name, killed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("swarm-older", "Older Swarm", '["file:///tmp/ws1"]', "CASCADE_STATUS_DONE", 100.0, "", "", 0),
                    ("worker-older-w1", "", "", "CASCADE_STATUS_DONE", 110.0, "swarm-older", "test-author", 0),
                    ("subonly-newest", "Subagent Only", '["file:///tmp/ws2"]', "CASCADE_STATUS_DONE", 300.0, "", "", 0),
                    ("child-subonly-r1", "", "", "CASCADE_STATUS_DONE", 310.0, "subonly-newest", "research", 0),
                    ("swarm-newer", "Newer Swarm", '["file:///tmp/ws3"]', "CASCADE_STATUS_RUNNING", 200.0, "", "", 0),
                    ("worker-newer-w1", "", "", "CASCADE_STATUS_WAITING", 210.0, "swarm-newer", "verifier", 0),
                ],
            )
            con.commit()
            con.close()

            def _write_tp(cid: str, mtime: float, lines: list[dict]) -> None:
                p = brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
                os.utime(p, (mtime, mtime))

            _write_tp("swarm-older", 100.0, [{"type": "PLANNER_RESPONSE", "input_tokens": 500, "output_tokens": 50}])
            _write_tp("worker-older-w1", 110.0, [{"type": "PLANNER_RESPONSE", "input_tokens": 300, "output_tokens": 30}])
            _write_tp("subonly-newest", 300.0, [{"type": "PLANNER_RESPONSE", "input_tokens": 900, "output_tokens": 90}])
            _write_tp("child-subonly-r1", 310.0, [{"type": "PLANNER_RESPONSE", "input_tokens": 400, "output_tokens": 40}])
            _write_tp(
                "swarm-newer",
                200.0,
                [
                    {
                        "type": "PLANNER_RESPONSE",
                        "input_tokens": 700,
                        "output_tokens": 70,
                        "tool_calls": [{"name": "swarm", "args": {"action": '"title"', "title": '"Active Swarm"'}}],
                    }
                ],
            )
            _write_tp("worker-newer-w1", 210.0, [{"type": "PLANNER_RESPONSE", "input_tokens": 600, "output_tokens": 60}])

            with mock.patch.dict(os.environ, {"ANTIGRAVITY_DATA_DIR": str(agy_dir)}):
                # 1) discover_agy_sessions marks is_swarm accurately
                raw_reps = st.discover_agy_sessions(limit=10)
                by_id = {r.session_id: r for r in raw_reps}
                self.assertTrue(by_id["swarm-older"].is_swarm)
                self.assertTrue(by_id["swarm-newer"].is_swarm)
                self.assertFalse(by_id["subonly-newest"].is_swarm)

                # 2) Default collect_sessions prioritizes is_swarm=True over newer subagent-only session,
                #    and sorts the swarm sessions by updated_at descending
                default_reps = st.collect_sessions(host="agy", limit=10)
                self.assertEqual([r.session_id for r in default_reps], ["swarm-newer", "swarm-older"])
                self.assertEqual(default_reps[0].title, "Active Swarm")

                # 3) include_subagent_only=True includes subonly-newest and sorts all by updated_at desc
                all_reps = st.collect_sessions(host="agy", limit=10, include_subagent_only=True)
                self.assertEqual([r.session_id for r in all_reps], ["subonly-newest", "swarm-newer", "swarm-older"])

                # 4) Explicit target_id can still inspect subagent-only session directly
                targeted = st.collect_sessions(host="agy", target_id="subonly")
                self.assertEqual(len(targeted), 1)
                self.assertEqual(targeted[0].session_id, "subonly-newest")

    # -----------------------------------------------------------------------
    # 5. Edge Cases (missing DB, malformed JSONL, zero-token division, rollups)
    # -----------------------------------------------------------------------
    def test_edge_cases_and_zero_division_guards(self) -> None:
        # Zero-token division safety on AgentUsage and SessionReport
        empty_agent = st.AgentUsage(name="idle", role="worker", model="gemini-4-carbon", state="waiting")
        self.assertEqual(empty_agent.total_context_tokens, 0)
        self.assertEqual(empty_agent.cache_hit_pct, 0.0)

        empty_report = st.SessionReport(
            host="agy",
            session_id="zero-sess",
            title="Zero Tokens",
            workspace="/tmp",
            updated_at=0.0,
            agents=[empty_agent],
        )
        self.assertEqual(empty_report.cache_hit_pct, 0.0)
        self.assertEqual(st.build_model_rollups(empty_report), [])
        self.assertEqual(st.detect_anomalies(empty_report), [])

        # Missing SQLite DB returns empty list cleanly
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_DATA_DIR": tmpdir}):
                self.assertEqual(st.discover_agy_sessions(), [])

        # Hotspot detection for runaway monitor daemon
        busy_report = st.SessionReport(
            host="agy",
            session_id="hotspot-sess",
            title="Hotspot Test",
            workspace="/tmp",
            updated_at=1700000000.0,
            agents=[
                st.AgentUsage(
                    name="coordinator",
                    role="coordinator",
                    model="gemini-4-carbon",
                    state="done",
                    turns=5,
                    input_tokens=500,
                    cache_read_tokens=1000,
                    output_tokens=100,
                ),
                st.AgentUsage(
                    name="monitor",
                    role="worker",
                    model="gemini-flash",
                    state="waiting",
                    turns=12,
                    input_tokens=500,  # 50% of total uncached input >= 25% threshold
                    cache_read_tokens=0,
                    output_tokens=50,
                ),
            ],
        )
        warnings = st.detect_anomalies(busy_report)
        self.assertEqual(len(warnings), 1)
        self.assertIn("monitor", warnings[0])
        md_out = st.render_report_text(busy_report, markdown=True)
        self.assertIn("Hotspot", md_out)

    # -----------------------------------------------------------------------
    # 6. Cross-Family Verifier Edge-Case Regression Tests + Coordinator Slice
    # -----------------------------------------------------------------------
    def test_bug1_non_dict_jsonl_lines_do_not_crash(self) -> None:
        """Non-dict JSON lines (e.g. '"PLANNER_RESPONSE"', '"assistant"', '123') should be ignored, not raise AttributeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p_agy = Path(tmpdir) / "transcript.jsonl"
            p_agy.write_text('"PLANNER_RESPONSE"\n', encoding="utf-8")
            self.assertEqual(st._sum_agy_transcript(p_agy), (0, 0, 0, 0))

            p_claude = Path(tmpdir) / "claude.jsonl"
            p_claude.write_text('"assistant"\n', encoding="utf-8")
            self.assertEqual(st._sum_claude_jsonl(p_claude), ("-", 0, 0, 0, 0))

            p_codex = Path(tmpdir) / "rollout-1.jsonl"
            p_codex.write_text("123\n", encoding="utf-8")
            self.assertIsNone(st._parse_codex_rollout(p_codex))

    def test_bug2_non_numeric_token_values_do_not_crash(self) -> None:
        """Non-numeric token values ('abc', '1.5') should be handled gracefully without raising ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p_agy = Path(tmpdir) / "transcript.jsonl"
            p_agy.write_text(json.dumps({"type": "PLANNER_RESPONSE", "input_tokens": "abc"}) + "\n", encoding="utf-8")
            self.assertEqual(st._sum_agy_transcript(p_agy), (1, 0, 0, 0))

            p_claude = Path(tmpdir) / "claude.jsonl"
            p_claude.write_text(
                json.dumps({
                    "type": "assistant",
                    "message": {"id": "m1", "model": "claude-opus-4-6", "usage": {"input_tokens": "1.5"}},
                })
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(st._sum_claude_jsonl(p_claude), ("claude-opus-4-6", 1, 0, 0, 0))

            p_codex = Path(tmpdir) / "rollout-1.jsonl"
            p_codex.write_text(
                "\n".join([
                    json.dumps({"type": "session_meta", "payload": {"id": "cid-1"}}),
                    json.dumps({
                        "type": "event_msg",
                        "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": "abc"}}},
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            self.assertIsNotNone(st._parse_codex_rollout(p_codex))

    def test_bug3_malformed_members_and_thread_spawn_types_do_not_crash(self) -> None:
        """Non-dict entries in 'members' or non-dict 'thread_spawn' should not raise AttributeError/TypeError."""
        members: dict = {}
        st._ingest_members_json_text('{"members":["x"]}', members)
        st._ingest_members_json_text('{"members":5}', members)

        with tempfile.TemporaryDirectory() as tmpdir:
            p_codex = Path(tmpdir) / "rollout-bad-spawn.jsonl"
            p_codex.write_text(
                json.dumps({
                    "type": "session_meta",
                    "payload": {"id": "cid-2", "source": {"subagent": {"thread_spawn": "not-a-dict"}}},
                })
                + "\n",
                encoding="utf-8",
            )
            self.assertIsNotNone(st._parse_codex_rollout(p_codex))

    def test_bug4_sqlite_missing_table_or_corrupt_db_handled_gracefully(self) -> None:
        """Missing table (OperationalError) or corrupt file (DatabaseError) in conversation_summaries.db should return []."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversation_summaries.db"
            # Empty SQLite DB with no conversation_summaries table
            sqlite3.connect(str(db_path)).close()
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_DATA_DIR": tmpdir}):
                self.assertEqual(st.discover_agy_sessions(), [])

            # Garbage non-SQLite bytes
            db_path.write_bytes(b"not a valid sqlite database file")
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_DATA_DIR": tmpdir}):
                self.assertEqual(st.discover_agy_sessions(), [])

    def test_bug5_antigravity_data_dir_with_uri_special_chars(self) -> None:
        """ANTIGRAVITY_DATA_DIR containing '#', '?' or '%20' should not break sqlite3 URI connection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            special_dir = Path(tmpdir) / "agy#hash%20dir"
            special_dir.mkdir(parents=True, exist_ok=True)
            db_path = special_dir / "conversation_summaries.db"
            con = sqlite3.connect(str(db_path))
            con.execute(
                """
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    workspace_uris TEXT,
                    status TEXT,
                    last_modified_time REAL,
                    parent_conversation_id TEXT,
                    agent_name TEXT,
                    killed INTEGER DEFAULT 0
                )
                """
            )
            con.executemany(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("root-1", "URI Test", "", "DONE", 100.0, "", "", 0),
                    ("child-1", "", "", "DONE", 110.0, "root-1", "worker-1", 0),
                ],
            )
            con.commit()
            con.close()

            with mock.patch.dict(os.environ, {"ANTIGRAVITY_DATA_DIR": str(special_dir)}):
                reps = st.discover_agy_sessions()
                self.assertEqual(len(reps), 1)

    def test_bug6_codex_broken_symlink_rollout_does_not_crash(self) -> None:
        """A broken symlink matching rollout-*.jsonl should not crash discover_codex_sessions with FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            sess_dir = home / ".codex" / "sessions"
            sess_dir.mkdir(parents=True, exist_ok=True)
            broken_link = sess_dir / "rollout-broken.jsonl"
            broken_link.symlink_to(sess_dir / "nonexistent-target.jsonl")

            with mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(st.discover_codex_sessions(), [])

    def test_bug7_collect_sessions_negative_limit_does_not_slice_last_item(self) -> None:
        """collect_sessions(limit=-1) clamps limit>=1 rather than slicing reports[:-1]."""
        single = [
            st.SessionReport(host="agy", session_id="s1", title="S1", workspace="", updated_at=200.0, agents=[]),
        ]
        with mock.patch.object(st, "discover_agy_sessions", return_value=single):
            res = st.collect_sessions(host="agy", limit=-1)
            self.assertEqual([r.session_id for r in res], ["s1"])

        triple = [
            st.SessionReport(host="agy", session_id="s1", title="S1", workspace="", updated_at=300.0, agents=[]),
            st.SessionReport(host="agy", session_id="s2", title="S2", workspace="", updated_at=200.0, agents=[]),
            st.SessionReport(host="agy", session_id="s3", title="S3", workspace="", updated_at=100.0, agents=[]),
        ]
        with mock.patch.object(st, "discover_agy_sessions", return_value=triple):
            res3 = st.collect_sessions(host="agy", limit=-1)
            self.assertEqual([r.session_id for r in res3], ["s1"])

    def test_coordinator_slices_pre_swarm_history(self) -> None:
        """Pre-Swarm turns in a long parent conversation are excluded from coordinator by default, included with full_session=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agy_dir = Path(tmpdir)
            db_path = agy_dir / "conversation_summaries.db"
            brain_dir = agy_dir / "brain"
            con = sqlite3.connect(str(db_path))
            con.execute(
                """
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    workspace_uris TEXT,
                    status TEXT,
                    last_modified_time REAL,
                    parent_conversation_id TEXT,
                    agent_name TEXT,
                    killed INTEGER DEFAULT 0
                )
                """
            )
            con.executemany(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("long-root", "Long Chat", "", "RUNNING", 200.0, "", "", 0),
                    ("swarm-w1", "", "", "DONE", 210.0, "long-root", "verifier", 1),
                ],
            )
            con.commit()
            con.close()

            root_tp = brain_dir / "long-root" / ".system_generated" / "logs" / "transcript.jsonl"
            root_tp.parent.mkdir(parents=True, exist_ok=True)
            root_tp.write_text(
                "\n".join(
                    json.dumps(x)
                    for x in [
                        # Early read-only swarm(models) + pre-swarm unrelated turn (5,000,000 tokens!)
                        {
                            "step_index": 5,
                            "type": "PLANNER_RESPONSE",
                            "input_tokens": 100_000,
                            "output_tokens": 1_000,
                            "tool_calls": [{"name": "swarm", "args": {"action": '"models"'}}],
                        },
                        {"step_index": 10, "type": "PLANNER_RESPONSE", "input_tokens": 5_000_000, "output_tokens": 90_000},
                        # User requests Swarm at step 48 -> quota preflight at 49 -> swarm(add) at 50
                        {"step_index": 48, "type": "USER_INPUT", "content": "run swarm"},
                        {
                            "step_index": 49,
                            "type": "PLANNER_RESPONSE",
                            "input_tokens": 15_000,
                            "output_tokens": 500,
                            "tool_calls": [{"name": "swarm", "args": {"action": '"models"'}}],
                        },
                        {
                            "step_index": 50,
                            "type": "PLANNER_RESPONSE",
                            "input_tokens": 40_000,
                            "output_tokens": 2_000,
                            "tool_calls": [{"name": "swarm", "args": {"action": '"add"', "name": '"verifier"', "model": '"claude-sonnet-5-5-high"'}}],
                        },
                        {"step_index": 55, "type": "PLANNER_RESPONSE", "input_tokens": 20_000, "output_tokens": 1_000},
                        # Next user turn after all swarm workers were killed -> excluded by default
                        {"step_index": 80, "type": "USER_INPUT", "content": "unrelated follow-up"},
                        {"step_index": 85, "type": "PLANNER_RESPONSE", "input_tokens": 900_000, "output_tokens": 10_000},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            w_tp = brain_dir / "swarm-w1" / ".system_generated" / "logs" / "transcript.jsonl"
            w_tp.parent.mkdir(parents=True, exist_ok=True)
            w_tp.write_text(
                json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "input_tokens": 30_000, "output_tokens": 3_000}) + "\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"ANTIGRAVITY_DATA_DIR": str(agy_dir)}):
                sliced = st.collect_sessions(host="agy", target_id="long-root")[0]
                coord = sliced.agents[0]
                self.assertEqual(coord.turns, 3)  # steps 49, 50, 55
                self.assertEqual(coord.input_tokens, 75_000)

                full = st.collect_sessions(host="agy", target_id="long-root", full_session=True)[0]
                self.assertEqual(full.agents[0].turns, 6)
                self.assertEqual(full.agents[0].input_tokens, 6_075_000)

    def test_codex_bridge_graceful_fallback_when_codex_missing(self) -> None:
        """When `codex` is NOT installed, codex_bridge.py exits 0 cleanly without crashing."""
        import io
        import contextlib

        bridge_path = Path(__file__).resolve().parent.parent / "skills" / "swarm-mix" / "scripts" / "codex_bridge.py"
        spec = importlib.util.spec_from_file_location("codex_bridge", bridge_path)
        assert spec and spec.loader
        cb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cb)

        with mock.patch.dict(os.environ, {"SWARM_MIX_CODEX_BIN": "/nonexistent/codex-bin", "PATH": ""}, clear=False):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cb.main(["--probe"])
            self.assertEqual(rc, 0)
            info = json.loads(buf.getvalue().strip())
            self.assertFalse(info["available"])
            self.assertEqual(info["reason"], "codex_not_installed")

            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                rc2 = cb.main(["--mode", "review", "check diff"])
            self.assertEqual(rc2, 0)
            self.assertIn("[CODEX_BRIDGE_UNAVAILABLE]", buf2.getvalue())

    def test_codex_bridge_execution_and_agy_token_correlation(self) -> None:
        """Mock `codex` CLI emits --json events -> codex_bridge.py emits [CODEX_BRIDGE_USAGE] -> swarm_tokens correlates GPT usage."""
        import io
        import contextlib

        bridge_path = Path(__file__).resolve().parent.parent / "skills" / "swarm-mix" / "scripts" / "codex_bridge.py"
        spec = importlib.util.spec_from_file_location("codex_bridge", bridge_path)
        assert spec and spec.loader
        cb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cb)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            codex_home = tmp / "codex_home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('model = "gpt-6-astra"\n', encoding="utf-8")

            fake_bin = tmp / "fake-codex"
            fake_bin.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "login" ]; then echo "Logged in using ChatGPT"; exit 0; fi\n'
                'echo \'{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"LGTM: 0 blocking bugs"}}\'\n'
                'echo \'{"type":"turn.completed","usage":{"input_tokens":30000,"cached_input_tokens":20000,"cache_write_input_tokens":0,"output_tokens":450}}\'\n',
                encoding="utf-8",
            )
            fake_bin.chmod(0o755)

            with mock.patch.dict(os.environ, {"SWARM_MIX_CODEX_BIN": str(fake_bin), "CODEX_HOME": str(codex_home)}):
                pbuf = io.StringIO()
                with contextlib.redirect_stdout(pbuf):
                    cb.main(["--probe"])
                pinfo = json.loads(pbuf.getvalue().strip())
                self.assertTrue(pinfo["available"])
                self.assertEqual(pinfo["model"], "gpt-6-astra")

                ebuf = io.StringIO()
                with contextlib.redirect_stdout(ebuf):
                    rc = cb.main(["--mode", "review", "--cwd", str(tmp), "--effort", "medium", "check edge cases"])
                self.assertEqual(rc, 0)
                tool_stdout = ebuf.getvalue()
                self.assertIn("LGTM: 0 blocking bugs", tool_stdout)
                self.assertIn("[CODEX_BRIDGE_USAGE]", tool_stdout)

            # Verify Antigravity transcript correlation in swarm_tokens.py
            agy_dir = tmp / "agy"
            brain_dir = agy_dir / "brain"
            brain_dir.mkdir(parents=True)
            db_path = agy_dir / "conversation_summaries.db"
            con = sqlite3.connect(str(db_path))
            con.execute(
                "CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, workspace_uris TEXT, status TEXT, parent_conversation_id TEXT, agent_name TEXT, killed INTEGER, last_modified_time INTEGER)"
            )
            con.execute("INSERT INTO conversation_summaries VALUES ('root-cb', 'GPT Swarm', '', 'done', '', '', 0, 100)")
            con.execute("INSERT INTO conversation_summaries VALUES ('w-cb', 'verifier', '', 'done', 'root-cb', 'verifier', 1, 101)")
            con.commit()
            con.close()

            p_tp = brain_dir / "root-cb" / ".system_generated" / "logs" / "transcript.jsonl"
            p_tp.parent.mkdir(parents=True)
            p_tp.write_text(
                json.dumps({
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "input_tokens": 12000,
                    "output_tokens": 600,
                    "tool_calls": [{"name": "swarm", "args": {"action": '"add"', "name": '"verifier"', "model": '"gemini-4-hydrogen-medium"'}}],
                })
                + "\n",
                encoding="utf-8",
            )
            w_tp = brain_dir / "w-cb" / ".system_generated" / "logs" / "transcript.jsonl"
            w_tp.parent.mkdir(parents=True)
            w_tp.write_text(
                "\n".join([
                    json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "input_tokens": 4000, "output_tokens": 200}),
                    json.dumps({"step_index": 2, "type": "GENERIC", "content": tool_stdout}),
                ])
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"ANTIGRAVITY_DATA_DIR": str(agy_dir)}):
                rep = st.collect_sessions(host="agy", target_id="root-cb")[0]
                by_name = {a.name: a for a in rep.agents}
                self.assertIn("verifier", by_name)
                self.assertIn("verifier[codex]", by_name)
                self.assertEqual(by_name["verifier"].model, "gemini-4-hydrogen-medium")
                self.assertEqual(by_name["verifier[codex]"].model, "gpt-6-astra (medium)")
                self.assertEqual(by_name["verifier[codex]"].input_tokens, 10000)  # 30000 - 20000 cached
                self.assertEqual(by_name["verifier[codex]"].cache_read_tokens, 20000)
                self.assertEqual(by_name["verifier[codex]"].output_tokens, 450)

    def test_backgrounded_system_message_and_monitor_role_guard(self) -> None:
        """
        Regression test:
        1. Backgrounded codex_bridge.py runs arrive via SYSTEM_MESSAGE (often middle-truncated in transcript.jsonl)
           plus `.system_generated/tasks/task-XX.log`; must recover exact GPT tokens with zero double-counting,
           and ignore read_board GENERIC messages quoting `[CODEX_BRIDGE_USAGE]`.
        2. `codex_bridge.py --role monitor` / `--role scout` automatically downgrades to L3 `gpt-6-luna (low)`
           instead of inheriting `config.toml`'s flagship `gpt-6-astra`.
        """
        import io
        import contextlib

        bridge_path = Path(__file__).resolve().parent.parent / "skills" / "swarm-mix" / "scripts" / "codex_bridge.py"
        spec = importlib.util.spec_from_file_location("codex_bridge", bridge_path)
        assert spec and spec.loader
        cb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cb)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            codex_home = tmp / "codex_home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('model = "gpt-6-astra"\n', encoding="utf-8")
            (codex_home / "native-router-models.json").write_text(
                json.dumps({"models": [{"slug": "gpt-6-astra", "priority": 1}, {"slug": "gpt-6-luna", "priority": 3}]}),
                encoding="utf-8",
            )

            fake_bin = tmp / "fake-codex"
            fake_bin.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "login" ]; then echo "Logged in"; exit 0; fi\n'
                'echo \'{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"All clear"}}\'\n'
                'echo \'{"type":"turn.completed","usage":{"input_tokens":5000,"cached_input_tokens":4000,"output_tokens":80}}\'\n',
                encoding="utf-8",
            )
            fake_bin.chmod(0o755)

            with mock.patch.dict(os.environ, {"SWARM_MIX_CODEX_BIN": str(fake_bin), "CODEX_HOME": str(codex_home)}):
                mbuf = io.StringIO()
                with contextlib.redirect_stdout(mbuf):
                    rc = cb.main(["--role", "monitor", "--cwd", str(tmp), "check board"])
                self.assertEqual(rc, 0)
                self.assertIn('"model": "gpt-6-luna (low)"', mbuf.getvalue())
                self.assertNotIn("gpt-6-astra", mbuf.getvalue())

            # Simulate truncated SYSTEM_MESSAGE + untruncated task-27.log + monitor read_board quote
            w_sys = tmp / "brain" / "audit-w" / ".system_generated"
            (w_sys / "logs").mkdir(parents=True)
            (w_sys / "tasks").mkdir(parents=True)
            (w_sys / "tasks" / "task-27.log").write_text(
                'Audit complete.\n\n[CODEX_BRIDGE_USAGE] {"model": "gpt-6-astra (high)", "turns": 1, "input_tokens": 115000, "cache_read_tokens": 900000, "output_tokens": 7500}\n',
                encoding="utf-8",
            )
            (w_sys / "tasks" / "task-28.log").write_text(
                'Second pass.\n\n[CODEX_BRIDGE_USAGE] {"model": "gpt-6-astra (high)", "turns": 1, "input_tokens": 85000, "cache_read_tokens": 600000, "output_tokens": 2500}\n',
                encoding="utf-8",
            )
            w_tp = w_sys / "logs" / "transcript.jsonl"
            w_tp.write_text(
                "\n".join([
                    # Truncated SYSTEM_MESSAGE referencing task-27.log (must read task-27.log once, not twice!)
                    json.dumps({
                        "step_index": 35,
                        "type": "SYSTEM_MESSAGE",
                        "truncated_fields": ["content"],
                        "content": "<SYSTEM_MESSAGE>\n...ens\": 900000, \"output_tokens\": 7500}\nLog: file:///tmp/tasks/task-27.log\n</SYSTEM_MESSAGE>",
                    }),
                    # Untruncated SYSTEM_MESSAGE referencing task-28.log (must deduplicate with task-28.log!)
                    json.dumps({
                        "step_index": 40,
                        "type": "SYSTEM_MESSAGE",
                        "content": '<SYSTEM_MESSAGE>\n[CODEX_BRIDGE_USAGE] {"model": "gpt-6-astra (high)", "turns": 1, "input_tokens": 85000, "cache_read_tokens": 600000, "output_tokens": 2500}\nLog: file:///tmp/tasks/task-28.log\n</SYSTEM_MESSAGE>',
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            usages = st._extract_codex_bridge_usages(w_tp)
            self.assertEqual(usages, {"gpt-6-astra (high)": (2, 200000, 1500000, 10000)})

            # Monitor transcript reading board message that quotes [CODEX_BRIDGE_USAGE] must stay empty
            m_tp = tmp / "brain" / "mon-w" / ".system_generated" / "logs" / "transcript.jsonl"
            m_tp.parent.mkdir(parents=True)
            m_tp.write_text(
                json.dumps({
                    "step_index": 10,
                    "type": "GENERIC",
                    "content": '{"head":50,"messages":[{"seq":49,"agent":"audit-gpt","text":"Done\\n[CODEX_BRIDGE_USAGE] {\\"model\\": \\"gpt-6-astra (high)\\", \\"turns\\": 1, \\"input_tokens\\": 99999, \\"cache_read_tokens\\": 0, \\"output_tokens\\": 500}"}]}',
                }) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(st._extract_codex_bridge_usages(m_tp), {})


if __name__ == "__main__":
    unittest.main()
