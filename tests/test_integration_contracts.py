#!/usr/bin/env python3
"""Hermetic integration ownership and lifecycle contracts."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agentfix import cli, hooks, mcp
from agentfix import engine as engine_module
from agentfix import catalog as cat
from agentfix import result as operation_result
from agentfix.result import StatusText


class IntegrationSafetyTests(unittest.TestCase):
    def test_hooks_facade_keeps_public_runtime_surface(self) -> None:
        required = (
            "dispatch",
            "hooks_status",
            "hooks_uninstall",
            "mcp_register",
            "mcp_remove",
            "install_target",
            "uninstall_target",
            "ROOT",
            "MCP_SERVER",
            "SKILL_NAME",
            "SOURCE_ITEMS",
            "PLUGIN_NAME",
            "CRON_NAME",
            "MARK_BEGIN",
            "AGENTS_MD_BEGIN",
            "AGENTS_MD_END",
        )
        self.assertIs(cli.hooks, hooks)
        self.assertIs(mcp.hooks, hooks)
        for name in required:
            self.assertTrue(hasattr(hooks, name), name)

    def test_installed_cli_points_to_staged_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            runtime = root / "runtime"
            (runtime / "scripts").mkdir(parents=True)
            (runtime / "scripts" / "fix.py").write_text("#", encoding="utf-8")
            with patch.object(hooks.Path, "home", return_value=home), patch.dict(
                os.environ, {"HOME": str(home), "USERPROFILE": str(home)}, clear=False
            ), patch.object(hooks.cat, "is_windows", return_value=True):
                result = hooks._install_cli(runtime)
            launcher = home / "bin" / "fix.cmd"
            self.assertIn(str(runtime), launcher.read_text(encoding="utf-8"))

    def test_posix_cli_fallback_writes_absolute_runtime_shim(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            runtime = root / "runtime"
            (runtime / "scripts").mkdir(parents=True)
            launcher = runtime / "scripts" / "fix.py"
            launcher.write_text("#", encoding="utf-8")
            with patch.object(hooks.Path, "home", return_value=home), patch.object(
                hooks.cat, "is_windows", return_value=False
            ), patch.object(hooks.os, "symlink", side_effect=OSError("symlink unavailable")):
                result = hooks._install_cli(runtime)
            shim = home / "bin" / "fix"
            self.assertIn(str(shim), result)
            self.assertEqual(shim.read_text(encoding="utf-8"), f'#!/usr/bin/env bash\nexec "{launcher}" "$@"\n')

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_posix_cli_refuses_unowned_existing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            runtime = root / "runtime"
            external = root / "external"
            (runtime / "scripts").mkdir(parents=True)
            launcher = runtime / "scripts" / "fix.py"
            launcher.write_text("#", encoding="utf-8")
            external.write_text("owned elsewhere", encoding="utf-8")
            bindir = home / "bin"
            bindir.mkdir(parents=True)
            target = bindir / "fix"
            try:
                target.symlink_to(external)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with patch.object(hooks.Path, "home", return_value=home), patch.object(
                hooks.cat, "is_windows", return_value=False
            ):
                with self.assertRaises(hooks.state.StateError):
                    hooks._install_cli(runtime)
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "owned elsewhere")

    def test_hermes_scripts_dir_uses_macos_home_layout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch.object(hooks.Path, "home", return_value=home), patch.object(
                hooks.sys, "platform", "darwin"
            ):
                self.assertEqual(hooks._hermes_scripts_dir(), home / ".hermes" / "scripts")

    def test_legacy_hook_flavors_status_and_uninstall_are_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            claude_settings = home / ".claude" / "settings.json"
            claude_settings.parent.mkdir(parents=True)
            command = hooks._fix_cmd()
            claude_settings.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {"hooks": [{"command": command}]},
                                {"hooks": [{"command": "user-owned"}]},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            plugin = home / ".config" / "opencode" / "plugins" / hooks.PLUGIN_NAME
            plugin.parent.mkdir(parents=True)
            plugin.write_text("owned", encoding="utf-8")
            hermes_scripts = home / ".hermes" / "scripts"
            hermes_scripts.mkdir(parents=True)
            (hermes_scripts / "agent-fix-watchdog.py").write_text("owned", encoding="utf-8")
            cron_calls = []

            def cron(*args, timeout=60):
                cron_calls.append(args)
                if args[0] == "list":
                    return True, hooks.CRON_NAME
                return True, "removed"

            with patch.object(hooks.Path, "home", return_value=home), patch.object(
                hooks, "_hermes_scripts_dir", return_value=hermes_scripts
            ), patch.object(hooks, "_hermes_cron", side_effect=cron):
                self.assertIn("registered", hooks._hook_claude("status"))
                self.assertIn("registered", hooks._hook_opencode("status"))
                self.assertIn("registered", hooks._hook_hermes("status"))
                self.assertIn("removed", hooks._hook_claude("uninstall"))
                self.assertIn("plugin removed", hooks._hook_opencode("uninstall"))
                self.assertIn("watchdog script removed", hooks._hook_hermes("uninstall"))
            remaining = json.loads(claude_settings.read_text(encoding="utf-8"))
            self.assertEqual(remaining["hooks"]["SessionStart"], [{"hooks": [{"command": "user-owned"}]}])
            self.assertFalse(plugin.exists())
            self.assertFalse((hermes_scripts / "agent-fix-watchdog.py").exists())
            self.assertEqual(cron_calls, [("list",), ("list",), ("remove", hooks.CRON_NAME)])

    def test_legacy_codex_status_and_uninstall_preserve_unrelated_sections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            config = home / ".codex" / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                "[hooks]\nsession_start = \"selfheal agent-fix fix.py\"\n\n[other]\nkeep = true\n",
                encoding="utf-8",
            )
            with patch.object(hooks.Path, "home", return_value=home):
                self.assertIn("registered", hooks._hook_codex("status"))
                self.assertIn("removed", hooks._hook_codex("uninstall"))
            text = config.read_text(encoding="utf-8")
            self.assertNotIn("selfheal", text)
            self.assertIn("[other]\nkeep = true", text)

    def test_remaining_json_mcp_flavors_register_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            opencode = home / ".config" / "opencode" / "opencode.json"
            cursor = home / ".cursor" / "mcp.json"
            opencode.parent.mkdir(parents=True)
            cursor.parent.mkdir(parents=True)
            with patch.object(hooks.Path, "home", return_value=home):
                opencode_result = hooks._mcp_opencode(True)
                cursor_result = hooks._mcp_cursor(True)
                self.assertIn("registered", opencode_result)
                self.assertIn("registered", cursor_result)
                self.assertIn("removed", hooks._mcp_opencode(False))
                self.assertIn("removed", hooks._mcp_cursor(False))
            self.assertNotIn("agent-fix", json.loads(opencode.read_text(encoding="utf-8")).get("mcp", {}))
            self.assertNotIn("agent-fix", json.loads(cursor.read_text(encoding="utf-8")).get("mcpServers", {}))

    def test_installer_orchestration_order_and_no_mcp_registration(self) -> None:
        events = []
        skill = Path("skill-target")
        with patch.object(hooks, "_skill_targets", return_value=[skill]), patch.object(
            hooks, "_install_skill", side_effect=lambda target: events.append("skill") or "installed"
        ), patch.object(
            hooks, "_agents_md_target", return_value=None
        ), patch.object(hooks, "_install_cli", side_effect=lambda root: events.append("cli") or "cli"), patch.object(
            hooks, "mcp_register", side_effect=Mock(side_effect=AssertionError("install must not register MCP"))
        ):
            result = hooks.install_target("alpha")
        self.assertIn("installed", "\n".join(result))
        self.assertEqual(events, ["skill", "cli"])

        target = Path("uninstall-target")
        with patch.object(hooks, "_skill_targets", return_value=[target]), patch.object(
            hooks, "hooks_uninstall", side_effect=lambda agent: events.append("hooks") or ["clean"]
        ), patch.object(
            hooks, "_mcp_agents", side_effect=lambda agent: events.append("lookup") or [("alpha", "fake")]
        ), patch.object(
            hooks, "mcp_remove", side_effect=lambda agent: events.append("mcp") or ["removed"]
        ), patch.object(
            hooks, "_agents_md_target", return_value=None
        ), patch.object(hooks.shutil, "rmtree"):
            result = hooks.uninstall_target("alpha")
        self.assertIn("done", result[-1])
        self.assertEqual(events[-3:], ["hooks", "lookup", "mcp"])

    def test_kimi_and_minimax_mcp_use_explicit_local_config_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".kimi-code").mkdir()
            (home / ".minimax" / "mcp").mkdir(parents=True)
            with patch.object(hooks.Path, "home", return_value=home), patch.dict(
                os.environ,
                {"HOME": str(home), "USERPROFILE": str(home), "KIMI_CODE_HOME": "", "MINIMAX_DATA_DIR": "", "MAVIS_DATA_DIR": ""},
                clear=False,
            ):
                kimi_result = hooks._mcp_kimi(True)
                minimax_result = hooks._mcp_minimax(True)
            kimi_data = (home / ".kimi-code" / "mcp.json").read_text(encoding="utf-8")
            minimax_data = (home / ".minimax" / "mcp.json").read_text(encoding="utf-8")
            self.assertIn("registered", kimi_result)
            self.assertIn("registered", minimax_result)
            self.assertIn("mcpServers", kimi_data)
            self.assertIn("mcpServers", minimax_data)
            with patch.object(hooks.Path, "home", return_value=home), patch.dict(
                os.environ,
                {"HOME": str(home), "USERPROFILE": str(home), "KIMI_CODE_HOME": "", "MINIMAX_DATA_DIR": "", "MAVIS_DATA_DIR": ""},
                clear=False,
            ):
                self.assertIn("removed", hooks._mcp_kimi(False))
                self.assertIn("removed", hooks._mcp_minimax(False))

    def test_json_mcp_does_not_overwrite_or_remove_unowned_same_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mcp.json"
            original = {"mcpServers": {"agent-fix": {"command": "user-owned"}}}
            path.write_text(json.dumps(original), encoding="utf-8")
            entry = {"command": "agent-fix", "args": ["x"]}
            merged = hooks._json_bucket_merge(path, "mcpServers", entry, "test")
            removed = hooks._json_bucket_unmerge(path, "mcpServers", "test", entry)
            self.assertIn("not owned", merged)
            self.assertIn("refusing", removed)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_minimax_mcp_removes_primary_and_owned_legacy_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            base = home / ".minimax"
            (base / "mcp").mkdir(parents=True)
            entry = {"type": "stdio", "command": hooks.sys.executable, "args": [hooks.MCP_SERVER.as_posix()], "enabled": True}
            for path in (base / "mcp.json", base / "mcp" / "mcp.json"):
                path.write_text(json.dumps({"mcpServers": {"agent-fix": entry}}), encoding="utf-8")
            with patch.object(hooks.Path, "home", return_value=home), patch.dict(
                os.environ, {"HOME": str(home), "USERPROFILE": str(home), "MINIMAX_DATA_DIR": "", "MAVIS_DATA_DIR": ""}, clear=False
            ):
                result = hooks._mcp_minimax(False)
            self.assertIn("removed", result)
            self.assertEqual(json.loads((base / "mcp.json").read_text(encoding="utf-8")), {"mcpServers": {}})
            self.assertEqual(json.loads((base / "mcp" / "mcp.json").read_text(encoding="utf-8")), {"mcpServers": {}})

    def test_runtime_manifest_includes_mcp_server(self) -> None:
        self.assertIn("mcp/server.py", hooks.SOURCE_ITEMS)

    def test_install_stages_runtime_and_removes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "agentfix").mkdir()
            (source / "mcp").mkdir()
            (source / "fixes").mkdir()
            (source / "SKILL.md").write_text("skill", encoding="utf-8")
            (source / "catalog.json").write_text("{}", encoding="utf-8")
            (source / "scripts" / "fix.py").write_text("# launcher", encoding="utf-8")
            (source / "agentfix" / "__init__.py").write_text("", encoding="utf-8")
            (source / "mcp" / "server.py").write_text("# server", encoding="utf-8")
            (source / "fixes" / "readme.md").write_text("doc", encoding="utf-8")
            target = root / "installed" / "agent-fix"
            target.mkdir(parents=True)
            (target / "stale.txt").write_text("stale", encoding="utf-8")
            with patch.object(hooks, "ROOT", source):
                result = hooks._install_skill(target)
            self.assertIn("skill installed", result)
            self.assertFalse((target / "stale.txt").exists())
            self.assertTrue((target / "mcp" / "server.py").is_file())

    def test_codex_owned_block_with_blanks_and_duplicate_is_removed_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "config.toml"
            original = (
                "[mcp_servers.agent-fix]\ncommand = \"python\"\n\n# owned\nargs = [\"server.py\"]\n"
                "[mcp_servers.agent-fix]\ncommand = \"python\"\nargs = [\"server.py\"]\n"
                "[other]\nkeep = true\n"
            )
            config.write_text(original, encoding="utf-8")
            with patch.object(hooks, "_codex_config", return_value=config):
                result = hooks._mcp_codex(False)
            text = config.read_text(encoding="utf-8")
            self.assertIn("removed", result)
            self.assertEqual(text.count("[mcp_servers.agent-fix]"), 0)
            self.assertIn("[other]\nkeep = true", text)

    def test_unpaired_codex_marker_is_not_removed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "config.toml"
            original = "[hooks]\n" + hooks.MARK_BEGIN + " 2026-01-01)\nkeep me\n"
            config.write_text(original, encoding="utf-8")
            with patch.object(hooks, "_codex_config", return_value=config):
                result = hooks._hook_codex("uninstall")
            self.assertIn("refusing", result.lower())
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_unpaired_agents_marker_is_not_removed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "AGENTS.md"
            original = hooks.AGENTS_MD_BEGIN + " 2026-01-01) ---\nkeep me\n"
            config.write_text(original, encoding="utf-8")
            result = hooks._agents_md_unhook(config)
            self.assertIn("refusing", result.lower())
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_uninstall_preserves_unpaired_agents_refusal_as_error_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "AGENTS.md"
            config.write_text(hooks.AGENTS_MD_BEGIN + " 2026-01-01) ---\nkeep me\n", encoding="utf-8")
            with patch.object(hooks, "_skill_targets", return_value=[Path(td) / "skill"]), patch.object(
                hooks, "_agents_md_target", return_value=config
            ):
                result = hooks.uninstall_target("alpha")
            self.assertEqual(operation_result.status_of(result), "error")
            self.assertEqual(config.read_text(encoding="utf-8"), hooks.AGENTS_MD_BEGIN + " 2026-01-01) ---\nkeep me\n")

    def test_uninstall_reports_partial_skill_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "skill"
            target.mkdir()
            with patch.object(hooks, "_skill_targets", return_value=[target]), patch.object(
                hooks, "hooks_uninstall", return_value=["nothing to remove"]
            ), patch.object(hooks, "_mcp_agents", return_value=[]), patch.object(
                hooks, "_agents_md_target", return_value=None
            ), patch.object(hooks.shutil, "rmtree", side_effect=OSError("locked")):
                lines = hooks.uninstall_target("alpha")
            self.assertTrue(any(line.lower().startswith("error:") for line in lines))
            self.assertTrue(any("incomplete" in line.lower() for line in lines))

    def test_unowned_codex_mcp_block_is_not_removed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "config.toml"
            original = "[mcp_servers.agent-fix]\nkeep = true\n"
            config.write_text(original, encoding="utf-8")
            with patch.object(hooks, "_codex_config", return_value=config):
                result = hooks._mcp_codex(False)
            self.assertIn("refusing", result.lower())
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_claude_mcp_register_does_not_remove_existing_entry(self) -> None:
        with patch.object(hooks.shutil, "which", return_value="claude"), patch.object(
            hooks, "_run_cmd", return_value=(True, "agent-fix already exists")
        ) as run_cmd:
            result = hooks._mcp_claude(True)
        self.assertIn("SKIP", result)
        self.assertEqual(run_cmd.call_count, 1)

    def test_run_cmd_reports_nonzero_exit(self) -> None:
        class Result:
            returncode = 7
            stdout = ""
            stderr = "permission denied"

        with patch.object(hooks.subprocess, "run", return_value=Result()):
            ok, output = hooks._run_cmd(["fake"])
        self.assertFalse(ok)
        self.assertIn("error", output.lower())

    def test_not_mcp_capable_remove_is_error(self) -> None:
        result = operation_result.from_lines(
            "mcp_remove", ["agent 'alpha' is not MCP-capable"], status="error"
        )
        self.assertFalse(result.ok)

    def test_uninstall_reports_inconclusive_cleanup_as_failure(self) -> None:
        """A cleanup that could not confirm a removal must not report success;
        the CLI turns this status into a nonzero exit."""
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "skills" / "agent-fix"
            skipped = [StatusText("SKIP unparsable", "inconclusive")]
            with patch.object(hooks, "_skill_targets", return_value=[missing]), patch.object(
                hooks, "hooks_uninstall", return_value=list(skipped)
            ), patch.object(hooks, "_mcp_agents", return_value=[("alpha", "codex")]), patch.object(
                hooks, "mcp_remove", return_value=[StatusText("SKIP not found", "inconclusive")]
            ), patch.object(hooks, "_agents_md_target", return_value=None):
                result = hooks.uninstall_target("alpha")
            self.assertEqual(operation_result.status_of(result, "ok"), "error")

    def test_uninstall_still_succeeds_when_cleanup_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "skills" / "agent-fix"
            with patch.object(hooks, "_skill_targets", return_value=[missing]), patch.object(
                hooks, "hooks_uninstall", return_value=[StatusText("removed", "ok")]
            ), patch.object(hooks, "_mcp_agents", return_value=[]), patch.object(
                hooks, "_agents_md_target", return_value=None
            ):
                result = hooks.uninstall_target("alpha")
            self.assertEqual(operation_result.status_of(result, "ok"), "ok")

    def test_install_cli_refuses_to_overwrite_unowned_launcher(self) -> None:
        """Installing must not silently destroy a same-named file it does not own."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            bindir = home / "bin"
            bindir.mkdir()
            foreign = bindir / "fix.cmd"
            foreign.write_text("@echo off\ncall C:/other/tool.exe %*\n", encoding="utf-8")
            with patch.object(Path, "home", return_value=home), patch.object(
                cat, "is_windows", return_value=True
            ):
                with self.assertRaises(Exception):
                    hooks._install_cli(Path(hooks.ROOT))
            self.assertIn("other/tool.exe", foreign.read_text(encoding="utf-8"))

    def test_install_cli_replaces_launcher_it_owns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            bindir = home / "bin"
            bindir.mkdir()
            owned = bindir / "fix.cmd"
            owned.write_text(
                '@echo off\n"C:/old/python.exe" "C:/old/agent-fix/scripts/fix.py" %*\n',
                encoding="utf-8",
            )
            with patch.object(Path, "home", return_value=home), patch.object(
                cat, "is_windows", return_value=True
            ):
                hooks._install_cli(Path(hooks.ROOT))
            self.assertIn("fix.py", owned.read_text(encoding="utf-8"))


    def test_provider_apply_write_failure_is_typed_and_masks_key(self) -> None:
        """A write failure must surface as an error status, not a raw OSError,
        and the supplied key must never be echoed."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch.object(Path, "home", return_value=home), patch.object(
                engine_module, "resolve_agent", return_value={"id": "claude-code", "name": "Claude Code"}
            ), patch("tempfile.mkstemp", side_effect=OSError(28, "No space left on device")):
                out = engine_module.provider_text(
                    "deepseek", "claude-code", api_key="sk-ant-api03-LEAKMEPLEASE", apply=True
                )
            self.assertEqual(operation_result.status_of(out, "ok"), "error")
            self.assertNotIn("LEAKMEPLEASE", out)

    def test_run_cmd_masks_subprocess_output(self) -> None:
        """Subprocess output reaches MCP callers, so it must cross the
        secret-masking boundary."""

        class Result:
            returncode = 0
            stdout = "registered with token sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            stderr = None

        with patch.object(hooks.subprocess, "run", return_value=Result()):
            ok, output = hooks._run_cmd(["fake"])
        self.assertTrue(ok)
        self.assertNotIn("sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", output)
        self.assertIn("sk-an***", output)

    def test_run_cmd_masks_subprocess_output_on_failure(self) -> None:
        class Result:
            returncode = 3
            stdout = ""
            stderr = "failed using sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

        with patch.object(hooks.subprocess, "run", return_value=Result()):
            ok, output = hooks._run_cmd(["fake"])
        self.assertFalse(ok)
        self.assertNotIn("sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", output)


if __name__ == "__main__":
    unittest.main()
