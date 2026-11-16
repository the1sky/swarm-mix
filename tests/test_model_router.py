import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTER_SCRIPT = REPO_ROOT / "skills" / "swarm-mix" / "scripts" / "model_router.py"

spec = importlib.util.spec_from_file_location("model_router", ROUTER_SCRIPT)
mr = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mr)


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class TestModelRouterStandaloneAndAdaptive(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state_dir = self.root / "state"
        self.agy_dir = self.root / "agy"
        self.codex_dir = self.root / "codex"
        self.claude_dir = self.root / "claude"
        for d in (self.state_dir, self.agy_dir, self.codex_dir, self.claude_dir):
            d.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _base_env(self, **extra: str) -> dict:
        env = {
            "SWARM_MIX_STATE_DIR": str(self.state_dir),
            "ANTIGRAVITY_DATA_DIR": str(self.agy_dir),
            "CODEX_HOME": str(self.codex_dir),
            "CLAUDE_CONFIG_DIR": str(self.claude_dir),
            "SWARM_MIX_AGY_BIN": "none",
            "SWARM_MIX_CODEX_BIN": "none",
            "SWARM_MIX_CLAUDE_BIN": "none",
            "OPENAI_API_KEY": "",
        }
        env.update(extra)
        return env

    def test_standalone_antigravity_zero_external_clis(self) -> None:
        with patch.dict(os.environ, self._base_env(), clear=False):
            plan = mr.compute_routing_plan(host="agy")
            self.assertEqual(plan["host"], "agy")
            self.assertEqual(plan["pools"]["codex"], "codex_not_installed")
            self.assertEqual(plan["pools"]["claude_cli"], "claude_not_installed")

            recs = plan["recommendations"]
            self.assertTrue(all(r["channel"] == "native" for r in recs.values()))
            self.assertEqual(recs["architecture"]["recommended_model"], "claude-opus-5-5-high")
            self.assertEqual(recs["frontend_ui"]["recommended_model"], "gemini-4-carbon-medium")
            self.assertEqual(recs["backend_refactor"]["recommended_model"], "claude-sonnet-5-5-medium")
            self.assertEqual(recs["cross_verify"]["recommended_model"], "claude-opus-5-5-medium")
            self.assertEqual(recs["scout_daemon"]["recommended_model"], "gemini-4-hydrogen-medium")

            # CLI exit code must be 0
            rc = mr.main(["--host", "agy", "-m"])
            self.assertEqual(rc, 0)

    def test_standalone_claude_code_zero_external_clis(self) -> None:
        with patch.dict(os.environ, self._base_env(), clear=False):
            plan = mr.compute_routing_plan(host="claude")
            self.assertEqual(plan["host"], "claude")
            self.assertEqual(plan["pools"]["codex"], "codex_not_installed")
            self.assertEqual(plan["pools"]["agy_cli"], "agy_not_installed")

            recs = plan["recommendations"]
            self.assertTrue(all(r["channel"] == "native" for r in recs.values()))
            self.assertEqual(recs["architecture"]["recommended_model"], "opus (high)")
            self.assertEqual(recs["backend_refactor"]["recommended_model"], "sonnet (medium)")
            self.assertEqual(recs["scout_daemon"]["recommended_model"], "haiku (medium)")

            rc = mr.main(["--host", "claude", "-m"])
            self.assertEqual(rc, 0)

    def test_standalone_codex_and_unknown_future_model_inheritance(self) -> None:
        (self.codex_dir / "config.toml").write_text(
            'model = "gpt-7-zenith"\nmodel_catalog_json = "' + str(self.codex_dir / "catalog.json") + '"\n',
            encoding="utf-8",
        )
        catalog = {
            "models": [
                {
                    "slug": "gpt-7-zenith",
                    "display_name": "GPT-7 Zenith",
                    "priority": 0,
                    "context_window": 1000000,
                    "input_modalities": ["text", "image"],
                    "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "high"}],
                },
                {
                    "slug": "gpt-6-luna",
                    "display_name": "GPT-6 Luna",
                    "priority": 3,
                    "context_window": 400000,
                    "input_modalities": ["text"],
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
                },
                {
                    "slug": "lan-box/unverified-local-8b",
                    "display_name": "LAN Box 8B",
                    "priority": 0,
                    "context_window": 128000,
                    "input_modalities": ["text"],
                    "supported_reasoning_levels": [{"effort": "high"}],
                },
            ]
        }
        (self.codex_dir / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")

        with patch.dict(os.environ, self._base_env(), clear=False):
            plan = mr.compute_routing_plan(host="codex")
            self.assertEqual(plan["host"], "codex")
            self.assertEqual(plan["pools"]["claude_cli"], "claude_not_installed")
            self.assertEqual(plan["pools"]["agy_cli"], "agy_not_installed")

            zenith_cards = [m for m in plan["models"] if m["base_id"] == "gpt-7-zenith"]
            self.assertTrue(len(zenith_cards) >= 1)
            self.assertEqual(zenith_cards[0]["prior_source"], "inherited:openai:l1")
            self.assertEqual(zenith_cards[0]["context_window"], 1000000)

            recs = plan["recommendations"]
            self.assertEqual(recs["architecture"]["recommended_model"], "gpt-7-zenith (high)")
            self.assertEqual(recs["scout_daemon"]["recommended_model"], "gpt-6-luna (medium)")
            self.assertNotIn("lan-box", recs["cross_verify"]["recommended_model"])

    def test_multi_vendor_low_quota_shifts_cross_verify_to_codex_bridge(self) -> None:
        fake_codex = _write_exec(
            self.root / "fake_codex",
            "#!/bin/sh\nif [ \"$1\" = \"login\" ]; then echo 'Logged in using ChatGPT'; exit 0; fi\nexit 0\n",
        )
        (self.codex_dir / "native-router-models.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-6-astra",
                            "priority": 1,
                            "context_window": 400000,
                            "input_modalities": ["text", "image"],
                            "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "high"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        agy_json = json.dumps(
            {
                "models": ["gemini-4-carbon-high", "gemini-4-hydrogen-medium", "claude-opus-5-5-medium", "claude-sonnet-5-5-high"],
                "quotas": {
                    "gemini-4-carbon-high": {"quota_remaining": "94%"},
                    "gemini-4-hydrogen-medium": {"quota_remaining": "94%"},
                    "claude-opus-5-5-medium": {"quota_remaining": "9%"},
                    "claude-sonnet-5-5-high": {"quota_remaining": "0%", "quota_note": ""},
                },
            }
        )

        with patch.dict(os.environ, self._base_env(SWARM_MIX_CODEX_BIN=str(fake_codex)), clear=False):
            plan = mr.compute_routing_plan(host="agy", agy_models_json=agy_json)
            recs = plan["recommendations"]
            # Low Claude quota (9% < 15%) shifts cross_verify against Google author onto OpenAI Codex bridge
            self.assertEqual(recs["cross_verify"]["recommended_model"], "gpt-6-astra (medium)")
            self.assertEqual(recs["cross_verify"]["channel"], "codex-bridge")
            # Exhausted sonnet (0% without credits) must be marked unavailable
            sonnet = next(m for m in plan["models"] if m["id"] == "claude-sonnet-5-5-high")
            self.assertFalse(sonnet["available"])

            # AI Credits note restores full availability even at 0%
            agy_credits_json = json.dumps(
                {
                    "models": ["claude-opus-5-5-medium"],
                    "quotas": {"claude-opus-5-5-medium": {"quota_remaining": "0%", "quota_note": "Using AI Credits"}},
                }
            )
            plan_cred = mr.compute_routing_plan(host="agy", agy_models_json=agy_credits_json)
            opus = next(m for m in plan_cred["models"] if m["id"] == "claude-opus-5-5-medium")
            self.assertTrue(opus["available"])
            self.assertEqual(opus["quota_pct"], 1.0)

    def test_layer3_passive_harvest_idempotency_and_bayesian_bounds(self) -> None:
        swarm_dir = self.agy_dir / "brain" / "conv-1234" / ".system_generated" / "swarm"
        swarm_dir.mkdir(parents=True, exist_ok=True)
        (swarm_dir / "members.json").write_text(
            json.dumps(
                {
                    "members": {
                        "coordinator": {"role": "coordinator", "model": "gemini-4-carbon-high"},
                        "core-author": {"role": "worker", "model": "gemini-4-argon-high", "killed": True},
                        "verifier": {"role": "worker", "model": "claude-opus-5-5-medium", "killed": True},
                    }
                }
            ),
            encoding="utf-8",
        )
        (swarm_dir / "board.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"seq": 1, "agent": "core-author", "text": "Implementation complete."}),
                    json.dumps({"seq": 2, "agent": "verifier", "text": "Found 1 blocking bug in line 42, needs fix."}),
                    json.dumps({"seq": 3, "agent": "core-author", "text": "Fixed line 42."}),
                    json.dumps({"seq": 4, "agent": "verifier", "text": "0 blocking issues, LGTM!"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, self._base_env(), clear=False):
            s1 = mr.sync_agy_swarm_outcomes(force=True)
            s2 = mr.sync_agy_swarm_outcomes(force=True)
            self.assertEqual(s1["models"]["gemini-4-argon"]["author_tasks"], 1)
            self.assertEqual(s1["models"]["gemini-4-argon"]["author_first_pass"], 0)
            self.assertEqual(s1["models"]["claude-opus-5-5"]["verifier_reviews"], 1)
            self.assertEqual(s1["models"]["claude-opus-5-5"]["verifier_bug_catches"], 1)
            # Idempotent on second sync
            self.assertEqual(s2["models"]["gemini-4-argon"]["author_tasks"], 1)

            # Record 10 explicit outcomes to trigger Bayesian adaptation (n >= 3) & test +-0.15 drift clamp
            for _ in range(10):
                mr.record_explicit_outcome(
                    author_model="gemini-4-argon-high",
                    first_pass=False,
                    verifier_model="claude-opus-5-5-medium",
                    caught_bug=True,
                )

            plan = mr.compute_routing_plan(host="agy")
            argon = next(m for m in plan["models"] if m["base_id"] == "gemini-4-argon")
            opus = next(m for m in plan["models"] if m["base_id"] == "claude-opus-5-5")
            self.assertLess(argon["empirical"]["author_shift"], 0.0)
            self.assertGreaterEqual(argon["empirical"]["author_shift"], -0.15)
            self.assertGreater(opus["empirical"]["verify_shift"], 0.0)
            self.assertLessEqual(opus["empirical"]["verify_shift"], 0.15)


if __name__ == "__main__":
    unittest.main()
