from __future__ import annotations

import contextlib
import io
import json
import subprocess
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from agentfix import cli, engine, mcp
from agentfix import catalog as cat
from agentfix import report as rep
from agentfix import result as operation_result


class ResultTruthTests(unittest.TestCase):
    def test_line_adapter_status_is_explicit_not_text_inferred(self) -> None:
        result = operation_result.from_lines(
            "mcp_register", ["error-looking but explicitly ok"], status="ok"
        )
        self.assertTrue(result.ok)
        with self.assertRaises(ValueError):
            operation_result.from_lines("mcp_register", ["x"], status="healthy")

    def test_command_only_target_is_probed_once(self) -> None:
        issue = {"id": "x", "agents": ["ghost"]}
        detected = {"id": "ghost", "name": "ghost", "bin": ["ghost"], "exe": "ghost"}
        with patch.object(cat, "find_agent", return_value=None), patch.object(
            cat, "_probe_agent", return_value=detected
        ) as probe:
            resolved = engine.resolve_target({"agents": {}}, issue, "ghost")
        self.assertEqual(resolved, detected)
        probe.assert_called_once()

    def test_provider_keys_are_agent_owned_and_not_global_union(self) -> None:
        self.assertEqual(cat.provider_keys({"provider_env": ["ONLY_THIS"]}), ["ONLY_THIS"])
        catalog = cat.load_catalog()
        self.assertEqual(catalog["agents"]["kimi-code"].get("provider_env", []), [])
        self.assertNotIn("ANTHROPIC_BASE_URL", catalog["agents"]["claude-code"]["provider_env"])

    def test_secret_mask_covers_auth_headers_jwt_and_url_values(self) -> None:
        raw = (
            "Authorization: Bearer bearer-secret-123456 "
            "https://user:password@example.com/v1 "
            "https://example.com/v1?api_key=query-secret "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature123"
        )
        masked = rep.mask_secrets(raw)
        for secret in (
            "bearer-secret-123456",
            "password",
            "query-secret",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature123",
        ):
            self.assertNotIn(secret, masked)

    def test_catalog_checks_do_not_hide_nonzero_exit_codes(self) -> None:
        catalog = cat.load_catalog()
        commands = [
            step.get("cmd", "")
            for issue in catalog["issues"]
            for step in issue.get("checks", [])
        ]
        self.assertTrue(commands)
        self.assertFalse(any("|| true" in command for command in commands))

    def test_auth_and_provider_verification_is_structured_env_check(self) -> None:
        catalog = cat.load_catalog()
        for issue_id in ("agent-auth-broken", "provider-config"):
            issue = cat.find_issue(catalog, issue_id)
            self.assertEqual(issue["checks"][0].get("kind"), "env_keys")
            for verify in issue["verify"]:
                self.assertEqual(verify.get("kind"), "env_keys")

    def test_node_verification_consumes_agent_requirement(self) -> None:
        issue = cat.find_issue(cat.load_catalog(), "node-version-too-old")
        verify = issue["verify"][0]
        self.assertEqual(verify.get("kind"), "node_requirement")
        self.assertNotIn("cmd", verify)

    def test_dsh_verification_includes_web_boot_smoke(self) -> None:
        issue = cat.find_issue(cat.load_catalog(), "deepseek-harness-broken")
        self.assertTrue(any("dsh web" in step.get("cmd", "") for step in issue["verify"]))

    def test_opencode_catalog_commands_preserve_producer_status(self) -> None:
        issue = cat.find_issue(cat.load_catalog(), "opencode-mcp-schema")
        commands = [step.get("cmd", "") for step in issue["checks"] + issue["verify"]]
        self.assertTrue(all("| head" not in cmd and "| tail" not in cmd for cmd in commands))
        self.assertTrue(all("grep" not in cmd for cmd in commands))
        self.assertTrue(all("opencode" in cmd or "node -e" in cmd for cmd in commands))

    def test_node_requirement_is_agent_owned_and_uses_minimax_range(self) -> None:
        issue = {
            "id": "node",
            "title": "Node",
            "checks": [{"name": "node", "kind": "node_requirement"}],
        }
        agent = cat.find_agent(cat.load_catalog(), "minimax-code")
        with patch.object(engine, "run", return_value={"ok": True, "stdout": "22.19.0\n"}):
            passed = engine.check_issue(issue, agent, quiet=True)
        self.assertEqual(passed["status"], "PASS")
        with patch.object(engine, "run", return_value={"ok": True, "stdout": "23.0.0\n"}):
            failed = engine.check_issue(issue, agent, quiet=True)
        self.assertEqual(failed["status"], "FAIL")
        with patch.object(engine, "run", return_value={"ok": False, "stdout": "", "stderr": "missing"}):
            unknown = engine.check_issue(issue, agent, quiet=True)
        self.assertEqual(unknown["status"], "INCONCLUSIVE")

    def test_dsh_check_includes_web_boot_smoke(self) -> None:
        issue = cat.find_issue(cat.load_catalog(), "deepseek-harness-broken")
        self.assertTrue(any("dsh web --help" in step.get("cmd", "") for step in issue["checks"]))

    def test_missing_command_only_dsh_target_still_reaches_diagnostic_checks(self) -> None:
        catalog = cat.load_catalog()
        issue = cat.find_issue(catalog, "deepseek-harness-broken")
        with patch.object(engine.shutil, "which", return_value=None), patch.object(
            engine, "run", return_value={"ok": False, "exit": 127, "stdout": "", "stderr": "command not found"}
        ):
            state = engine.check_issue(issue, engine.resolve_target(catalog, issue, "dsh"), quiet=True)
        self.assertEqual(state["status"], "FAIL")

    def test_new_npm_agents_are_in_shared_registry_issue_scopes(self) -> None:
        catalog = cat.load_catalog()
        for issue_id in ("npm-postinstall-skipped", "node-version-too-old", "npm-registry-mirror"):
            targets = set(cat.find_issue(catalog, issue_id)["agents"])
            self.assertTrue({"kimi-code", "minimax-code"}.issubset(targets), issue_id)
        net_targets = set(cat.find_issue(catalog, "net-connectivity")["agents"])
        self.assertIn("kimi-code", net_targets)
        self.assertNotIn("minimax-code", net_targets)

    def test_minimax_data_dir_override_owns_config_and_skills_paths(self) -> None:
        kimi = cat.find_agent(cat.load_catalog(), "kimi-code")
        minimax = cat.find_agent(cat.load_catalog(), "minimax-code")
        with patch.dict(cat.os.environ, {"MINIMAX_DATA_DIR": "/tmp/mcode-data"}, clear=False):
            self.assertEqual(cat.config_path(minimax), "/tmp/mcode-data")
            self.assertEqual(cat.skills_dir(minimax), "/tmp/mcode-data/skills")
        with patch.dict(
            cat.os.environ,
            {"MINIMAX_DATA_DIR": "", "MAVIS_DATA_DIR": "/tmp/mavis-data"},
            clear=False,
        ):
            self.assertEqual(cat.config_path(minimax), "/tmp/mavis-data")
            self.assertEqual(cat.skills_dir(minimax), "/tmp/mavis-data/skills")
        with patch.dict(cat.os.environ, {"KIMI_CODE_HOME": "/tmp/kimi-home"}, clear=False):
            self.assertEqual(cat.config_path(kimi), "/tmp/kimi-home")
            self.assertEqual(cat.skills_dir(kimi), "/tmp/kimi-home/skills")

    def test_kimi_and_minimax_registry_contracts_are_explicit(self) -> None:
        catalog = cat.load_catalog()
        kimi = catalog["agents"]["kimi-code"]
        minimax = catalog["agents"]["minimax-code"]
        self.assertEqual(kimi["npm_pkg"], "@moonshot-ai/kimi-code")
        self.assertEqual(kimi["bin"], ["kimi"])
        self.assertEqual(kimi["node_requirement"], ">=22.19.0")
        self.assertEqual(minimax["npm_pkg"], "@minimax-ai/code")
        self.assertEqual(minimax["bin"], ["mcode"])
        self.assertIn("22.19", minimax["node_requirement"])
        self.assertEqual(minimax["config"], "~/.minimax")
        self.assertIn("mcpServers", Path("fixes/minimax-code.md").read_text(encoding="utf-8"))

    def test_shell_prefix_never_accepts_system32_bash_on_windows(self) -> None:
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        with patch.object(engine.cat, "is_windows", return_value=False):
            self.assertEqual(engine._shell_prefix(), ["/bin/sh", "-c"])
        with patch.object(engine.cat, "is_windows", return_value=True), patch.object(
            engine.os.path, "exists", side_effect=lambda path: path == git_bash
        ), patch.object(engine.shutil, "which", return_value=r"C:\Windows\System32\bash.exe"):
            self.assertEqual(engine._shell_prefix(), [git_bash, "-c"])
        with patch.object(engine.cat, "is_windows", return_value=True), patch.object(
            engine.os.path, "exists", return_value=False
        ), patch.object(engine.shutil, "which", return_value=r"C:\Windows\System32\bash.exe"):
            self.assertEqual(engine._shell_prefix(), ["cmd", "/c"])

    def test_command_only_dsh_target_is_in_public_registry(self) -> None:
        catalog = cat.load_catalog()
        self.assertIn("dsh", catalog["agents"])
        self.assertTrue(catalog["agents"]["dsh"]["command_only"])
        issue = cat.find_issue(catalog, "deepseek-harness-broken")
        self.assertEqual(issue["agents"], ["dsh"])

    def test_npm_postinstall_scope_is_explicit_npm_agents(self) -> None:
        issue = cat.find_issue(cat.load_catalog(), "npm-postinstall-skipped")
        self.assertNotIn("all", issue["agents"])
        catalog = cat.load_catalog()
        expected = {
            agent_id
            for agent_id, agent in catalog["agents"].items()
            if agent.get("npm_pkg") and not agent.get("command_only")
        }
        self.assertEqual(set(issue["agents"]), expected)

    def test_env_key_check_rejects_empty_or_unowned_credentials(self) -> None:
        issue = {
            "id": "auth",
            "title": "Auth",
            "checks": [{"name": "credential", "kind": "env_keys"}],
        }
        agent = {"id": "alpha", "bin": ["alpha"], "provider_env": ["ALPHA_TOKEN"]}
        with patch.dict(engine.os.environ, {"ALPHA_TOKEN": ""}, clear=False):
            with patch.object(engine, "run", side_effect=AssertionError("shell auth probe ran")):
                state = engine.check_issue(issue, agent, quiet=True)
        self.assertEqual(state["status"], "INCONCLUSIVE")
        self.assertEqual(state["results"][0]["status"], "INCONCLUSIVE")

    def test_nonempty_env_key_is_presence_only_not_authentication_verified(self) -> None:
        issue = {
            "id": "auth",
            "title": "Auth",
            "checks": [{"name": "credential", "kind": "env_keys"}],
        }
        agent = {"id": "alpha", "bin": ["alpha"], "provider_env": ["ALPHA_TOKEN"]}
        with patch.dict(engine.os.environ, {"ALPHA_TOKEN": "present"}, clear=False):
            state = engine.check_issue(issue, agent, quiet=True)
        self.assertEqual(state["status"], "INCONCLUSIVE")
        self.assertIn("validity is unverified", state["results"][0]["detail"])

    def test_mixed_pass_and_skip_is_inconclusive(self) -> None:
        issue = {
            "id": "mixed",
            "title": "Mixed",
            "checks": [
                {"name": "portable", "cmd": "fake"},
                {"name": "windows", "cmd": "fake", "platform": "windows"},
            ],
        }
        with patch.object(engine, "run", return_value=self.result()), patch.object(
            engine.cat, "is_windows", return_value=False
        ):
            state = engine.check_issue(issue, {"id": "alpha", "bin": ["alpha"]}, quiet=True)
        self.assertEqual(state["status"], "INCONCLUSIVE")
        self.assertFalse(state["broken"])

    def test_no_version_check_is_inconclusive_even_when_binary_exists(self) -> None:
        issue = {
            "id": "gui",
            "title": "GUI",
            "checks": [{"name": "version", "cmd": "{bin} --version"}],
        }
        with patch.object(engine.shutil, "which", return_value="gui.exe"):
            state = engine.check_issue(
                issue, {"id": "zcode", "bin": ["zcode"], "no_version": True}, quiet=True
            )
        self.assertEqual(state["status"], "INCONCLUSIVE")
        self.assertEqual(state["results"][0]["status"], "INCONCLUSIVE")

    def result(self, ok: bool = True, exit_code: int = 0, stdout: str = "", stderr: str = "") -> dict:
        return {
            "ok": ok,
            "exit": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration": 0.0,
        }

    def test_pass_matcher_honors_real_exit_even_with_negative_spec(self) -> None:
        result = self.result(ok=False, exit_code=2, stdout="permission denied")

        self.assertFalse(
            engine._passes({"stdout_not_contains": ["not found"]}, result)
        )
        self.assertFalse(engine._passes({"stdout_not_contains": ["not found"]}, result))

    def test_all_skipped_checks_are_inconclusive_not_healthy(self) -> None:
        issue = {
            "id": "platform-only",
            "title": "Platform only",
            "checks": [{"name": "windows", "cmd": "unused", "platform": "windows"}],
        }
        with patch.object(engine.cat, "is_windows", return_value=False):
            state = engine.check_issue(
                issue, {"id": "alpha", "bin": ["alpha"]}, quiet=True
            )

        self.assertFalse(state["broken"])
        self.assertEqual(state["status"], "INCONCLUSIVE")
        self.assertEqual(state["results"][0]["status"], "SKIPPED")

    def test_no_version_verify_is_not_authoritative_success(self) -> None:
        issue = {
            "id": "generic",
            "title": "Generic",
            "checks": [{"name": "binary", "cmd": "{bin} --version"}],
            "fixes": [{"name": "manual", "cmd": "echo manual", "platform": "other"}],
            "verify": [{"name": "binary", "cmd": "{bin} --version"}],
        }
        agent = {"id": "zcode", "name": "ZCode", "bin": ["zcode"], "no_version": True}

        with patch.object(engine, "run", side_effect=AssertionError("GUI version probe ran")):
            outcome = engine.apply_issue(issue, agent, yes=True, quiet=True)

        self.assertFalse(outcome["verified"])
        self.assertEqual(outcome["verification_status"], "inconclusive")

    def test_partial_fix_with_skipped_step_cannot_verify(self) -> None:
        issue = {
            "id": "partial",
            "title": "Partial",
            "checks": [{"name": "probe", "cmd": "fake"}],
            "fixes": [
                {"name": "auto", "cmd": "fake"},
                {"name": "unsupported", "cmd": "fake", "platform": "other"},
            ],
            "verify": [{"name": "verify", "cmd": "fake"}],
        }
        with patch.object(engine, "run", return_value=self.result()):
            outcome = engine.apply_issue(issue, {"id": "alpha", "bin": ["alpha"]}, yes=True, quiet=True)
        self.assertFalse(outcome["verified"])
        self.assertEqual(outcome["verification_status"], "inconclusive")

    def test_skipped_native_fix_cannot_become_verified_success(self) -> None:
        issue = {
            "id": "native-scope",
            "title": "Native",
            "checks": [{"name": "probe", "cmd": "fake"}],
            "fixes": [{"name": "npm-only", "cmd": "fake", "cwd": "npm_root/pkg"}],
            "verify": [{"name": "verify", "cmd": "fake"}],
        }
        with patch.object(engine, "run", return_value=self.result()):
            outcome = engine.apply_issue(
                issue, {"id": "hermes", "bin": ["hermes"]}, yes=True, quiet=True
            )
        self.assertFalse(outcome["verified"])
        self.assertEqual(outcome["verification_status"], "inconclusive")

    def test_manual_fix_without_actual_verification_is_not_verified(self) -> None:
        issue = {
            "id": "manual",
            "title": "Manual",
            "checks": [{"name": "probe", "cmd": "fake check"}],
            "fixes": [{"name": "manual", "cmd": "echo manual", "manual": True}],
            "verify": [{"name": "unsupported", "cmd": "fake verify", "platform": "other"}],
        }
        with patch.object(engine, "run", return_value=self.result()):
            outcome = engine.apply_issue(
                issue, {"id": "alpha", "bin": ["alpha"]}, yes=True, quiet=True
            )

        self.assertFalse(outcome["verified"])
        self.assertEqual(outcome["verification_status"], "inconclusive")
        self.assertTrue(any(item["name"] == "manual" for item in outcome["skipped"]))

    def test_cli_check_does_not_print_healthy_for_inconclusive(self) -> None:
        issue = {"id": "x", "title": "X", "checks": []}
        args = Namespace(id="x", agent="alpha", json=False)
        state = {
            "id": "x",
            "title": "X",
            "target": "alpha",
            "results": [],
            "broken": False,
            "status": "INCONCLUSIVE",
        }
        stdout = io.StringIO()
        with patch.object(engine, "resolve_target", return_value={"id": "alpha"}), patch.object(
            engine, "check_issue", return_value=state
        ), contextlib.redirect_stdout(stdout):
            status = cli.cmd_check({"issues": [issue]}, args)

        self.assertEqual(status, 1)
        self.assertIn("inconclusive", stdout.getvalue().lower())
        self.assertNotIn("healthy", stdout.getvalue().lower())

    def test_cli_json_sanitizes_subprocess_detail(self) -> None:
        issue = {
            "id": "secret",
            "title": "Secret",
            "checks": [{"name": "probe", "cmd": "fake"}],
        }
        args = Namespace(id="secret", agent="alpha", json=True)
        run_result = self.result(stdout="token=sk-abcdefghijklmnopqrstuvwxyz")
        stdout = io.StringIO()
        with patch.object(engine, "resolve_target", return_value={"id": "alpha"}), patch.object(
            engine, "run", return_value=run_result
        ), contextlib.redirect_stdout(stdout):
            status = cli.cmd_check({"issues": [issue]}, args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", stdout.getvalue())
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", payload["results"][0]["detail"])

    def test_cli_json_masks_domain_result_at_the_output_boundary(self) -> None:
        issue = {"id": "x", "title": "X", "checks": [{"name": "probe", "cmd": "fake"}]}
        args = Namespace(id="x", agent="alpha", json=True)
        state = {
            "id": "x",
            "title": "X",
            "target": "alpha",
            "results": [{"name": "probe", "status": "FAIL", "detail": "api_key=raw-secret-value-123456"}],
            "broken": True,
            "status": "FAIL",
        }
        stdout = io.StringIO()
        with patch.object(engine, "resolve_target", return_value={"id": "alpha"}), patch.object(
            engine, "check_issue", return_value=state
        ), contextlib.redirect_stdout(stdout):
            status = cli.cmd_check({"issues": [issue]}, args)
        self.assertEqual(status, 1)
        self.assertNotIn("raw-secret-value-123456", stdout.getvalue())
        self.assertNotIn("raw-secret-value-123456", json.dumps(json.loads(stdout.getvalue())))

    def test_dry_run_does_not_label_inapplicable_fixes_auto(self) -> None:
        issue = {
            "id": "planned",
            "title": "Planned",
            "checks": [{"name": "probe", "cmd": "fake"}],
            "fixes": [
                {"name": "windows-only", "cmd": "fake", "platform": "windows"},
                {"name": "native", "cmd": "fake", "cwd": "npm_root/native"},
                {"name": "manual", "cmd": "echo manual", "manual": True},
            ],
            "verify": [{"name": "verify", "cmd": "fake"}],
        }
        catalog = {"issues": [issue]}
        agent = {"id": "alpha", "name": "Alpha", "bin": ["alpha"]}
        with patch.object(engine.cat, "is_windows", return_value=False), patch.object(
            engine.cat, "load_catalog", return_value=catalog
        ), patch.object(engine, "resolve_target", return_value=agent):
            output = engine.apply_text(issue["id"], agent["id"])

        self.assertIn("[SKIP]", output)
        self.assertIn("windows-only", output)
        self.assertIn("native", output)
        self.assertIn("[MANUAL]", output)
        self.assertNotIn("[AUTO]   windows-only", output)
        self.assertNotIn("[AUTO]   native", output)

    def test_apply_prints_masked_fix_output(self) -> None:
        issue = {
            "id": "secret-fix",
            "title": "Secret fix",
            "checks": [{"name": "probe", "cmd": "fake"}],
            "fixes": [{"name": "write", "cmd": "fake"}],
            "verify": [{"name": "verify", "cmd": "fake"}],
        }
        run_result = self.result(stdout="api_key=sk-abcdefghijklmnopqrstuvwxyz")
        stdout = io.StringIO()
        with patch.object(engine, "run", return_value=run_result), contextlib.redirect_stdout(stdout):
            outcome = engine.apply_issue(
                issue, {"id": "alpha", "name": "Alpha", "bin": ["alpha"]}, yes=True
            )

        self.assertTrue(outcome["verified"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", stdout.getvalue())


class McpContractTests(unittest.TestCase):
    def test_review_rejects_missing_unknown_and_enum_arguments(self) -> None:
        missing = mcp.call_tool("check", {"issue_id": "x"})
        unknown = mcp.call_tool("check", {"issue_id": "x", "agent_id": "a", "extra": 1})
        bad_enum = mcp.call_tool("hooks", {"action": "bogus", "agent_id": "claude-code"})

        for result in (missing, unknown, bad_enum):
            self.assertTrue(result.get("isError"))
            self.assertIn("rejected", result["content"][0]["text"].lower())

    def test_tools_list_emits_required_fields(self) -> None:
        tools = {tool["name"]: tool for tool in mcp.tools_list()}
        self.assertIn("issue_id", tools["check"]["inputSchema"]["required"])
        self.assertIn("agent_id", tools["check"]["inputSchema"]["required"])

    def test_tool_exception_text_is_masked(self) -> None:
        spec = {
            "description": "test",
            "args": {},
            "fn": lambda a: (_ for _ in ()).throw(RuntimeError("token=sk-abcdefghijklmnopqrstuvwxyz")),
        }
        with patch.dict(mcp.TOOLS, {"secret_test": spec}, clear=False):
            result = mcp.call_tool("secret_test", {})

        self.assertTrue(result.get("isError"))
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", result["content"][0]["text"])

    def test_hooks_install_action_is_an_error_not_a_success_string(self) -> None:
        result = mcp.call_tool("hooks", {"action": "install", "agent_id": "claude-code"})
        self.assertTrue(result.get("isError"))
        self.assertIn("disabled", result["content"][0]["text"].lower())

    def test_provider_show_key_never_bypasses_final_mask(self) -> None:
        with patch.object(
            engine,
            "resolve_agent",
            return_value={"id": "alpha", "name": "Alpha"},
        ):
            text = engine.provider_text(
                provider="deepseek",
                agent_id="alpha",
                api_key="sk-abcdefghijklmnopqrstuvwxyz",
                show_key=True,
            )
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", text)

    def test_kimi_provider_snippet_uses_official_provider_schema(self) -> None:
        with patch.object(
            engine,
            "resolve_agent",
            return_value={"id": "kimi-code", "name": "Kimi Code", "config": "~/.kimi-code"},
        ), patch.object(engine.cat, "config_path", return_value="/tmp/kimi/config"):
            text = engine.provider_text(
                provider="moonshot", agent_id="kimi-code", api_key="sk-private-value-123456789"
            )
        self.assertIn("api.moonshot.ai/v1", text)
        self.assertIn('[providers."moonshot"]', text)
        self.assertIn('type = "kimi"', text)
        self.assertIn("api_key_env", text)
        self.assertNotIn("[provider.", text)
        self.assertNotIn("sk-private-value-123456789", text)

    def test_provider_apply_does_not_label_write_failure_as_applied(self) -> None:
        with patch.object(
            engine, "resolve_agent", return_value={"id": "claude-code", "name": "Claude"}
        ), patch.object(engine, "_apply_provider_settings", return_value="could not write settings"):
            text = engine.provider_text(
                provider="deepseek", agent_id="claude-code", api_key="secret", apply=True
            )
        self.assertIn("error", text.lower())
        self.assertNotIn("APPLIED: could not", text)

    def test_provider_rejects_non_http_or_credentialed_base_url(self) -> None:
        with patch.object(engine, "resolve_agent", return_value={"id": "alpha", "name": "Alpha"}):
            for base in (
                "file:///tmp/key",
                "https://user:pass@example.com/v1",
                "https://example.com/v1?token=secret",
                "https://example.com/v1#secret",
            ):
                text = engine.provider_text(
                    provider="custom", agent_id="alpha", api_key="secret", base_url=base
                )
                self.assertIn("error", text.lower())

    def test_check_and_apply_have_structured_adapter_results(self) -> None:
        with patch.object(
            mcp.engine,
            "check_result",
            return_value={"state": {"status": "FAIL", "broken": True}},
        ), patch.object(mcp.engine, "format_check_result", return_value="broken"):
            check = mcp.call_tool("check", {"issue_id": "x", "agent_id": "alpha"})
        with patch.object(
            mcp.engine,
            "apply_result",
            return_value={
                "dry_run": False,
                "outcome": {"verified": False, "verification_status": "inconclusive"},
            },
        ), patch.object(mcp.engine, "format_apply_result", return_value="not verified"):
            apply = mcp.call_tool(
                "apply", {"issue_id": "x", "agent_id": "alpha", "confirm": True}
            )
        self.assertTrue(check.get("isError"))
        self.assertTrue(apply.get("isError"))

    def test_audit_marks_file_budget_as_incomplete_instead_of_safe(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for i in range(205):
                (base / f"config-{i}.json").write_text("{}", encoding="utf-8")
            with patch.object(engine, "resolve_agent", return_value={"id": "alpha", "name": "Alpha", "config": base.as_posix()}):
                result = engine.audit_result("alpha", depth=2)
            text = engine.audit_text_from_result(result)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn("file-count limit", text)
        self.assertIn("scan is incomplete", text)

    def test_mcp_audit_marks_incomplete_scan_as_error(self) -> None:
        with patch.object(
            mcp.engine,
            "audit_result",
            return_value={"status": "INCONCLUSIVE", "reason": "scan incomplete", "files": [], "parse_errors": [], "leaks": []},
        ), patch.object(
            mcp.engine,
            "audit_text_from_result",
            return_value="AUDIT STATUS: INCONCLUSIVE\nscan is incomplete",
        ):
            result = mcp.call_tool("audit", {"agent_id": "alpha"})
        self.assertTrue(result.get("isError"))

    def test_output_masking_covers_env_and_json_secret_fields(self) -> None:
        text = 'MCODE_PROVIDER_API_KEY=supersecretvalue {"api_key":"jsonSecret123"}'
        masked = rep.mask_secrets(text)
        self.assertNotIn("supersecretvalue", masked)
        self.assertNotIn("jsonSecret123", masked)

    def test_version_probe_never_treats_stderr_as_installed_version(self) -> None:
        failed = subprocess.CompletedProcess(["alpha", "--version"], 1, "", "command not found")
        with patch.object(engine, "resolve_agent", return_value={"id": "alpha", "name": "Alpha", "bin": ["alpha"]}), patch.object(
            engine.subprocess, "run", return_value=failed
        ):
            result = engine.versions_result("alpha")
            text = engine.versions_text_from_result(result)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn("ERROR exit 1", text)
        self.assertIn("=> inconclusive", text)

    def test_mcp_versions_marks_probe_failure_as_error(self) -> None:
        with patch.object(
            mcp.engine,
            "versions_result",
            return_value={"status": "INCONCLUSIVE", "name": "Alpha", "installed": "ERROR", "latest": None, "agent": "alpha"},
        ), patch.object(
            mcp.engine,
            "versions_text_from_result",
            return_value="VERSION CHECK\n=> inconclusive",
        ):
            result = mcp.call_tool("versions", {"agent_id": "alpha"})
        self.assertTrue(result.get("isError"))

    def test_restore_tool_uses_explicit_status_not_text_markers(self) -> None:
        with patch.object(
            mcp.engine,
            "restore_text",
            return_value=operation_result.StatusText("error-looking but explicitly ok", "ok"),
        ):
            result = mcp.call_tool("restore", {})
        self.assertFalse(result.get("isError"))
        self.assertIn("explicitly ok", result["content"][0]["text"])

    def test_provider_tool_uses_explicit_status_not_text_markers(self) -> None:
        with patch.object(
            mcp.engine,
            "provider_text",
            return_value=operation_result.StatusText("error-looking but explicitly ok", "ok"),
        ):
            result = mcp.call_tool("provider", {"provider": "custom", "agent_id": "alpha"})
        self.assertFalse(result.get("isError"))

    def test_provider_tool_marks_error_text_as_error(self) -> None:
        with patch.object(mcp.engine, "provider_text", return_value="error: invalid provider"):
            result = mcp.call_tool("provider", {"provider": "custom", "agent_id": "alpha"})
        self.assertTrue(result.get("isError"))

    def test_net_tool_marks_structured_failure_as_error(self) -> None:
        with patch.object(
            mcp.engine,
            "net_result",
            return_value={"host": "invalid.invalid", "ok": False, "error": "unreachable", "ms": None},
        ), patch.object(mcp.engine, "net_text_from_result", return_value="UNREACHABLE invalid.invalid"):
            result = mcp.call_tool("net", {"host": "invalid.invalid", "timeout": 0.1})

        self.assertTrue(result.get("isError"))
        self.assertIn("unreachable", result["content"][0]["text"].lower())

    def test_review_enforces_numeric_bounds(self) -> None:
        result = mcp.call_tool("net", {"host": "example.invalid", "timeout": 0})
        self.assertTrue(result.get("isError"))
        self.assertIn("minimum", result["content"][0]["text"].lower())

    def test_info_and_restore_domain_failures_are_tool_errors(self) -> None:
        with patch.object(mcp.engine, "info_text", side_effect=mcp.engine.TargetError("unknown issue")):
            info = mcp.call_tool("info", {"issue_id": "missing"})
        with patch.object(mcp.engine, "restore_text", return_value="backup not found: missing"):
            restore = mcp.call_tool("restore", {"backup": "missing", "agent_id": "alpha", "confirm": True})
        self.assertTrue(info.get("isError"))
        self.assertTrue(restore.get("isError"))

    def test_mcp_envelope_rejects_non_object_params_and_unknown_tool(self) -> None:
        state = mcp.ProtocolState()
        mcp._handle(
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": mcp.PROTOCOL}},
            state,
        )
        mcp._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, state)
        response = mcp._handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}, state
        )
        self.assertEqual(response["error"]["code"], -32602)
        response = mcp._handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "missing", "arguments": {}}}, state
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_mcp_rejects_tools_before_initialize_and_duplicate_initialize(self) -> None:
        state = mcp.ProtocolState()
        before = mcp._handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "agents", "arguments": {}}}, state
        )
        self.assertEqual(before["error"]["code"], -32002)
        first = mcp._handle(
            {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": mcp.PROTOCOL}}, state
        )
        self.assertIn("result", first)
        duplicate = mcp._handle(
            {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"protocolVersion": mcp.PROTOCOL}}, state
        )
        self.assertEqual(duplicate["error"]["code"], -32600)
        still_closed = mcp._handle(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}, state
        )
        self.assertEqual(still_closed["error"]["code"], -32002)
        mcp._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, state)
        opened = mcp._handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}}, state)
        self.assertIn("result", opened)

    def test_mcp_hooks_uninstall_requires_explicit_confirmation(self) -> None:
        with patch.object(mcp.hooks, "dispatch", return_value="should not run") as dispatch:
            result = mcp.call_tool("hooks", {"action": "uninstall", "agent_id": "alpha"})
        dispatch.assert_not_called()
        self.assertIn("confirm", result["content"][0]["text"].lower())
        with patch.object(mcp.hooks, "dispatch", return_value="alpha: not registered") as dispatch:
            result = mcp.call_tool(
                "hooks", {"action": "uninstall", "agent_id": "alpha", "confirm": True}
            )
        dispatch.assert_called_once()
        self.assertNotIn("isError", result)

    def test_mcp_schema_declares_closed_objects_and_rejects_null(self) -> None:
        schema = next(item for item in mcp.tools_list() if item["name"] == "provider")["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        result = mcp.call_tool("provider", {"provider": "custom", "agent_id": "alpha", "api_key": None})
        self.assertTrue(result.get("isError"))

    def test_initialize_rejects_unsupported_protocol(self) -> None:
        response = mcp._handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_mcp_batch_dispatches_requests_and_filters_notifications(self) -> None:
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": mcp.PROTOCOL}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(batch) + "\n")), contextlib.redirect_stdout(stdout):
            self.assertEqual(mcp.main(), 0)
        responses = json.loads(stdout.getvalue())
        self.assertIsInstance(responses, list)
        self.assertEqual([r["id"] for r in responses], [1, 2])

    def test_mcp_null_id_is_a_valid_request_id(self) -> None:
        response = mcp._handle({"jsonrpc": "2.0", "id": None, "method": "ping"})
        self.assertEqual(response["id"], None)
        self.assertIn("result", response)

    def test_malformed_notifications_receive_no_response(self) -> None:
        self.assertIsNone(mcp._handle({"jsonrpc": "1.0", "method": 42}))

    def test_invalid_json_gets_protocol_error_instead_of_silent_drop(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO("{not-json\n")), contextlib.redirect_stdout(stdout):
            status = mcp.main()
        response = json.loads(stdout.getvalue())

        self.assertEqual(status, 0)
        self.assertEqual(response["error"]["code"], -32700)
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping","params":NaN}\n')), contextlib.redirect_stdout(stdout):
            mcp.main()
        self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], -32700)


class AdapterExitTests(unittest.TestCase):
    def test_cli_integration_error_lines_return_nonzero(self) -> None:
        for argv, function_name in (
            (["mcp", "register", "alpha"], "mcp_register"),
            (["install", "--agent", "alpha"], "install_target"),
            (["uninstall", "--agent", "alpha"], "uninstall_target"),
        ):
            stdout = io.StringIO()
            operation = Mock(return_value=["adapter-boundary: explicit failure"])
            with patch.object(cli.cat, "load_catalog", return_value={"issues": []}), patch.object(
                cli.hooks, function_name, operation
            ), contextlib.redirect_stdout(stdout):
                status = cli.main(argv)
            operation.assert_called_once_with("alpha")
            self.assertEqual(status, 1, argv)
            self.assertIn("adapter-boundary: explicit failure", stdout.getvalue())

    def test_cli_json_target_error_is_one_masked_json_document(self) -> None:
        issue = {"id": "x", "title": "X", "checks": []}
        args = Namespace(id="x", agent="alpha", json=True)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(engine, "resolve_target", side_effect=engine.TargetError("token=sk-abcdefghijklmnopqrstuvwxyz")), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cli.cmd_check({"issues": [issue]}, args)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(payload["status"], "error")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_net_failure_uses_structured_status(self) -> None:
        with patch.object(cli.cat, "load_catalog", return_value={"issues": []}), patch.object(
            engine,
            "net_result",
            return_value={"host": "invalid.invalid", "ok": False, "error": "unreachable", "ms": 0},
        ), contextlib.redirect_stdout(io.StringIO()):
            status = cli.main(["net", "invalid.invalid", "--timeout", "0.1"])

        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
