from __future__ import annotations

import unittest
from unittest.mock import patch

from agentfix import catalog, engine


class TargetedRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = {
            "agents": {
                "alpha": {"name": "Alpha", "bin": ["alpha"], "config": "", "npm_pkg": "pkg-alpha"},
                "beta": {"name": "Beta", "bin": ["beta"], "config": "", "npm_pkg": "pkg-beta"},
            },
            "issues": [],
        }
        self.issue = {
            "id": "targeted",
            "title": "Targeted repair",
            "agents": ["all"],
            "checks": [{"name": "{name} check", "cmd": "{bin} check"}],
            "fixes": [{"name": "{name} fix", "cmd": "{bin} fix"}],
            "verify": [{"name": "{name} verify", "cmd": "{bin} verify"}],
        }
        self.alpha = {"id": "alpha", **self.catalog["agents"]["alpha"], "exe": "/bin/alpha"}

    def result(self, cmd: str) -> dict:
        return {"ok": True, "exit": 0, "stdout": cmd, "stderr": "", "duration": 0.01}

    def test_check_runs_only_target_command(self) -> None:
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return self.result(cmd)

        with patch.object(engine, "run", side_effect=fake_run):
            state = engine.check_issue(self.issue, agent=self.alpha, quiet=True)

        self.assertFalse(state["broken"])
        self.assertEqual(commands, ["alpha check"])
        self.assertTrue(all("beta" not in cmd for cmd in commands))

    def test_apply_and_verify_use_same_target(self) -> None:
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return self.result(cmd)

        with patch.object(engine, "run", side_effect=fake_run):
            outcome = engine.apply_issue(self.issue, agent=self.alpha, yes=True, quiet=True)

        self.assertTrue(outcome["verified"])
        self.assertEqual(commands, ["alpha fix", "alpha verify"])
        self.assertTrue(all("beta" not in cmd for cmd in commands))

    def test_target_resolution_does_not_bulk_detect(self) -> None:
        with patch.object(catalog, "detect_agents", side_effect=AssertionError("bulk detection used")), patch.object(
            catalog, "detect_agent", return_value=self.alpha
        ) as detect_one:
            target = engine.resolve_target(self.catalog, self.issue, "alpha")

        self.assertEqual(target["id"], "alpha")
        detect_one.assert_called_once_with(self.catalog, "alpha")

    def test_missing_unknown_and_undetected_targets_fail(self) -> None:
        with self.assertRaises(engine.TargetError):
            engine.resolve_target(self.catalog, self.issue, "")
        with self.assertRaises(engine.TargetError):
            engine.resolve_target(self.catalog, self.issue, "unknown")
        with patch.object(catalog, "detect_agent", return_value=None):
            with self.assertRaises(engine.TargetError):
                engine.resolve_target(self.catalog, self.issue, "alpha")

    def test_issue_agent_scope_is_enforced(self) -> None:
        scoped = {**self.issue, "agents": ["alpha"]}
        with self.assertRaises(engine.TargetError):
            engine.resolve_target(self.catalog, scoped, "beta")

    def test_empty_agent_never_reports_healthy_or_verified(self) -> None:
        with self.assertRaises(engine.TargetError):
            engine.check_issue(self.issue, agent={}, quiet=True)
        with self.assertRaises(engine.TargetError):
            engine.apply_issue(self.issue, agent={}, yes=True, quiet=True)

    def test_per_step_agent_scope_skips_unrelated_actions(self) -> None:
        issue = {
            **self.issue,
            "checks": [
                {"name": "alpha only", "cmd": "alpha-only", "agents": ["alpha"]},
                {"name": "beta only", "cmd": "beta-only", "agents": ["beta"]},
            ],
        }
        commands = []
        with patch.object(engine, "run", side_effect=lambda cmd, **kwargs: commands.append(cmd) or self.result(cmd)):
            engine.check_issue(issue, agent=self.alpha, quiet=True)
        self.assertEqual(commands, ["alpha-only"])


if __name__ == "__main__":
    unittest.main()
