from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import tracemalloc
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from agentfix import engine, state


class SecureStateTests(unittest.TestCase):
    @unittest.skipUnless(os.name != "nt" and hasattr(os, "symlink"), "symlinks unavailable")
    def test_chmod_no_follow_does_not_modify_replaced_external_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            outside = root / "outside"
            target.write_text("target", encoding="utf-8")
            outside.write_text("outside", encoding="utf-8")
            os.chmod(str(outside), 0o644)
            real_chmod = os.chmod
            replaced = False

            def swapping_chmod(path, mode, *args, **kwargs):
                nonlocal replaced
                if Path(path) == target and not replaced:
                    replaced = True
                    target.unlink()
                    target.symlink_to(outside)
                return real_chmod(path, mode, *args, **kwargs)

            with patch.object(state.os, "chmod", side_effect=swapping_chmod):
                with self.assertRaises(state.StateError):
                    state._chmod_no_follow(target, 0o700)
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o644)

    def test_atomic_write_reports_temp_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "settings.json"
            real_unlink = Path.unlink
            leaked = []

            def fail_unlink(path, *args, **kwargs):
                if path.name.startswith(".settings.json.") and path.name.endswith(".tmp"):
                    leaked.append(path)
                    raise OSError("temp locked")
                return real_unlink(path, *args, **kwargs)

            with patch.object(state.os, "replace", side_effect=OSError("rename failed")), patch.object(
                Path, "unlink", new=fail_unlink
            ):
                with self.assertRaises(state.StateError) as context:
                    state.atomic_write_text(target, "secret", private=True)
            self.assertIn("cleanup failed", str(context.exception))
            for path in leaked:
                real_unlink(path)

    def test_create_zip_reports_temp_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "backup.zip"
            source = root / "source.txt"
            source.write_text("secret", encoding="utf-8")
            real_unlink = Path.unlink
            leaked = []

            def fail_unlink(path, *args, **kwargs):
                if path.name.startswith(".backup.zip.") and path.name.endswith(".tmp"):
                    leaked.append(path)
                    raise OSError("temp locked")
                return real_unlink(path, *args, **kwargs)

            with patch.object(state.os, "replace", side_effect=OSError("rename failed")), patch.object(
                Path, "unlink", new=fail_unlink
            ):
                with self.assertRaises(state.StateError) as context:
                    state.create_zip_atomic(target, [("source.txt", source)], {})
            self.assertIn("cleanup failed", str(context.exception))
            for path in leaked:
                real_unlink(path)

    def test_atomic_write_replaces_content_without_leaving_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "settings.json"
            state.atomic_write_text(target, '{"ok": true}\n', private=True)
            state.atomic_write_text(target, '{"ok": false}\n', private=True)

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"ok": False})
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_atomic_write_rolls_back_when_temp_is_replaced_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "settings.json"
            target.write_text("old-content-longer", encoding="utf-8")
            real_replace = state.os.replace
            fired = False

            def replace_with_attacker(src, dst, *args, **kwargs):
                nonlocal fired
                if not fired and ".tmp" in str(src) and Path(dst) == target:
                    fired = True
                    Path(src).unlink()
                    Path(src).write_text("attacker-evil", encoding="utf-8")
                return real_replace(src, dst, *args, **kwargs)

            with patch.object(state.os, "replace", side_effect=replace_with_attacker):
                with self.assertRaises(state.StateError):
                    state.atomic_write_text(target, "new", private=True)
            self.assertTrue(fired)
            self.assertEqual(target.read_text(encoding="utf-8"), "old-content-longer")
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_create_zip_rolls_back_when_temp_is_replaced_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "backup.zip"
            target.write_bytes(b"original-archive-bytes")
            source = root / "source.txt"
            source.write_text("payload", encoding="utf-8")
            real_replace = state.os.replace
            fired = False

            def replace_with_attacker(src, dst, *args, **kwargs):
                nonlocal fired
                if not fired and ".tmp" in str(src) and Path(dst) == target:
                    fired = True
                    Path(src).unlink()
                    Path(src).write_text("evil", encoding="utf-8")
                return real_replace(src, dst, *args, **kwargs)

            with patch.object(state.os, "replace", side_effect=replace_with_attacker):
                with self.assertRaises(state.StateError):
                    state.create_zip_atomic(target, [("source.txt", source)], {})
            self.assertTrue(fired)
            self.assertEqual(target.read_bytes(), b"original-archive-bytes")
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_atomic_write_rollback_retries_when_rollback_temp_is_swapped(self) -> None:
        """If the rollback temp is swapped mid-commit, the restore retries and
        still lands the captured pre-write content."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "settings.json"
            target.write_text("old-content", encoding="utf-8")
            real_replace = state.os.replace
            real_assert = state._assert_path_identity
            corrupted = {"done": False}
            rollback_swapped = {"done": False}

            def assert_hook(path, expected):
                if Path(path) == target and not corrupted["done"]:
                    corrupted["done"] = True
                    Path(path).write_text("attacker-content", encoding="utf-8")
                return real_assert(path, expected)

            def racing_replace(src, dst, *args, **kwargs):
                result = real_replace(src, dst, *args, **kwargs)
                if Path(src).name.endswith(".rollback") and Path(dst) == target and not rollback_swapped["done"]:
                    rollback_swapped["done"] = True
                    Path(dst).unlink()
                    Path(dst).write_text("rollback-evil", encoding="utf-8")
                return result

            with patch.object(state.os, "replace", side_effect=racing_replace), patch.object(
                state, "_assert_path_identity", side_effect=assert_hook
            ):
                with self.assertRaises(state.StateError):
                    state.atomic_write_bytes(target, b"new", private=True)
            self.assertTrue(rollback_swapped["done"])
            self.assertEqual(target.read_text(encoding="utf-8"), "old-content")
            self.assertEqual(list(root.glob(".*.rollback")), [])

    def test_atomic_write_rollback_retries_when_rollback_temp_swapped_precommit(self) -> None:
        """A rollback temp swapped before its own commit must be discarded and
        retried rather than raising with attacker content left in the target."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "settings.json"
            target.write_text("old-content", encoding="utf-8")
            real_assert = state._assert_path_identity
            corrupted = {"done": False}
            rollback_swapped = {"done": False}

            def assert_hook(path, expected):
                path_obj = Path(path)
                if path_obj == target and not corrupted["done"]:
                    corrupted["done"] = True
                    path_obj.write_text("attacker-content", encoding="utf-8")
                    return real_assert(path, expected)
                if path_obj.name.endswith(".rollback") and not rollback_swapped["done"]:
                    rollback_swapped["done"] = True
                    # Swap the rollback temp right before it would be committed.
                    path_obj.unlink()
                    path_obj.write_text("rollback-evil", encoding="utf-8")
                return real_assert(path, expected)

            with patch.object(state, "_assert_path_identity", side_effect=assert_hook):
                with self.assertRaises(state.StateError):
                    state.atomic_write_bytes(target, b"new", private=True)
            self.assertTrue(rollback_swapped["done"])
            self.assertEqual(target.read_text(encoding="utf-8"), "old-content")
            self.assertEqual(list(root.glob(".*.rollback")), [])

    @unittest.skipUnless(hasattr(os, "link"), "hardlinks unsupported")
    def test_atomic_write_rollback_does_not_write_through_hardlink(self) -> None:
        """A hardlink planted at the target during rollback must not let the
        restore write through to the external inode."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "settings.json"
            target.write_text("old", encoding="utf-8")
            outside = root / "outside-secret.txt"
            outside.write_text("outside-secret", encoding="utf-8")
            real_replace = state.os.replace
            fired = False

            def replace_with_attacker_then_link(src, dst, *args, **kwargs):
                nonlocal fired
                result = real_replace(src, dst, *args, **kwargs)
                if not fired and ".tmp" in str(src) and Path(dst) == target:
                    fired = True
                    # Replace target with a hardlink to an external file, then
                    # let the post-commit verification fail and roll back.
                    target.unlink()
                    os.link(str(outside), str(target))
                return result

            with patch.object(state.os, "replace", side_effect=replace_with_attacker_then_link):
                with self.assertRaises(state.StateError):
                    state.atomic_write_text(target, "new", private=True)
            self.assertTrue(fired)
            # The external file must be untouched by the rollback.
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside-secret")
            # The target must be restored to the pre-write content.
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_directory_swap_rolls_back_when_stage_is_replaced_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stage = root / ".target.stage-test"
            target = root / "target"
            stage.mkdir()
            (stage / "expected.txt").write_text("ok", encoding="utf-8")
            target.mkdir()
            (target / "original.txt").write_text("old", encoding="utf-8")
            real_replace = state.os.replace
            fired = False

            def replace_with_attacker_dir(src, dst, *args, **kwargs):
                nonlocal fired
                src_path = Path(src)
                if not fired and src_path.name.startswith(".target.stage-"):
                    fired = True
                    src_path.rename(root / "moved-stage")
                    attacker = root / ".target.stage-attacker"
                    attacker.mkdir()
                    (attacker / "evil.txt").write_text("evil", encoding="utf-8")
                    src_path.symlink_to(attacker, target_is_directory=True)
                return real_replace(src, dst, *args, **kwargs)

            with patch.object(state.os, "replace", side_effect=replace_with_attacker_dir):
                with self.assertRaises((state.StateError, OSError)):
                    state.atomic_replace_directory(stage, target)
            self.assertTrue(fired)
            self.assertFalse((target / "evil.txt").exists())
            self.assertTrue((target / "original.txt").exists())
            self.assertEqual((target / "original.txt").read_text(encoding="utf-8"), "old")
            self.assertEqual(
                sorted(p.name for p in target.iterdir()), ["original.txt"]
            )
            self.assertFalse((root / ".target.swap.json").exists())

    def test_directory_swap_recovery_rejects_unverified_crash_window_target(self) -> None:
        """A crash between the stage rename and the marker update must not adopt
        an unverified target; recovery restores the backup instead."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            backup = root / ".target.old-crash"
            # Simulate the crash window: the old target sits in the backup, and
            # an unverified object occupies the target while the stage is gone.
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            target.mkdir()
            (target / "evil.txt").write_text("evil", encoding="utf-8")
            marker = state._swap_marker_path(target)
            backup_identity = state._dir_identity(backup)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": -1,
                        "stage_ino": -1,
                        "stage_sig": "0" * 32,
                        "backup": str(backup),
                        "backup_dev": backup_identity[0],
                        "backup_ino": backup_identity[1],
                        "backup_sig": backup_identity[2],
                        "state": "old-moved",
                    }
                ),
                encoding="utf-8",
            )
            recovery = state._recover_directory_swap(target)
            self.assertEqual(recovery, ("rolled_back", False))
            self.assertFalse((target / "evil.txt").exists())
            self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse(backup.exists())
            self.assertFalse(marker.exists())

    def test_directory_swap_recovery_accepts_identity_verified_crash_window(self) -> None:
        """A genuine crash-window install keeps the target when the recorded
        stage identity still matches what landed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            backup = root / ".target.old-crash"
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            stage_identity = state._dir_identity(stage)
            stage.rename(target)  # the stage really did land on the target
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            marker = state._swap_marker_path(target)
            backup_identity = state._dir_identity(backup)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": stage_identity[0],
                        "stage_ino": stage_identity[1],
                        "stage_sig": stage_identity[2],
                        "backup": str(backup),
                        "backup_dev": backup_identity[0],
                        "backup_ino": backup_identity[1],
                        "backup_sig": backup_identity[2],
                        "state": "old-moved",
                    }
                ),
                encoding="utf-8",
            )
            recovery = state._recover_directory_swap(target)
            self.assertEqual(recovery[0], "installed")
            self.assertTrue((target / "new.txt").exists())
            self.assertFalse(backup.exists())
            self.assertFalse(marker.exists())

    def test_directory_swap_recovery_rejects_unverified_prepared_no_backup(self) -> None:
        """With no old target to restore, an unverified crash-window target must
        be refused and the marker preserved for diagnosis."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            target.mkdir()
            (target / "evil.txt").write_text("evil", encoding="utf-8")
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": -1,
                        "stage_ino": -1,
                        "stage_sig": "0" * 32,
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)
            # Fail closed: the unverified target stays, but the marker remains
            # so the state is diagnosable rather than silently adopted.
            self.assertTrue(marker.exists())
            self.assertTrue((target / "evil.txt").exists())

    def test_directory_swap_recovery_accepts_verified_prepared_no_backup(self) -> None:
        """The same shape is accepted when the target still carries the stage
        identity recorded before the commit."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            stage_identity = state._dir_identity(stage)
            stage.rename(target)
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": stage_identity[0],
                        "stage_ino": stage_identity[1],
                        "stage_sig": stage_identity[2],
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            recovery = state._recover_directory_swap(target)
            self.assertEqual(recovery, ("installed", False))
            self.assertTrue((target / "new.txt").exists())
            self.assertFalse(marker.exists())

    def test_directory_swap_reverts_when_target_replaced_after_new_installed_marker(self) -> None:
        """A target swapped after the new-installed marker is written must not
        survive with the old backup deleted."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stage = root / ".target.stage-test"
            target = root / "target"
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            real_atomic_write_json = state.atomic_write_json
            fired = False

            def swap_after_marker(path, data, **kwargs):
                nonlocal fired
                result = real_atomic_write_json(path, data, **kwargs)
                if not fired and isinstance(data, dict) and data.get("state") == "new-installed":
                    fired = True
                    state._remove_path(target)
                    attacker = root / "attacker"
                    attacker.mkdir()
                    (attacker / "evil.txt").write_text("evil", encoding="utf-8")
                    os.replace(str(attacker), str(target))
                return result

            with patch.object(state, "atomic_write_json", side_effect=swap_after_marker):
                with self.assertRaises((state.StateError, OSError)):
                    state.atomic_replace_directory(stage, target)
            self.assertTrue(fired)
            self.assertFalse((target / "evil.txt").exists())
            self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse((root / ".target.swap.json").exists())

    def test_directory_swap_recovery_rejects_unverified_new_installed_target(self) -> None:
        """Recovery must verify a new-installed target before deleting backup."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            backup = root / ".target.old-newinstalled"
            target.mkdir()
            (target / "evil.txt").write_text("evil", encoding="utf-8")
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            marker = state._swap_marker_path(target)
            backup_identity = state._dir_identity(backup)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": -1,
                        "stage_ino": -1,
                        "stage_sig": "0" * 32,
                        "backup": str(backup),
                        "backup_dev": backup_identity[0],
                        "backup_ino": backup_identity[1],
                        "backup_sig": backup_identity[2],
                        "state": "new-installed",
                    }
                ),
                encoding="utf-8",
            )
            recovery = state._recover_directory_swap(target)
            self.assertEqual(recovery, ("rolled_back", False))
            self.assertFalse((target / "evil.txt").exists())
            self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse(marker.exists())

    def test_directory_swap_marker_rejects_partial_stage_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            stage.mkdir()
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": 5,
                        "stage_sig": "0" * 32,
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)

    def test_windows_junction_detection_contract(self) -> None:
        with patch.object(Path, "is_junction", return_value=True, create=True):
            self.assertTrue(state._is_link_or_reparse(Path("junction")))

    def test_windows_reparse_attribute_detection_contract(self) -> None:
        class ReparseInfo:
            st_file_attributes = 0x400

        with patch.object(Path, "is_symlink", return_value=False), patch.object(
            state.os, "lstat", return_value=ReparseInfo()
        ):
            self.assertTrue(state._is_link_or_reparse(Path("reparse")))

    def test_directory_swap_rejects_reparse_target_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stage = root / ".target.stage-test"
            target = root / "target"
            stage.mkdir()
            target.mkdir()
            original = state._is_link_or_reparse

            def fake_reparse(path: Path) -> bool:
                return Path(path) == target or original(path)

            with patch.object(state, "_is_link_or_reparse", side_effect=fake_reparse):
                with self.assertRaises(state.StateError):
                    state.atomic_replace_directory(stage, target)
            self.assertTrue(stage.exists())
            self.assertTrue(target.exists())
            self.assertFalse(state._swap_marker_path(target).exists())

    @unittest.skipUnless(os.name == "nt", "real Windows junction test")
    def test_real_windows_junction_is_refused_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            junction = root / "junction"
            target.mkdir()
            env = os.environ.copy()
            env["AGENTFIX_JUNCTION_PATH"] = str(junction)
            env["AGENTFIX_JUNCTION_TARGET"] = str(target)
            created = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:AGENTFIX_JUNCTION_PATH -Target $env:AGENTFIX_JUNCTION_TARGET | Out-Null",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            if created.returncode != 0:
                self.skipTest("junction creation unavailable")
            try:
                with self.assertRaises(state.StateError):
                    state.ensure_safe_path(junction)
                stage = root / ".target.stage-test"
                stage.mkdir()
                with self.assertRaises(state.StateError):
                    state.atomic_replace_directory(stage, junction)
                self.assertTrue(junction.exists())
                self.assertTrue(stage.exists())
                self.assertFalse(state._swap_marker_path(junction).exists())
            finally:
                subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "Remove-Item -LiteralPath $env:AGENTFIX_JUNCTION_PATH -Force -ErrorAction SilentlyContinue",
                    ],
                    capture_output=True,
                    env=env,
                    timeout=30,
                )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_atomic_write_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "real.txt"
            link = root / "link.txt"
            real.write_text("original", encoding="utf-8")
            try:
                link.symlink_to(real)
            except OSError:
                self.skipTest("symlink creation unavailable")

            with self.assertRaises(state.StateError):
                state.atomic_write_text(link, "changed", private=True)
            self.assertEqual(real.read_text(encoding="utf-8"), "original")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_directory_swap_marker_recovers_before_next_install(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            backup = root / ".target.old-crash"
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            marker = root / ".target.swap.json"
            stage_identity = state._dir_identity(stage)
            backup_identity = state._dir_identity(backup)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": stage_identity[0],
                        "stage_ino": stage_identity[1],
                        "stage_sig": stage_identity[2],
                        "backup": str(backup),
                        "backup_dev": backup_identity[0],
                        "backup_ino": backup_identity[1],
                        "backup_sig": backup_identity[2],
                        "state": "old-moved",
                    }
                ),
                encoding="utf-8",
            )
            state.atomic_replace_directory(stage, target)
            self.assertTrue((target / "new.txt").exists())
            self.assertFalse(backup.exists())
            self.assertFalse(marker.exists())

    def test_directory_swap_recovers_prepared_backup_then_continues_install(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            backup = root / ".target.old-test"
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            marker = state._swap_marker_path(target)
            # Crash between the old-target rename and the old-moved marker
            # update: a prepared marker proves the backup via the old target's
            # identity, which was recorded before the rename.
            backup_identity = state._dir_identity(backup)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": state._dir_identity(stage)[0],
                        "stage_ino": state._dir_identity(stage)[1],
                        "stage_sig": state._dir_identity(stage)[2],
                        "backup": str(backup),
                        "old_target_dev": backup_identity[0],
                        "old_target_ino": backup_identity[1],
                        "old_target_sig": backup_identity[2],
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            state.atomic_replace_directory(stage, target)
            self.assertTrue((target / "new.txt").exists())
            self.assertFalse(stage.exists())
            self.assertFalse(backup.exists())
            self.assertFalse(marker.exists())

    def test_directory_swap_promotes_prepared_stage_and_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            os.chmod(str(stage), 0o755)
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            state.atomic_replace_directory(stage, target)
            self.assertTrue((target / "new.txt").exists())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(target.stat().st_mode) & 0o777, 0o700)
            self.assertFalse(stage.exists())
            self.assertFalse(marker.exists())

    def test_directory_swap_completed_old_operation_does_not_skip_new_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            old_stage = root / ".target.stage-old"
            new_stage = root / ".target.stage-new"
            backup = root / ".target.old-test"
            target.mkdir()
            (target / "old-installed.txt").write_text("old", encoding="utf-8")
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            new_stage.mkdir()
            (new_stage / "new.txt").write_text("new", encoding="utf-8")
            marker = state._swap_marker_path(target)
            # The old operation's stage was renamed onto the target, so the
            # recorded stage identity is now the target's own identity.
            installed_identity = state._dir_identity(target)
            backup_identity = state._dir_identity(backup)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "old",
                        "target": str(target),
                        "stage": str(old_stage),
                        "stage_dev": installed_identity[0],
                        "stage_ino": installed_identity[1],
                        "stage_sig": installed_identity[2],
                        "backup": str(backup),
                        "backup_dev": backup_identity[0],
                        "backup_ino": backup_identity[1],
                        "backup_sig": backup_identity[2],
                        "state": "old-moved",
                    }
                ),
                encoding="utf-8",
            )
            state.atomic_replace_directory(new_stage, target)
            self.assertTrue((target / "new.txt").exists())
            self.assertFalse(new_stage.exists())
            self.assertFalse(backup.exists())
            self.assertFalse(marker.exists())

    def test_directory_swap_prepared_target_with_missing_stage_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            stage = root / ".target.stage-test"
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state.atomic_replace_directory(stage, target)
            self.assertTrue((target / "old.txt").exists())

    def test_directory_swap_recovery_rejects_windows_alias_marker_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": ".",
                        "target": str(target),
                        "stage": str(root / ".target.stage-."),
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "good",
                        "target": str(target),
                        "stage": str(root / ".target.stage-good"),
                        "backup": str(root / ".target.old-.."),
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)

    def test_directory_swap_rejects_nul_in_marker_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "bad\x00token",
                        "target": str(target),
                        "stage": str(root / ".target.stage-bad\x00token"),
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)

    def test_directory_swap_recovery_rejects_marker_without_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "backup": None,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)
            self.assertTrue(stage.exists())
            self.assertTrue(marker.exists())

    def test_directory_swap_does_not_overwrite_preexisting_marker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            target.mkdir()
            stage.mkdir()
            marker = state._swap_marker_path(target)
            marker.write_text("user-owned", encoding="utf-8")
            with self.assertRaises(state.StateError):
                state.atomic_replace_directory(stage, target)
            self.assertEqual(marker.read_text(encoding="utf-8"), "user-owned")
            self.assertTrue(stage.exists())
            self.assertTrue(target.exists())

    def test_directory_swap_recovery_rejects_non_directory_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            bad_stage = root / ".target.stage-file"
            bad_stage.write_text("not a directory", encoding="utf-8")
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "file",
                        "target": str(target),
                        "stage": str(bad_stage),
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)
            self.assertFalse(target.exists())
            self.assertTrue(marker.exists())
            self.assertTrue(bad_stage.is_file())

    def test_directory_swap_recovery_rejects_foreign_sibling_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            foreign = root / "foreign"
            foreign.mkdir()
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "foreign",
                        "target": str(target),
                        "stage": str(foreign),
                        "backup": None,
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)
            self.assertFalse(target.exists())
            self.assertTrue(foreign.exists())
            self.assertTrue(marker.exists())

    def test_backup_budget_includes_manifest_member(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "settings.json"
            source.write_text("{}", encoding="utf-8")
            with self.assertRaises(state.StateError):
                state.create_zip_atomic(
                    root / "too-many.zip",
                    [("00-config/settings.json", source)],
                    {"00-config": str(root / "config")},
                    max_files=1,
                )
            with self.assertRaises(state.StateError):
                state.create_zip_atomic(
                    root / "too-large.zip",
                    [("00-config/settings.json", source)],
                    {"00-config": str(root / "config")},
                    max_bytes=1,
                )

            manifest = {"00-config": str(root / "config")}
            legal = state.create_zip_atomic(
                root / "legal.zip",
                [("00-config/settings.json", source)],
                manifest,
                max_files=2,
                max_bytes=len(json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")) + 2,
            )
            state.inspect_zip(legal)

    def test_directory_swap_retains_marker_when_poststage_marker_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            real_write = state.atomic_write_json
            calls = []

            def flaky_write(path, data, private=True):
                calls.append(str(path))
                if len(calls) == 3:
                    raise OSError("final marker failed")
                return real_write(path, data, private=private)

            with patch.object(state, "atomic_write_json", side_effect=flaky_write):
                with self.assertRaises(OSError):
                    state.atomic_replace_directory(stage, target)
            self.assertEqual((target / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertTrue(state._swap_marker_path(target).exists())
            self.assertTrue(any(root.glob(".target.old-*")))

    def test_directory_swap_rolls_back_on_value_error_marker_update(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            real_write = state.atomic_write_json
            calls = []

            def flaky_write(path, data, private=True):
                calls.append(str(path))
                if len(calls) == 2:
                    raise ValueError("marker update failed")
                return real_write(path, data, private=private)

            with patch.object(state, "atomic_write_json", side_effect=flaky_write):
                with self.assertRaises(ValueError):
                    state.atomic_replace_directory(stage, target)
            self.assertTrue((target / "old.txt").exists())
            self.assertFalse(any(root.glob(".target.old-*")))
            self.assertFalse(state._swap_marker_path(target).exists())
            self.assertTrue(stage.exists())

    def test_directory_swap_recovery_rejects_inconsistent_objects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            backup = root / ".target.old-test"
            for path in (target, stage, backup):
                path.mkdir()
                (path / "data.txt").write_text(path.name, encoding="utf-8")
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "backup": str(backup),
                        "state": "old-moved",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)
            self.assertTrue(target.exists())
            self.assertTrue(stage.exists())
            self.assertTrue(backup.exists())
            self.assertTrue(marker.exists())

    def test_directory_swap_recovery_preserves_file_target_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            backup = root / ".target.old-test"
            target.write_text("target-file", encoding="utf-8")
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": "test",
                        "target": str(target),
                        "stage": str(stage),
                        "backup": str(backup),
                        "state": "old-moved",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)
            self.assertEqual(target.read_text(encoding="utf-8"), "target-file")
            self.assertTrue(backup.exists())
            self.assertTrue(marker.exists())

    def test_directory_swap_retains_marker_when_postcommit_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            with patch.object(state, "_remove_path", side_effect=OSError("cleanup locked")):
                with self.assertRaises(OSError):
                    state.atomic_replace_directory(stage, target)
            self.assertEqual((target / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertTrue(state._swap_marker_path(target).exists())
            self.assertTrue(any(root.glob(".target.old-*")))

    def test_atomic_directory_swap_removes_stale_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            target.mkdir()
            (target / "stale.txt").write_text("old", encoding="utf-8")
            stage = root / ".target.stage-test"
            stage.mkdir()
            (stage / "fresh.txt").write_text("new", encoding="utf-8")
            state.atomic_replace_directory(stage, target)
            self.assertFalse((target / "stale.txt").exists())
            self.assertEqual((target / "fresh.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse(stage.exists())

    def test_atomic_write_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real_dir = root / "real"
            link_dir = root / "link"
            real_dir.mkdir()
            try:
                link_dir.symlink_to(real_dir, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")

            with self.assertRaises(state.StateError):
                state.atomic_write_text(link_dir / "settings.json", "changed", private=True)
            self.assertFalse((real_dir / "settings.json").exists())

    def test_private_write_clamps_group_and_other_permissions(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows does not expose POSIX permission bits")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "secret.txt"
            target.write_text("old", encoding="utf-8")
            try:
                os.chmod(target, 0o744)
            except OSError:
                self.skipTest("filesystem does not support POSIX modes")
            state.atomic_write_text(target, "new", private=True)
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode & 0o077, 0)
            self.assertEqual(mode & 0o100, 0o100)
            readonly = Path(td) / "readonly.txt"
            readonly.write_text("old", encoding="utf-8")
            os.chmod(readonly, 0o400)
            state.atomic_write_text(readonly, "new", private=True)
            self.assertEqual(stat.S_IMODE(readonly.stat().st_mode) & 0o600, 0o600)

    def test_engine_walk_does_not_read_symlink_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outside = root.parent / "outside-secret.txt"
            outside.write_text("secret", encoding="utf-8")
            link = root / "linked.txt"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation unavailable")
            try:
                files = engine._walk_files(root, max_depth=2, suffixes={".txt"})
                self.assertNotIn(link, files)
            finally:
                outside.unlink(missing_ok=True)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_snapshot_targets_rejects_dangling_symlink_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            link = root / "config"
            try:
                link.symlink_to(root / "missing", target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with patch.object(engine, "resolve_agent", return_value={"id": "alpha", "config": link.as_posix()}):
                with self.assertRaises(state.StateError):
                    engine._snapshot_targets("alpha")

    def test_audit_refuses_symlinked_config_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "real"
            link = root / "config"
            real.mkdir()
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with patch.object(engine, "resolve_agent", return_value={"id": "alpha", "name": "Alpha", "config": link.as_posix()}):
                result = engine.audit_result("alpha")
            self.assertEqual(result["status"], "INCONCLUSIVE")
            self.assertIn("symlink", result["reason"].lower())

    def test_backup_text_converts_walk_refusal_to_stable_result(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", root / "backups"), patch.object(
                engine, "resolve_agent", return_value=agent
            ), patch.object(engine, "_iter_backup_files", side_effect=state.StateError("nested reparse")):
                result = engine.backup_text("alpha")
            self.assertIn("backup refused: nested reparse", result)

    def test_backup_walk_errors_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            base.mkdir(exist_ok=True)

            def fake_walk(path, onerror=None):
                onerror(OSError("walk denied"))
                return []

            with patch.object(engine.os, "walk", side_effect=fake_walk):
                with self.assertRaises(OSError):
                    engine._iter_backup_files(base)

    def test_backup_walk_rejects_nested_reparse_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            nested = base / "nested"
            nested.mkdir()
            original = state._is_link_or_reparse

            def fake_reparse(path: Path) -> bool:
                return Path(path) == nested or original(path)

            with patch.object(state, "_is_link_or_reparse", side_effect=fake_reparse), patch.object(
                engine.os, "walk", return_value=[(str(base), ["nested"], [])]
            ):
                with self.assertRaises(state.StateError):
                    engine._iter_backup_files(base)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_backup_walk_rejects_nested_symlink_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            outside = base.parent / "agentfix-backup-outside.txt"
            outside.write_text("secret", encoding="utf-8")
            link = base / "linked.txt"
            try:
                link.symlink_to(outside)
            except OSError:
                outside.unlink(missing_ok=True)
                self.skipTest("symlink creation unavailable")
            try:
                with self.assertRaises(state.StateError):
                    engine._iter_backup_files(base)
            finally:
                outside.unlink(missing_ok=True)

    def test_audit_walk_marks_nested_reparse_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            nested = base / "nested"
            nested.mkdir()
            original = state._is_link_or_reparse

            def fake_reparse(path: Path) -> bool:
                return Path(path) == nested or original(path)

            skipped = []
            with patch.object(state, "_is_link_or_reparse", side_effect=fake_reparse), patch.object(
                engine.os, "walk", return_value=[(str(base), ["nested"], [])]
            ):
                files = engine._walk_files(base, skipped=skipped)
            self.assertEqual(files, [])
            self.assertEqual(skipped, [nested])

    def test_audit_result_is_inconclusive_for_nested_reparse(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            nested = base / "nested"
            nested.mkdir()
            original = state._is_link_or_reparse

            def fake_reparse(path: Path) -> bool:
                return Path(path) == nested or original(path)

            agent = {"id": "alpha", "name": "Alpha", "config": base.as_posix()}
            with patch.object(engine, "resolve_agent", return_value=agent), patch.object(
                state, "_is_link_or_reparse", side_effect=fake_reparse
            ), patch.object(engine.os, "walk", return_value=[(str(base), ["nested"], [])]):
                result = engine.audit_result("alpha")
            self.assertEqual(result["status"], "INCONCLUSIVE")
            self.assertEqual(result["symlink_count"], 1)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_audit_walk_skips_fifo_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            fifo = base / "config.json"
            os.mkfifo(str(fifo))
            skipped = []
            self.assertEqual(engine._walk_files(base, skipped=skipped), [])
            self.assertEqual(skipped, [fifo])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_backup_walk_rejects_fifo_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            fifo = base / "config.json"
            os.mkfifo(str(fifo))
            with self.assertRaises(state.StateError):
                engine._iter_backup_files(base)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_regular_source_rejects_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fifo = Path(td) / "config.json"
            os.mkfifo(str(fifo))
            with self.assertRaises(state.StateError):
                state._read_regular_source(fifo, 1024)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_directory_swap_rejects_fifo_marker_without_reading(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-test"
            stage.mkdir()
            marker = state._swap_marker_path(target)
            os.mkfifo(str(marker))
            with self.assertRaises(state.StateError):
                state._recover_directory_swap(target)

    @unittest.skipUnless(os.name != "nt" and hasattr(os, "symlink"), "symlinks unavailable")
    def test_logs_reports_incomplete_reparse_scan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "node_modules").symlink_to(base / "outside", target_is_directory=True)
            with patch.object(engine.cat, "config_path", return_value=str(base)), patch.object(
                engine, "resolve_agent", return_value={"name": "Test Agent"}
            ):
                result = engine.logs_text("test-agent")
            self.assertIn("scan incomplete", result)

    def test_audit_walk_marks_noise_name_reparse_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            noise = base / "node_modules"
            noise.mkdir()
            original = state._is_link_or_reparse

            def fake_reparse(path: Path) -> bool:
                return Path(path) == noise or original(path)

            skipped = []
            with patch.object(state, "_is_link_or_reparse", side_effect=fake_reparse), patch.object(
                engine.os, "walk", return_value=[(str(base), ["node_modules"], [])]
            ):
                files = engine._walk_files(base, skipped=skipped)
            self.assertEqual(files, [])
            self.assertEqual(skipped, [noise])

    def test_backup_walk_checks_reparse_at_depth_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            child = base / "nested"
            child.mkdir()
            original = state._is_link_or_reparse

            def fake_reparse(path: Path) -> bool:
                return Path(path) == child or original(path)

            with patch.object(state, "_is_link_or_reparse", side_effect=fake_reparse), patch.object(
                engine.os, "walk", return_value=[(str(base), ["nested"], [])]
            ):
                with self.assertRaises(state.StateError):
                    engine._iter_backup_files(base, max_depth=0)

    def test_backup_walk_rejects_noise_name_reparse(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            noise = base / "node_modules"
            noise.mkdir()
            original = state._is_link_or_reparse

            def fake_reparse(path: Path) -> bool:
                return Path(path) == noise or original(path)

            with patch.object(state, "_is_link_or_reparse", side_effect=fake_reparse), patch.object(
                engine.os, "walk", return_value=[(str(base), ["node_modules"], [])]
            ):
                with self.assertRaises(state.StateError):
                    engine._iter_backup_files(base)

    def test_provider_settings_use_secure_writer_and_preserve_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"keep": {"x": 1}, "env": {"OLD": "1"}}), encoding="utf-8")
            with patch.object(engine.Path, "home", return_value=root):
                result = engine._apply_provider_settings(
                    "https://api.example.com", "secret-key", "model-x"
                )
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertIn("wrote", result)
            self.assertEqual(data["keep"], {"x": 1})
            self.assertEqual(data["env"]["OLD"], "1")
            self.assertEqual(data["env"]["ANTHROPIC_MODEL"], "model-x")

    def test_zip_inspection_rejects_oversized_central_directory_before_parsing(self) -> None:
        """The entry budget must bound the parser, not just be checked after
        zipfile has already materialised every central-directory record."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.txt"
            source.write_text("x", encoding="utf-8")
            # A normal small archive first, to prove the preflight does not
            # reject valid input.
            good = root / "good.zip"
            state.create_zip_atomic(good, [("source.txt", source)], {"00-config": str(root)})
            state.inspect_zip(good, max_files=2000)

            bomb = root / "bomb.zip"
            with zipfile.ZipFile(bomb, "w") as zf:
                for index in range(60000):
                    zf.writestr(f"f{index}.txt", b"")

            tracemalloc.start()
            try:
                with self.assertRaises(state.StateError) as context:
                    state.inspect_zip(bomb, max_files=2000)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertIn("file budget", str(context.exception))
            # The preflight must refuse from the EOCD alone; materialising the
            # central directory would cost tens of megabytes.
            self.assertLess(peak, 5 * 1024 * 1024, f"peak allocation was {peak} bytes")

    def test_directory_swap_detects_inode_reuse_after_install(self) -> None:
        """Deleting the installed target frees its inode, so a replacement can
        be handed the same inode number; the entry signature must still catch it."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stage = root / ".target.stage-test"
            target = root / "target"
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            real_atomic_write_json = state.atomic_write_json

            def swap_after_marker(path, data, **kwargs):
                result = real_atomic_write_json(path, data, **kwargs)
                if data.get("state") == "new-installed":
                    state._remove_path(target)
                    attacker = root / "attacker"
                    attacker.mkdir()
                    (attacker / "evil.txt").write_text("evil", encoding="utf-8")
                    os.replace(str(attacker), str(target))
                return result

            with patch.object(state, "atomic_write_json", side_effect=swap_after_marker):
                with self.assertRaises(state.StateError):
                    state.atomic_replace_directory(stage, target)
            self.assertFalse((target / "evil.txt").exists())
            self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old")

    def test_zip_inspection_rejects_forged_eocd_central_directory_bounds(self) -> None:
        """A forged central-directory offset/size must not skip the scan and
        hand the archive to the full parser."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bomb = root / "bomb.zip"
            with zipfile.ZipFile(bomb, "w") as zf:
                for index in range(10000):
                    zf.writestr(f"f{index}.txt", b"")
            raw = bytearray(bomb.read_bytes())
            eocd = raw.rfind(b"PK\x05\x06")
            self.assertGreaterEqual(eocd, 0)
            real_size = int.from_bytes(raw[eocd + 12 : eocd + 16], "little")
            real_offset = int.from_bytes(raw[eocd + 16 : eocd + 20], "little")
            cases = {
                "offset_zero": (1, real_size, 0),
                "offset_past_end": (1, real_size, len(raw)),
                "size_zero": (5, 0, real_offset),
                "count_at_limit_offset_zero": (2000, real_size, 0),
            }
            for label, (count, size, offset) in cases.items():
                with self.subTest(case=label):
                    mutated = bytearray(raw)
                    mutated[eocd + 10 : eocd + 12] = count.to_bytes(2, "little")
                    mutated[eocd + 12 : eocd + 16] = size.to_bytes(4, "little")
                    mutated[eocd + 16 : eocd + 20] = offset.to_bytes(4, "little")
                    path = root / f"{label}.zip"
                    path.write_bytes(bytes(mutated))
                    tracemalloc.start()
                    try:
                        with self.assertRaises(state.StateError):
                            state.inspect_zip(path, max_files=2000)
                        _, peak = tracemalloc.get_traced_memory()
                    finally:
                        tracemalloc.stop()
                    self.assertLess(peak, 5 * 1024 * 1024, f"peak allocation was {peak} bytes")

    @unittest.skipUnless(os.name != "nt", "POSIX permission bits")
    def test_directory_swap_tightens_permissions_in_crash_windows(self) -> None:
        """Neither the crash-window backup nor the installed target may keep
        world-readable permissions."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stage = root / ".target.stage-test"
            target = root / "target"
            stage.mkdir(mode=0o755)
            (stage / "new.txt").write_text("n", encoding="utf-8")
            target.mkdir(mode=0o755)
            (target / "secret.txt").write_text("secret", encoding="utf-8")

            def crash(_path):
                raise KeyboardInterrupt("crash before cleanup")

            with patch.object(state, "_remove_path", side_effect=crash):
                with self.assertRaises(KeyboardInterrupt):
                    state.atomic_replace_directory(stage, target)

            backups = [p for p in root.iterdir() if p.name.startswith(".target.old-")]
            self.assertEqual(len(backups), 1)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    @unittest.skipUnless(os.name != "nt", "POSIX permission bits")
    def test_directory_swap_installs_private_target_from_world_readable_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stage = root / ".target.stage-test"
            target = root / "target"
            stage.mkdir(mode=0o755)
            (stage / "new.txt").write_text("n", encoding="utf-8")
            state.atomic_replace_directory(stage, target)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertTrue((target / "new.txt").exists())

    def test_mask_secrets_covers_bare_token_forms(self) -> None:
        """Agent configs routinely carry {"token": ...}; that form must not
        escape the masking boundary."""
        from agentfix import report as rep

        secret = "SUPERSECRETVALUE123"
        forms = [
            f"token={secret}",
            f'{{"token": "{secret}"}}',
            f"token: {secret}",
            f"export TOKEN={secret}",
            f"?token={secret}",
            f'{{"api_key": "{secret}"}}',
            f"api_key={secret}",
            f'{{"secret": "{secret}"}}',
            f"Authorization: Bearer {secret}",
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertNotIn(secret, rep.mask_secrets(form))
        # Ordinary prose must not be mangled.
        for text in ("no secret here at all", "config file loaded", "the key is rotated"):
            with self.subTest(text=text):
                self.assertEqual(rep.mask_secrets(text), text)

    def test_persistence_apis_reject_nul_paths_with_state_error(self) -> None:
        """An embedded NUL is a typed refusal, never a raw ValueError."""
        bad = Path("bad\x00name")
        calls = {
            "read_archive_bytes": lambda: state.read_archive_bytes(bad),
            "read_backup_layout": lambda: state.read_backup_layout(bad),
            "inspect_zip": lambda: state.inspect_zip(bad),
            "snapshot_zip": lambda: state.snapshot_zip(bad),
            "restore_zip_members": lambda: state.restore_zip_members(bad, {}, []),
            "atomic_write_text": lambda: state.atomic_write_text(bad, "x"),
            "atomic_write_bytes": lambda: state.atomic_write_bytes(bad, b"x"),
            "create_zip_atomic": lambda: state.create_zip_atomic(bad, [], {}),
        }
        for name, call in calls.items():
            with self.subTest(api=name):
                with self.assertRaises(state.StateError):
                    call()
        # The normalizer refuses it before any OS call.
        with self.assertRaises(state.StateError):
            state._normalized_persistent_path(bad)

    def test_persistence_apis_convert_tempfile_failure_to_state_error(self) -> None:
        """Temp creation runs before the guarded block, so its OSError must be
        converted or a raw exception escapes the typed persistence API."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "src.txt"
            source.write_text("x", encoding="utf-8")
            failure = OSError(28, "No space left on device")
            calls = {
                "atomic_write_text": lambda: state.atomic_write_text(root / "a.json", "x"),
                "atomic_write_bytes": lambda: state.atomic_write_bytes(root / "b.json", b"x"),
                "create_zip_atomic": lambda: state.create_zip_atomic(root / "c.zip", [("s.txt", source)], {}),
                "snapshot_zip": lambda: state.snapshot_zip(root / "c.zip"),
            }
            with patch("tempfile.mkstemp", side_effect=failure):
                for name, call in calls.items():
                    with self.subTest(api=name):
                        with self.assertRaises(state.StateError):
                            call()

    def test_restore_transaction_converts_tempfile_failure_to_state_error(self) -> None:
        import io
        import zipfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("payload.txt", "data")
            with patch("tempfile.mkstemp", side_effect=OSError(28, "No space left on device")):
                with self.assertRaises(state.StateError):
                    state.restore_zip_members_bytes(
                        buffer.getvalue(), {"payload.txt": root / "out.txt"}, [root]
                    )

    def test_zip_inspection_rejects_trailing_data_with_forged_count(self) -> None:
        """Trailing bytes after the EOCD must not skip the entry preflight."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bomb = root / "bomb.zip"
            with zipfile.ZipFile(bomb, "w") as zf:
                for index in range(10000):
                    zf.writestr(f"f{index}.txt", b"")
            raw = bytearray(bomb.read_bytes()) + b"X"
            eocd = raw.rfind(b"PK\x05\x06")
            raw[eocd + 10 : eocd + 12] = (1).to_bytes(2, "little")
            path = root / "tail.zip"
            path.write_bytes(bytes(raw))
            tracemalloc.start()
            try:
                with self.assertRaises(state.StateError) as context:
                    state.inspect_zip(path, max_files=2000)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertIn("trailing data", str(context.exception))
            self.assertLess(peak, 5 * 1024 * 1024, f"peak allocation was {peak} bytes")

    def test_atomic_write_detects_parent_directory_redirect(self) -> None:
        """Swapping the parent directory must not silently redirect the write."""
        with tempfile.TemporaryDirectory() as td:
            outer = Path(td)
            work = outer / "work"
            work.mkdir()
            target = work / "state.json"
            target.write_text("old", encoding="utf-8")
            real_replace = state.os.replace
            fired = {"done": False}

            def hijack(src, dst, *args, **kwargs):
                result = real_replace(src, dst, *args, **kwargs)
                if not fired["done"] and Path(str(src)).name.endswith(".tmp") and Path(str(dst)) == target:
                    fired["done"] = True
                    work.rename(outer / "moved")
                    attacker = outer / "work"
                    attacker.mkdir()
                    if Path(str(src)).exists():
                        real_replace(str(src), str(attacker / "state.json"))
                return result

            with patch.object(state.os, "replace", side_effect=hijack):
                with self.assertRaises(state.StateError):
                    state.atomic_write_text(target, "new", private=True)
            self.assertTrue(fired["done"])
            # The write must not have landed in the attacker-controlled parent,
            # and the operation must be reported rather than reported as success.
            self.assertFalse((outer / "work" / "state.json").exists())
            self.assertEqual(
                (outer / "moved" / "state.json").read_text(encoding="utf-8"), "new"
            )

    def test_restore_rollback_refuses_post_commit_directory_replacement(self) -> None:
        """A target replaced by a directory after the commit is reported, not
        deleted, so the partial state stays visible instead of being resolved by
        destroying an unknown object."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.txt"
            source.write_text("new", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(archive, [("payload.txt", source)], {"00-config": str(root)})
            target = root / "restored.txt"
            target.write_text("old", encoding="utf-8")
            real_replace = state.os.replace
            fired = {"done": False}

            def swap_dir(src, dst, *args, **kwargs):
                result = real_replace(src, dst, *args, **kwargs)
                if not fired["done"] and str(src).endswith(".restore") and Path(str(dst)) == target:
                    fired["done"] = True
                    Path(str(dst)).unlink()
                    Path(str(dst)).mkdir()
                    (Path(str(dst)) / "attacker").write_text("x", encoding="utf-8")
                return result

            with patch.object(state.os, "replace", side_effect=swap_dir):
                with self.assertRaises(state.StateError) as context:
                    state.restore_zip_members(archive, {"payload.txt": target}, allowed_roots=[root])
            self.assertTrue(fired["done"])
            self.assertIn("rollback refused", str(context.exception))
            # The unknown directory is left intact rather than removed.
            self.assertTrue(target.is_dir())
            self.assertTrue((target / "attacker").exists())

    def test_recovery_refuses_installer_when_backup_was_replaced(self) -> None:
        """A backup replaced in place must not be installed as the old target."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-x"
            backup = root / ".target.old-x"
            target.mkdir()
            (target / "evil.txt").write_text("evil", encoding="utf-8")
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            marker = state._swap_marker_path(target)
            backup_identity = state._dir_identity(backup)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": state._validate_swap_stage(target, stage),
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": -1,
                        "stage_ino": -1,
                        "stage_sig": "0" * 32,
                        "backup": str(backup),
                        "backup_dev": backup_identity[0],
                        "backup_ino": backup_identity[1],
                        "backup_sig": backup_identity[2],
                        "state": "old-moved",
                    }
                ),
                encoding="utf-8",
            )
            shutil.rmtree(backup)
            backup.mkdir()
            (backup / "attacker").write_text("x", encoding="utf-8")
            with self.assertRaises(state.StateError) as context:
                state._recover_directory_swap(target)
            self.assertIn("backup changed", str(context.exception))
            self.assertTrue((target / "evil.txt").exists())
            self.assertTrue(marker.exists())

    def test_recovery_refuses_old_moved_marker_without_backup(self) -> None:
        """old-moved implies a backup; its absence is a contradiction."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-x"
            target.mkdir()
            (target / "evil.txt").write_text("evil", encoding="utf-8")
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": state._validate_swap_stage(target, stage),
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": -1,
                        "stage_ino": -1,
                        "stage_sig": "0" * 32,
                        "backup": None,
                        "state": "old-moved",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(state.StateError) as context:
                state._recover_directory_swap(target)
            self.assertIn("old-moved", str(context.exception))
            # Nothing is consumed or adopted.
            self.assertTrue(marker.exists())
            self.assertTrue((target / "evil.txt").exists())
            self.assertTrue(stage.exists())

    def test_prepared_recovery_rejects_backup_replaced_by_attacker(self) -> None:
        """The prepared crash window proves the backup via the old target's
        identity; a swapped backup must be refused, not installed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            stage = root / ".target.stage-x"
            backup = root / ".target.old-x"
            backup.mkdir()
            (backup / "old.txt").write_text("old", encoding="utf-8")
            stage.mkdir()
            identity = state._dir_identity(backup)
            marker = state._swap_marker_path(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": state._SWAP_MARKER_OWNER,
                        "token": state._validate_swap_stage(target, stage),
                        "target": str(target),
                        "stage": str(stage),
                        "stage_dev": -1,
                        "stage_ino": -1,
                        "stage_sig": "0" * 32,
                        "backup": str(backup),
                        "old_target_dev": identity[0],
                        "old_target_ino": identity[1],
                        "old_target_sig": identity[2],
                        "state": "prepared",
                    }
                ),
                encoding="utf-8",
            )
            shutil.rmtree(backup)
            backup.mkdir()
            (backup / "attacker").write_text("x", encoding="utf-8")
            with self.assertRaises(state.StateError) as context:
                state._recover_directory_swap(target)
            self.assertIn("backup changed", str(context.exception))
            self.assertFalse(target.exists())
            self.assertTrue(marker.exists())

    def test_restore_zip_members_rejects_device_alias_destinations(self) -> None:
        """Directly supplied destinations get the same alias check as members."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("payload.txt", "data")
            data = buffer.getvalue()
            for relative in ("CON", "COM1", "AUX", "sub/CON", "nul.txt"):
                with self.subTest(component=relative):
                    with self.assertRaises(state.StateError) as context:
                        state.restore_zip_members_bytes(
                            data, {"payload.txt": root / relative}, [root]
                        )
                    self.assertIn("component", str(context.exception))
            # A legitimate destination is still accepted.
            state.restore_zip_members_bytes(data, {"payload.txt": root / "ok.txt"}, [root])
            self.assertTrue((root / "ok.txt").exists())

    def test_restore_rollback_refuses_swapped_parent_directory(self) -> None:
        """A parent renamed away after the commit must not receive the old
        secret when the restore is rolled back."""
        with tempfile.TemporaryDirectory() as td:
            outer = Path(td)
            config = outer / "config"
            config.mkdir()
            target = config / "settings.json"
            target.write_text("OLD-SECRET", encoding="utf-8")
            source = outer / "source.txt"
            source.write_text("NEW", encoding="utf-8")
            archive = outer / "backup.zip"
            state.create_zip_atomic(archive, [("payload.txt", source)], {"00-config": str(outer)})
            real_replace = state.os.replace
            fired = {"done": False}

            def hijack(src, dst, *args, **kwargs):
                result = real_replace(src, dst, *args, **kwargs)
                if not fired["done"] and str(src).endswith(".restore") and Path(str(dst)) == target:
                    fired["done"] = True
                    config.rename(outer / "moved")
                    (outer / "config").mkdir()
                return result

            with patch.object(state.os, "replace", side_effect=hijack):
                with self.assertRaises(state.StateError) as context:
                    state.restore_zip_members(archive, {"payload.txt": target}, allowed_roots=[outer])
            self.assertTrue(fired["done"])
            self.assertIn("rollback", str(context.exception))
            # The previous secret must never be written into the replacement
            # parent, and the committed data stays in the original directory.
            self.assertFalse((outer / "config" / "settings.json").exists())
            self.assertEqual(
                (outer / "moved" / "settings.json").read_text(encoding="utf-8"), "NEW"
            )

    def test_atomic_write_rejects_windows_device_alias_targets(self) -> None:
        """On Windows a reserved device name would redirect the write off the
        validated directory, so every persistence entrypoint refuses it."""
        if os.name != "nt":
            self.skipTest("Windows device aliases only exist on Windows")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for relative in ("CON", "COM1", "AUX", "sub/CON", "nul.txt"):
                with self.subTest(component=relative):
                    with self.assertRaises(state.StateError):
                        state.atomic_write_text(root / relative, "secret", private=True)
                    with self.assertRaises(state.StateError):
                        state.create_zip_atomic(root / relative, [], {})
            # Ordinary names still work.
            state.atomic_write_text(root / "ok.txt", "fine", private=True)
            self.assertEqual((root / "ok.txt").read_text(encoding="utf-8"), "fine")

    def test_restore_rejects_snapshot_replaced_after_verification(self) -> None:
        """Replacing the snapshot after it was verified must be refused, not
        silently restored while the report still claims success."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.txt"
            source.write_text("SAFE", encoding="utf-8")
            good = root / "good.zip"
            state.create_zip_atomic(good, [("payload.txt", source)], {"00-config": str(root)})
            source.write_text("EVIL", encoding="utf-8")
            evil = root / "evil.zip"
            state.create_zip_atomic(evil, [("payload.txt", source)], {"00-config": str(root)})
            snapshot, identity = state.snapshot_zip(good)
            os.replace(evil, snapshot)
            with self.assertRaises(state.StateError):
                state.read_snapshotted_bytes(snapshot, identity)
            # The verified bytes are still readable from an untouched snapshot.
            snapshot2, identity2 = state.snapshot_zip(good)
            with zipfile.ZipFile(io.BytesIO(state.read_snapshotted_bytes(snapshot2, identity2))) as archive:
                self.assertEqual(archive.read("payload.txt"), b"SAFE")

    def test_directory_swap_refuses_rollback_into_swapped_parent(self) -> None:
        """A parent replaced after the commit must not receive the backup."""
        with tempfile.TemporaryDirectory() as td:
            outer = Path(td)
            holder = outer / "hold"
            holder.mkdir()
            target = holder / "target"
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            stage = holder / ".target.stage-x"
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            real_replace = state.os.replace
            fired = {"done": False}

            def hijack(src, dst, *args, **kwargs):
                result = real_replace(src, dst, *args, **kwargs)
                if not fired["done"] and str(src) == str(stage) and Path(str(dst)) == target:
                    fired["done"] = True
                    holder.rename(outer / "moved")
                    (outer / "hold").mkdir()
                return result

            with patch.object(state.os, "replace", side_effect=hijack):
                with self.assertRaises(state.StateError):
                    state.atomic_replace_directory(stage, target)
            self.assertTrue(fired["done"])
            # Nothing may be deposited into the replacement parent.
            self.assertFalse(any((outer / "hold").rglob("*")))
            # The only good copy and the marker stay where they belong.
            moved = outer / "moved"
            backups = list(moved.glob(".target.old-*"))
            self.assertTrue(backups)
            self.assertEqual((backups[0] / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertTrue((moved / ".target.swap.json").exists())

    def test_zip_name_rejects_windows_superscript_device_aliases(self) -> None:
        for name in ("COM³", "LPT²", "COM¹", "LPT³", "dir/COM³.txt", "lpt¹", "aux.txt"):
            with self.subTest(name=name):
                self.assertFalse(state._safe_zip_name(name))
        # Only COM/LPT take a device digit, and superscripts are not device
        # names everywhere, so these stay legal.
        for name in ("COM10", "console.txt", "COM³-party.txt", "CON¹", "AUX²", "settings.json"):
            with self.subTest(name=name):
                self.assertTrue(state._safe_zip_name(name))

    def test_zip_inspection_rejects_forged_eocd_entry_count(self) -> None:
        """An EOCD that understates the entry count must not buy a full parse."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bomb = root / "forged.zip"
            with zipfile.ZipFile(bomb, "w") as zf:
                for index in range(10000):
                    zf.writestr(f"f{index}.txt", b"")
            raw = bytearray(bomb.read_bytes())
            eocd = raw.rfind(b"PK\x05\x06")
            self.assertGreaterEqual(eocd, 0)
            # Forged counts that a size-derived bound cannot see through.
            for forged in (0, 1, 2, 3, 5, 100, 1000, 2000):
                mutated = bytearray(raw)
                mutated[eocd + 10 : eocd + 12] = forged.to_bytes(2, "little")
                path = root / f"forged-{forged}.zip"
                path.write_bytes(bytes(mutated))
                tracemalloc.start()
                try:
                    with self.assertRaises(state.StateError) as context:
                        state.inspect_zip(path, max_files=2000)
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertIn("file budget", str(context.exception))
                self.assertLess(peak, 5 * 1024 * 1024, f"peak allocation was {peak} bytes")

    def test_zip_inspection_rejects_oversized_archive_comment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "comment.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("payload.txt", "ok")
                zf.comment = b"x" * 20000
            with self.assertRaises(state.StateError):
                state.inspect_zip(archive, max_bytes=10000)

    def test_zip_inspection_rejects_oversized_member_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "long-name.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("x" * 5000 + ".txt", "bad")
            with self.assertRaises(state.StateError):
                state.inspect_zip(archive, max_bytes=1024)

    def test_zip_inspection_rejects_traversal_members(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "bad")
            with self.assertRaises(state.StateError):
                state.inspect_zip(archive)

    def test_zip_inspection_rejects_windows_drive_relative_members(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "bad-drive.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("C:evil", "bad")
            with self.assertRaises(state.StateError):
                state.inspect_zip(archive)

    def test_backup_layout_and_manifest_are_owned_by_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "settings.json"
            source.write_text("{}", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(
                archive,
                [("00-config/settings.json", source)],
                {"00-config": str(root / "config")},
            )
            manifest, members = state.read_backup_layout(archive)
            self.assertEqual(manifest, {"00-config": str(root / "config")})
            self.assertEqual(set(members), {"_manifest.json", "00-config/settings.json"})

    def test_restore_zip_members_wraps_crc_failure_as_state_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.txt"
            source.write_bytes(b"payload")
            archive = root / "crc.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
                zf.writestr("payload.txt", source.read_bytes())
            raw = bytearray(archive.read_bytes())
            offset = raw.find(b"payload")
            self.assertGreaterEqual(offset, 0)
            payload_offset = raw.find(b"payload", offset + 1)
            self.assertGreater(payload_offset, offset)
            raw[payload_offset] = ord("P")
            archive.write_bytes(raw)
            with self.assertRaises(state.StateError):
                state.restore_zip_members(
                    archive,
                    {"payload.txt": root / "restored.txt"},
                    allowed_roots=[root],
                )

    def test_restore_zip_members_uses_bounded_archive_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.txt"
            source.write_text("payload", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(archive, [("payload.txt", source)], {"00-config": str(root)})
            restored = state.restore_zip_members(
                archive,
                {"payload.txt": root / "restored.txt"},
                allowed_roots=[root],
            )
            self.assertEqual(restored, [str(root / "restored.txt")])
            self.assertEqual((root / "restored.txt").read_text(encoding="utf-8"), "payload")
            self.assertEqual(list(root.glob(".agentfix-restore-*.zip")), [])

    def test_restore_rolls_back_when_temp_is_replaced_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.txt"
            source.write_text("new", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(archive, [("payload.txt", source)], {"00-config": str(root)})
            target = root / "restored.txt"
            target.write_text("old", encoding="utf-8")
            real_replace = state.os.replace
            fired = False

            def replace_with_attacker(src, dst, *args, **kwargs):
                nonlocal fired
                if not fired and ".restore" in str(src) and Path(dst) == target:
                    fired = True
                    Path(src).unlink()
                    Path(src).write_text("attacker", encoding="utf-8")
                return real_replace(src, dst, *args, **kwargs)

            with patch.object(state.os, "replace", side_effect=replace_with_attacker):
                with self.assertRaises(state.StateError):
                    state.restore_zip_members(
                        archive,
                        {"payload.txt": target},
                        allowed_roots=[root],
                    )
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_restore_zip_members_bounds_existing_rollback_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.txt"
            source.write_text("new", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(archive, [("payload.txt", source)], {"00-config": str(root)})
            target = root / "restored.txt"
            target.write_bytes(b"x" * 2048)
            with self.assertRaises(state.StateError):
                state.restore_zip_members(
                    archive,
                    {"payload.txt": target},
                    allowed_roots=[root],
                    max_bytes=1024,
                )
            self.assertEqual(target.stat().st_size, 2048)

    def test_restore_zip_members_bounds_aggregate_rollback_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_source = root / "first-source.txt"
            second_source = root / "second-source.txt"
            first_source.write_text("new-a", encoding="utf-8")
            second_source.write_text("new-b", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(
                archive,
                [("first.txt", first_source), ("second.txt", second_source)],
                {"00-config": str(root)},
            )
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"a" * 600)
            second.write_bytes(b"b" * 600)
            with self.assertRaises(state.StateError):
                state.restore_zip_members(
                    archive,
                    {"first.txt": first, "second.txt": second},
                    allowed_roots=[root],
                    max_bytes=1000,
                )
            self.assertEqual(first.read_bytes(), b"a" * 600)
            self.assertEqual(second.read_bytes(), b"b" * 600)

    def test_restore_zip_members_rejects_destination_outside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            allowed = root / "allowed"
            allowed.mkdir()
            source = root / "source.txt"
            source.write_text("payload", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(archive, [("payload.txt", source)], {"00-config": str(allowed)})
            outside = root / "outside.txt"
            target_alias = allowed / ".." / outside.name
            try:
                with self.assertRaises(state.StateError):
                    state.restore_zip_members(
                        archive,
                        {"payload.txt": target_alias},
                        allowed_roots=[allowed],
                    )
            finally:
                outside.unlink(missing_ok=True)
            self.assertFalse(outside.exists())

    def test_zip_inspection_rejects_windows_trailing_alias_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for index, member in enumerate(("foo.", "foo ", "NUL.txt")):
                archive = root / f"unsafe-{index}.zip"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr(member, b"payload")
                with self.assertRaises(state.StateError):
                    state.inspect_zip(archive)

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits unavailable")
    def test_restore_preserves_owner_execute_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "settings.json"
            target.write_text("old", encoding="utf-8")
            os.chmod(target, 0o700)
            state._restore_transaction([(target, b"new")])
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode & 0o077, 0)
            self.assertEqual(mode & 0o100, 0o100)

    def test_zip_restore_rolls_back_prior_members_on_commit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_a = root / "a.txt"
            source_b = root / "b.txt"
            source_a.write_text("new-a", encoding="utf-8")
            source_b.write_text("new-b", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(
                archive,
                [("a.txt", source_a), ("b.txt", source_b)],
                {"version": 1},
            )
            dest_a = root / "dest-a.txt"
            dest_b = root / "dest-b.txt"
            dest_a.write_text("old-a", encoding="utf-8")
            dest_b.write_text("old-b", encoding="utf-8")
            real_replace = state.os.replace
            calls = []

            def flaky_replace(src, dst):
                calls.append((str(src), str(dst)))
                if len(calls) == 2:
                    raise OSError("simulated commit failure")
                return real_replace(src, dst)

            with patch.object(state.os, "replace", side_effect=flaky_replace):
                with self.assertRaises(OSError):
                    state.restore_zip_members(
                        archive, {"a.txt": dest_a, "b.txt": dest_b}, allowed_roots=[root]
                    )
            self.assertEqual(dest_a.read_text(encoding="utf-8"), "old-a")
            self.assertEqual(dest_b.read_text(encoding="utf-8"), "old-b")

    def test_zip_restore_rolls_back_new_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_a = root / "a.txt"
            source_b = root / "b.txt"
            source_a.write_text("new-a", encoding="utf-8")
            source_b.write_text("new-b", encoding="utf-8")
            archive = root / "backup.zip"
            state.create_zip_atomic(
                archive,
                [("newdir/a.txt", source_a), ("newdir/b.txt", source_b)],
                {"00-config": str(root / "config")},
            )
            real_replace = state.os.replace
            calls = []

            def flaky_replace(src, dst):
                calls.append((str(src), str(dst)))
                if len(calls) == 2:
                    raise OSError("simulated commit failure")
                return real_replace(src, dst)

            with patch.object(state.os, "replace", side_effect=flaky_replace):
                with self.assertRaises(OSError):
                    state.restore_zip_members(
                        archive,
                        {
                            "newdir/a.txt": root / "newdir" / "a.txt",
                            "newdir/b.txt": root / "newdir" / "b.txt",
                        },
                        allowed_roots=[root],
                    )
            self.assertFalse((root / "newdir").exists())

    def test_backup_text_uses_distinct_archive_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            (config / "settings.json").write_text("{}", encoding="utf-8")
            backup_dir = root / "backups"
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ):
                first = engine.backup_text("alpha")
                second = engine.backup_text("alpha")
            paths = [
                Path(next(line for line in output.splitlines() if line.startswith("backup created:")).split(": ", 1)[1])
                for output in (first, second)
            ]
            self.assertEqual(len(paths), 2)
            self.assertNotEqual(paths[0], paths[1])
            self.assertTrue(all(path.exists() for path in paths))

    def test_snapshot_targets_rejects_file_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "config"
            config.write_text("not-a-directory", encoding="utf-8")
            with patch.object(engine, "resolve_agent", return_value={"id": "alpha", "config": config.as_posix()}):
                with self.assertRaises(state.StateError):
                    engine._snapshot_targets("alpha")

    def test_restore_listing_refuses_reparse_backup_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            backup_dir = Path(td) / "backups"
            original = state._is_link_or_reparse

            def fake_reparse(path: Path) -> bool:
                return Path(path) == backup_dir or original(path)

            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                state, "_is_link_or_reparse", side_effect=fake_reparse
            ):
                result = engine.restore_text()
            self.assertIn("backup refused", result.lower())

    def test_restore_rejects_empty_manifest_archive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            backup_dir = root / "backups"
            backup_dir.mkdir()
            archive = backup_dir / "agent-config-alpha-empty-manifest.zip"
            state.create_zip_atomic(archive, [], {})
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ), patch.object(engine, "_snapshot_targets", return_value=[config]):
                result = engine.restore_text(archive.name, "alpha", confirm=True)
            self.assertIn("refusing", result.lower())

    def test_restore_rejects_manifest_path_owned_by_another_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "a" / "config"
            other = root / "b" / "config"
            config.mkdir(parents=True)
            other.mkdir(parents=True)
            target_file = config / "settings.json"
            target_file.write_text("old", encoding="utf-8")
            backup_dir = root / "backups"
            backup_dir.mkdir()
            source = root / "new.txt"
            source.write_text("new", encoding="utf-8")
            archive = backup_dir / "agent-config-alpha-cross-owner.zip"
            state.create_zip_atomic(
                archive,
                [("00-config/settings.json", source)],
                {"00-config": str(other)},
            )
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ), patch.object(engine, "_snapshot_targets", return_value=[config]):
                result = engine.restore_text(archive.name, "alpha", confirm=True)
            self.assertIn("refusing", result.lower())
            self.assertEqual(target_file.read_text(encoding="utf-8"), "old")

    def test_restore_text_rejects_manifest_target_mapping_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            target = config / "settings.json"
            target.write_text("old", encoding="utf-8")
            backup_dir = root / "backups"
            backup_dir.mkdir()
            archive = backup_dir / "agent-config-alpha-bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("00-config/settings.json", "new")
                zf.writestr("_manifest.json", json.dumps({"00-other": str(config)}))
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ):
                output = engine.restore_text(archive.name, "alpha", confirm=True)
            self.assertIn("refusing", output.lower())
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_restore_text_rejects_unowned_top_level_member(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            backup_dir = root / "backups"
            backup_dir.mkdir()
            archive = backup_dir / "agent-config-alpha-unowned.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("outside.txt", "payload")
                zf.writestr("_manifest.json", json.dumps({"00-config": str(config)}))
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ):
                output = engine.restore_text(archive.name, "alpha", confirm=True)
            self.assertIn("unowned member", output.lower())
            self.assertFalse((config / "outside.txt").exists())

    def test_restore_uses_immutable_archive_snapshot_when_source_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            target_file = config / "settings.json"
            target_file.write_text("current", encoding="utf-8")
            backup_dir = root / "backups"
            backup_dir.mkdir()
            source = root / "source.txt"
            source.write_text("safe", encoding="utf-8")
            archive = backup_dir / "agent-config-alpha-snapshot.zip"
            state.create_zip_atomic(
                archive,
                [("00-config/settings.json", source)],
                {"00-config": str(config)},
            )
            source.write_text("evil", encoding="utf-8")
            replacement = root / "replacement.zip"
            state.create_zip_atomic(
                replacement,
                [("00-config/settings.json", source)],
                {"00-config": str(config)},
            )
            outer_replacement = root / "outer-replacement.zip"
            state.create_zip_atomic(
                outer_replacement,
                [("00-config/settings.json", source)],
                {"00-config": str(config)},
            )
            real_snapshot = state.snapshot_zip
            real_layout = state.read_backup_layout_bytes
            replaced = False
            outer_replaced = False
            snapshot_path = {}

            def snapshot_then_replace(path, **kwargs):
                nonlocal replaced
                snapshot, identity = real_snapshot(path, **kwargs)
                snapshot_path["path"] = snapshot
                snapshot_path["identity"] = identity
                if not replaced:
                    replaced = True
                    os.replace(replacement, path)
                return snapshot, identity

            def layout_then_replace(data, **kwargs):
                # Replace the snapshot before the bytes are read. The engine now
                # carries the snapshot identity, so this must be refused rather
                # than silently restoring the replacement archive.
                if not outer_replaced:
                    outer_replaced = True
                    os.replace(outer_replacement, snapshot_path["path"])
                return real_layout(data, **kwargs)

            real_read_snapshotted = state.read_snapshotted_bytes

            def read_then_replace(snapshot, identity, **kwargs):
                nonlocal outer_replaced
                if not outer_replaced:
                    outer_replaced = True
                    os.replace(outer_replacement, snapshot)
                return real_read_snapshotted(snapshot, identity, **kwargs)

            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ), patch.object(state, "snapshot_zip", side_effect=snapshot_then_replace), patch.object(
                state, "read_snapshotted_bytes", side_effect=read_then_replace
            ):
                result = engine.restore_text(archive.name, "alpha", confirm=True)
            # Replacing the snapshot after it was verified must be refused;
            # restoring the replacement's bytes would report success while
            # writing content nobody validated.
            self.assertIn("refused", result.lower())
            self.assertNotIn("restored 1 files", result)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "current")

    def test_restore_text_reports_outer_snapshot_cleanup_failure_as_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            source = root / "source.txt"
            source.write_text("safe", encoding="utf-8")
            backup_dir = root / "backups"
            backup_dir.mkdir()
            archive = backup_dir / "agent-config-alpha-cleanup.zip"
            state.create_zip_atomic(
                archive,
                [("00-config/source.txt", source)],
                {"00-config": str(config)},
            )
            real_unlink = Path.unlink
            calls = 0
            snapshots = []

            def flaky_unlink(path, *args, **kwargs):
                nonlocal calls
                calls += 1
                snapshots.append(path)
                if calls == 1:
                    raise OSError("outer snapshot locked")
                return real_unlink(path, *args, **kwargs)

            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ), patch.object(Path, "unlink", new=flaky_unlink):
                result = engine.restore_text(archive.name, "alpha", confirm=True)
            self.assertEqual(result.status, "error")
            self.assertIn("cleanup failed", result)
            for path in snapshots:
                if path.exists():
                    real_unlink(path)

    def test_restore_text_reports_cleanup_failure_after_restore_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            backup_dir = root / "backups"
            backup_dir.mkdir()
            archive = backup_dir / "agent-config-alpha-empty.zip"
            state.create_zip_atomic(archive, [], {})
            real_unlink = Path.unlink
            snapshots = []

            def failing_unlink(path, *args, **kwargs):
                if path.name.startswith(".agentfix-restore-"):
                    snapshots.append(path)
                    raise OSError("snapshot locked")
                return real_unlink(path, *args, **kwargs)

            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ), patch.object(Path, "unlink", new=failing_unlink):
                result = engine.restore_text(archive.name, "alpha", confirm=True)
            self.assertEqual(result.status, "error")
            self.assertIn("manifest", result.lower())
            self.assertIn("cleanup failed", result.lower())
            for snapshot in snapshots:
                real_unlink(snapshot)

    def test_restore_text_restores_only_explicit_target_transactionally(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            target_file = config / "settings.json"
            target_file.write_text('{"value": "original"}', encoding="utf-8")
            backup_dir = root / "backups"
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ):
                created = engine.backup_text("alpha")
                archive_name = next(
                    line for line in created.splitlines() if line.startswith("backup created:")
                ).split(": ", 1)[1]
                target_file.write_text('{"value": "changed"}', encoding="utf-8")
                restored = engine.restore_text(
                    backup=Path(archive_name).name, agent_id="alpha", confirm=True
                )
            self.assertIn("restored 1 files", restored)
            self.assertEqual(json.loads(target_file.read_text(encoding="utf-8"))["value"], "original")

    def test_empty_config_backup_round_trip_restores_zero_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            backup_dir = root / "backups"
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ):
                created = engine.backup_text("alpha")
                archive_name = next(
                    line for line in created.splitlines() if line.startswith("backup created:")
                ).split(": ", 1)[1]
                result = engine.restore_text(Path(archive_name).name, "alpha", confirm=True)
            self.assertIn("restored 0 files", result)
            self.assertIn("backup created", created)

    def test_restore_latest_uses_creation_order_not_uuid_lexical_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config"
            config.mkdir()
            target_file = config / "settings.json"
            target_file.write_text("current", encoding="utf-8")
            backup_dir = root / "backups"
            backup_dir.mkdir()
            agent = {"id": "alpha", "name": "Alpha", "config": config.as_posix()}

            def make_backup(name: str, value: str) -> None:
                source = root / f"source-{value}.json"
                source.write_text(value, encoding="utf-8")
                state.create_zip_atomic(
                    backup_dir / name,
                    [("00-config/settings.json", source)],
                    {"00-config": str(config)},
                )

            # Simulate two new-format backups created in one second. The newer
            # archive's random token is lexicographically smaller than the older one.
            make_backup("agent-config-alpha-20260925-120000-00000000000000000001-ffff.zip", "old")
            make_backup("agent-config-alpha-20260925-120000-00000000000000000002-0000.zip", "new")
            make_backup("agent-config-beta-20260925-120000-00000000000000000003-0000.zip", "other-agent")
            with patch.object(engine, "BACKUP_DIR", backup_dir), patch.object(
                engine, "resolve_agent", return_value=agent
            ), patch.object(engine, "_snapshot_targets", return_value=[config]):
                result = engine.restore_text(backup="latest", agent_id="alpha", confirm=True)

            self.assertIn("restored 1 files", result)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "new")

    def test_backup_names_reject_path_traversal_components(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for prefix, suffix in (("../escape", ".zip"), ("foo/bar", ".zip"), ("safe", "../.zip"), ("CON", ".zip")):
                with self.assertRaises(state.StateError):
                    state.unique_backup_path(root, prefix, suffix)

    def test_backup_names_are_unique_within_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            first = state.unique_backup_path(Path(td), "agent-config-alpha", ".zip")
            second = state.unique_backup_path(Path(td), "agent-config-alpha", ".zip")
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, second.parent)


if __name__ == "__main__":
    unittest.main()
