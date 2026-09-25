from __future__ import annotations

import contextlib
import io
import json
import re
import unittest
from pathlib import Path
from typing import List
from unittest.mock import patch

from agentfix import engine, mcp
from agentfix.cli import build_parser, cmd_apply, cmd_check, main


ROOT = Path(__file__).resolve().parents[1]
USER_DOCS = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "README_cn.md",
    ROOT / "SKILL.md",
    ROOT / "scripts" / "README.md",
    ROOT / "mcp" / "README.md",
    *(ROOT / "fixes").glob("*.md"),
)

EXECUTABLE_PREFIX = r"(?:\./scripts/fix(?:\.py)?|python\s+scripts/fix\.py|\bfix)"
TARGETED_COMMAND = re.compile(
    EXECUTABLE_PREFIX
    + r"\s+(?:check|apply)\s+(?P<issue>[A-Za-z0-9<>-]+)(?P<tail>.*)$"
)
NET_COMMAND = re.compile(EXECUTABLE_PREFIX + r"\s+net(?P<tail>.*)$")
RETIRED_COMMAND = re.compile(
    EXECUTABLE_PREFIX + r"\s+(?:doctor|auto|selfheal)\b"
)


def fenced_command_lines(path: Path) -> List[str]:
    lines: list[str] = []
    in_fence = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


class SurfaceContractTests(unittest.TestCase):
    def test_check_and_apply_keep_json_output(self) -> None:
        parser = build_parser()
        for command in ("check", "apply"):
            before = parser.parse_args(
                ["--json", command, "node-version-too-old", "--agent", "opencode"]
            )
            after = parser.parse_args(
                [command, "node-version-too-old", "--agent", "opencode", "--json"]
            )
            with self.subTest(command=command, placement="before"):
                self.assertTrue(before.json)
            with self.subTest(command=command, placement="after"):
                self.assertTrue(after.json)

    def test_list_and_info_reject_noop_json_option(self) -> None:
        parser = build_parser()
        rejected = (
            ["list", "--json"],
            ["info", "provider-config", "--json"],
            ["--json", "list"],
            ["--json", "info", "provider-config"],
        )
        for argv in rejected:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    if argv[0] == "--json":
                        main(argv)
                    else:
                        parser.parse_args(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_check_json_stdout_is_one_document(self) -> None:
        issue = {
            "id": "test-node-check",
            "title": "Node check",
            "checks": [{"name": "version", "cmd": "fake --version"}],
        }
        args = build_parser().parse_args(
            ["check", issue["id"], "--agent", "opencode", "--json"]
        )
        run_result = {
            "ok": True,
            "exit": 0,
            "stdout": "",
            "stderr": "",
            "duration": 0.0,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            engine, "resolve_target", return_value={"id": "opencode"}
        ), patch.object(engine, "run", return_value=run_result), contextlib.redirect_stdout(
            stdout
        ), contextlib.redirect_stderr(stderr):
            status = cmd_check({"issues": [issue]}, args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertFalse(payload["broken"])
        self.assertEqual(stderr.getvalue(), "")

    def test_apply_json_stdout_is_one_document_without_yes(self) -> None:
        issue = {
            "id": "test-node-apply",
            "title": "Node apply",
            "doc": "fixes/node-version.md",
            "fixes": [{"name": "repair", "cmd": "fake repair"}],
            "verify": [{"name": "verify", "cmd": "fake verify"}],
        }
        args = build_parser().parse_args(
            ["--json", "apply", issue["id"], "--agent", "opencode"]
        )
        run_result = {
            "ok": True,
            "exit": 0,
            "stdout": "",
            "stderr": "",
            "duration": 0.0,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            engine, "resolve_target", return_value={"id": "opencode"}
        ), patch.object(engine, "run", return_value=run_result), patch(
            "sys.stdin", io.StringIO("n\n")
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cmd_apply({"issues": [issue]}, args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 1)
        self.assertFalse(payload["verified"])
        self.assertEqual(payload["verification_status"], "inconclusive")
        self.assertIn("run this fix?", stderr.getvalue())

    def test_npm_lifecycle_has_one_catalog_owner(self) -> None:
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        issues = {issue["id"]: issue for issue in catalog["issues"]}

        generic_fixes = issues["agent-broken-generic"].get("fixes", [])
        generic_text = json.dumps(generic_fixes)
        self.assertNotIn("npm_root", generic_text)
        self.assertNotIn("child_process", generic_text)
        self.assertNotIn("execSync", generic_text)

        npm_fixes = issues["npm-postinstall-skipped"].get("fixes", [])
        self.assertTrue(
            any(
                fix.get("cwd", "").startswith("npm_root/")
                and "child_process" in fix.get("cmd", "")
                and "execSync" in fix.get("cmd", "")
                for fix in npm_fixes
            )
        )
        self.assertTrue(issues["npm-postinstall-skipped"].get("verify"))

        provider_fixes = json.dumps(issues["provider-config"].get("fixes", []))
        self.assertIn("MCP provider tool", provider_fixes)
        self.assertNotIn("provider_setup", provider_fixes)

    def test_persistence_and_adapter_owners_are_single(self) -> None:
        package = ROOT / "agentfix"
        archive_imports = {
            path.name
            for path in package.glob("*.py")
            if "import zipfile" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(archive_imports, {"state.py"})
        state_source = (package / "state.py").read_text(encoding="utf-8")
        self.assertEqual(state_source.count("def atomic_write_text"), 1)
        self.assertEqual(state_source.count("def create_zip_atomic"), 1)
        result_source = (package / "result.py").read_text(encoding="utf-8")
        self.assertNotIn("not mcp-capable", result_source)
        self.assertIn("class StatusText", result_source)

    def test_provider_apply_is_claude_only(self) -> None:
        with patch.object(
            engine, "resolve_agent", return_value={"id": "opencode", "name": "OpenCode"}
        ), patch.object(engine, "_apply_provider_settings") as apply_settings:
            output = engine.provider_text(
                provider="ollama", agent_id="opencode", apply=True
            )

        apply_settings.assert_not_called()
        self.assertIn("NO WRITE", output)
        self.assertIn("manual/config-file", output)
        self.assertIn(
            "Claude Code settings only", mcp.TOOLS["provider"]["description"]
        )
        self.assertIn(
            "other targets", mcp.TOOLS["provider"]["args"]["apply"]["description"]
        )

    def test_fenced_repair_commands_always_target_one_agent(self) -> None:
        offenders: List[str] = []
        for path in USER_DOCS:
            for line in fenced_command_lines(path):
                for match in TARGETED_COMMAND.finditer(line):
                    if "--agent" not in match.group("tail"):
                        offenders.append(f"{path.relative_to(ROOT)}: {line}")
        self.assertEqual(offenders, [])

    def test_fenced_commands_use_supported_net_and_no_retired_bulk_modes(self) -> None:
        offenders: List[str] = []
        for path in USER_DOCS:
            for line in fenced_command_lines(path):
                if RETIRED_COMMAND.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}: {line}")
                net_match = NET_COMMAND.search(line)
                if net_match:
                    host_args = net_match.group("tail").split()
                    if not host_args or host_args[0].startswith("-"):
                        offenders.append(f"{path.relative_to(ROOT)}: {line}")
        self.assertEqual(offenders, [])

    def test_command_scanners_cover_all_launcher_forms(self) -> None:
        targeted = (
            "./scripts/fix check provider-config --agent opencode",
            "python scripts/fix.py apply provider-config --agent opencode",
            "fix check provider-config --agent opencode",
        )
        for command in targeted:
            with self.subTest(command=command):
                self.assertIsNotNone(TARGETED_COMMAND.search(command))

        for command in (
            "./scripts/fix net api.deepseek.com",
            "python scripts/fix.py net api.deepseek.com",
            "fix net api.deepseek.com",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(NET_COMMAND.search(command))

        for command in (
            "./scripts/fix doctor",
            "python scripts/fix.py auto",
            "fix selfheal",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(RETIRED_COMMAND.search(command))

    def test_docs_reference_existing_mcp_tool_names(self) -> None:
        for path in USER_DOCS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn("provider_setup", text)
                self.assertNotIn("config_audit", text)

    def test_launcher_examples_pass_an_explicit_target(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for command in (
            "install/install.sh",
            "install\\install.ps1",
            "python scripts/fix.py uninstall",
        ):
            matching = [line for line in skill.splitlines() if command in line]
            with self.subTest(command=command):
                self.assertTrue(matching)
                self.assertTrue(all("--agent" in line for line in matching))

    def test_opencode_quick_start_uses_specialized_npm_issue(self) -> None:
        for name in ("README.md", "README_cn.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotIn(
                    "apply agent-broken-generic --agent opencode", text
                )

    def test_zcode_gui_path_does_not_invoke_unsupported_catalog_issue(self) -> None:
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        issues = {issue["id"]: issue for issue in catalog["issues"]}
        self.assertNotIn("zcode", issues["gui-path-blind"].get("agents", []))

        for name in ("AGENTS.md", "SKILL.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            gui_lines = [line for line in text.splitlines() if "gui-path-blind" in line]
            with self.subTest(name=name):
                self.assertTrue(gui_lines)
                self.assertTrue(any("fixes/zcode.md" in line for line in gui_lines))

        zcode_doc = (ROOT / "fixes" / "zcode.md").read_text(encoding="utf-8")
        self.assertNotIn("fix apply gui-path-blind", zcode_doc)
        self.assertNotIn("fix check gui-path-blind", zcode_doc)

    def test_install_launchers_forward_cli_arguments(self) -> None:
        install_sh = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
        uninstall_sh = (ROOT / "install" / "uninstall.sh").read_text(encoding="utf-8")
        install_ps1 = (ROOT / "install" / "install.ps1").read_text(encoding="utf-8")

        self.assertIn('scripts/fix.py" install "$@"', install_sh)
        self.assertIn('scripts/fix.py" uninstall "$@"', uninstall_sh)
        self.assertIn("scripts\\fix.py", install_ps1)
        self.assertIn("install @args", install_ps1)


if __name__ == "__main__":
    unittest.main()
