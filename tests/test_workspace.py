import contextlib
import io
import asyncio
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helper.auth import Controller
from helper.common import Log, MANIFEST
from helper.workspace import (MAGIC, MARKER, workspace_root, prepare_environment,
                              new_auth_session, remove_auth_session, request_cleanup)

SOURCE = Path(__file__).resolve().parents[1]
PWSH = os.environ.get("FINKA_TEST_PWSH") or shutil.which("powershell.exe") or shutil.which("pwsh")
SDK_AVAILABLE = importlib.util.find_spec("pymobiledevice3") is not None


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.bundle = self.root / "portable bundle 中文"
        self.bundle.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def copy_entries(self, platform):
        names = ["scripts/workspace.zsh", "清理-Mac.command", "启动-Mac.command", "manifest.json"] if platform == "mac" else ["scripts/Workspace.ps1", "cleanup.ps1"]
        for name in names:
            target = self.bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, target)


class WorkspaceTests(Fixture):
    def test_root_is_inside_bundle_and_legacy_override_is_ignored(self):
        with patch.dict(os.environ, {"FINKA_HELPER_DATA": str(self.root / "outside")}):
            work = workspace_root(self.bundle)
        self.assertEqual(work, self.bundle / ".finka-work")
        self.assertFalse((self.root / "outside").exists())
        self.assertEqual((work / MARKER).read_text().strip(), MAGIC)

    def test_unknown_nonempty_directory_is_not_adopted(self):
        work = self.bundle / ".finka-work"
        work.mkdir()
        (work / "original.txt").write_text("original")
        with self.assertRaises(RuntimeError):
            workspace_root(self.bundle)
        self.assertEqual((work / "original.txt").read_text(), "original")

    @unittest.skipUnless(os.name == "posix", "symlink creation requires POSIX")
    def test_workspace_symlink_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.bundle / ".finka-work").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            workspace_root(self.bundle)
        self.assertEqual(list(outside.iterdir()), [])

    def test_process_caches_and_temporary_paths_are_local(self):
        with patch.dict(os.environ, {"PIP_TARGET": str(self.root / "unrelated")}):
            work = prepare_environment(self.bundle)
            self.assertNotIn("PIP_TARGET", os.environ)
            for key in ("PIP_CACHE_DIR", "TMPDIR", "TMP", "TEMP"):
                self.assertTrue(Path(os.environ[key]).is_relative_to(work))
            self.assertEqual(os.environ["PYTHONDONTWRITEBYTECODE"], "1")

    def test_finder_metadata_does_not_break_portable_startup(self):
        work = workspace_root(self.bundle)
        (work / ".DS_Store").write_bytes(b"SYNTHETIC-METADATA")
        self.assertEqual(workspace_root(self.bundle), work)

    def test_auth_sessions_are_distinct_and_only_own_session_is_removed(self):
        work = workspace_root(self.bundle)
        first, other = new_auth_session(work), new_auth_session(work)
        (first / "account").write_text("SYNTHETIC-ENCRYPTED")
        remove_auth_session(first, work)
        self.assertFalse(first.exists())
        self.assertTrue(other.exists())

    def test_unknown_auth_path_is_preserved(self):
        work = workspace_root(self.bundle)
        outside = self.root / "other"
        outside.mkdir()
        (outside / "original").write_text("keep")
        with self.assertRaises(RuntimeError):
            remove_auth_session(outside, work)
        self.assertEqual((outside / "original").read_text(), "keep")

    def test_private_controller_uses_private_flag_and_random_phrase(self):
        work = workspace_root(self.bundle)
        log = Log(work / "logs")
        sent, commands = [], []
        chunks = iter(["enter passphrase to unlock private: ", "enter Apple ID: ", "enter password: ",
                       '{"success":true}'])
        class Session:
            def read(self, timeout=0):
                try:
                    return next(chunks)
                except StopIteration:
                    raise EOFError
            def sendline(self, value): sent.append(value)
            def alive(self): return True
            def returncode(self): return 0
            def close(self): pass
        def factory(command):
            commands.append(command)
            return Session()
        with patch("helper.common.ROOT", self.bundle), contextlib.redirect_stdout(io.StringIO()):
            ctrl = Controller("synthetic-tool", log, session_factory=factory, portable=True,
                              read_password=lambda *a: self.fail("No extra password prompt expected"))
            private = ctrl.private_directory
            phrase = ctrl.cache_phrase
            ctrl.login("test@example.invalid", "SYNTHETIC-PASSWORD")
            ctrl.close_private_state()
        self.assertIn("--finka-state-directory", commands[0])
        self.assertTrue(private.is_relative_to(work))
        self.assertEqual(sent[0], phrase)
        self.assertNotIn(phrase, str(commands))
        self.assertNotIn(phrase, log.path.read_text())
        self.assertFalse(private.exists())


@unittest.skipUnless(os.name == "posix", "Mac shell checks require POSIX")
class MacCleanupTests(Fixture):
    def setUp(self):
        super().setUp()
        self.copy_entries("mac")

    def cleanup(self, yes=True, input_text=None):
        args = ["zsh", str(self.bundle / "清理-Mac.command"), "--no-pause"]
        if yes:
            args.append("--yes")
        return subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=10)

    def test_removes_owned_work_and_can_repeat(self):
        work = workspace_root(self.bundle)
        (work / "downloads").mkdir()
        (work / "downloads/sample.ipa").write_bytes(b"SYNTHETIC")
        self.assertEqual(self.cleanup().returncode, 0)
        self.assertFalse(work.exists())
        self.assertEqual(self.cleanup().returncode, 0)
        self.assertTrue((self.bundle / "manifest.json").exists())

    def test_cancel_keeps_data(self):
        work = workspace_root(self.bundle)
        self.assertEqual(self.cleanup(False, "\n").returncode, 0)
        self.assertTrue(work.exists())

    def test_unmarked_directory_is_refused(self):
        work = self.bundle / ".finka-work"
        work.mkdir()
        (work / "original").write_text("keep")
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertEqual((work / "original").read_text(), "keep")

    def test_unknown_top_level_file_is_refused(self):
        work = workspace_root(self.bundle)
        (work / "unrelated.txt").write_text("keep")
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertEqual((work / "unrelated.txt").read_text(), "keep")

    def test_active_process_lock_is_refused(self):
        work = workspace_root(self.bundle)
        lock = work / ".running-lock"
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()))
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertTrue(work.exists())

    def test_stale_process_lock_is_recovered(self):
        work = workspace_root(self.bundle)
        lock = work / ".running-lock"
        lock.mkdir()
        (lock / "pid").write_text("99999999")
        self.assertEqual(self.cleanup().returncode, 0)
        self.assertFalse(work.exists())

    def test_orphan_child_process_prevents_cleanup_even_with_dead_parent(self):
        work = workspace_root(self.bundle)
        lock = work / ".running-lock"
        lock.mkdir()
        (lock / "pid").write_text("99999999")
        (lock / "worker-pid").write_text(str(os.getpid()))
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertTrue(work.exists())

    def test_generated_finder_metadata_is_cleaned(self):
        work = workspace_root(self.bundle)
        (work / ".DS_Store").write_bytes(b"SYNTHETIC")
        self.assertEqual(self.cleanup().returncode, 0)
        self.assertFalse(work.exists())

    def test_nested_symlink_never_deletes_external_target(self):
        work = workspace_root(self.bundle)
        cache = work / "cache"
        cache.mkdir()
        outside = self.root / "external"
        outside.mkdir()
        (outside / "original").write_text("keep")
        (cache / "link").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.cleanup().returncode, 0)
        self.assertEqual((outside / "original").read_text(), "keep")

    def test_git_directory_inside_work_is_refused(self):
        work = workspace_root(self.bundle)
        (work / "tools/.git").mkdir(parents=True)
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertTrue((work / "tools/.git").exists())

    def seed_stub_runtime(self, request=False):
        work = workspace_root(self.bundle)
        key = "macos-arm64" if os.uname().machine == "arm64" else "macos-x64"
        runtime = work / ("python-3.13-" + key)
        (runtime / "python/bin").mkdir(parents=True)
        executable = runtime / "python/bin/python3"
        content = "#!/bin/zsh\n"
        if request:
            content += "print -r -- " + shlex.quote(MAGIC) + " > " + shlex.quote(str(work / ".cleanup-request")) + "\n"
        content += "exit 0\n"
        executable.write_text(content)
        executable.chmod(0o755)
        (runtime / "runtime.ready").write_text(MANIFEST["platforms"][key]["python"]["sha256"])
        return work

    @unittest.skipUnless(sys.platform == "darwin", "Mac launcher requires macOS system utilities")
    def test_parent_cleans_after_child_exit_and_does_not_recreate_logs(self):
        work = self.seed_stub_runtime(request=True)
        result = subprocess.run(["zsh", str(self.bundle / "启动-Mac.command")], capture_output=True,
                                text=True, timeout=15, env={**os.environ, "FINKA_HELPER_NO_PAUSE": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(work.exists())

    @unittest.skipUnless(sys.platform == "darwin", "Mac launcher requires macOS system utilities")
    def test_old_cleanup_request_does_not_authorize_new_run_cleanup(self):
        work = self.seed_stub_runtime(request=False)
        request_cleanup(self.bundle)
        result = subprocess.run(["zsh", str(self.bundle / "启动-Mac.command")], capture_output=True,
                                text=True, timeout=15, env={**os.environ, "FINKA_HELPER_NO_PAUSE": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(work.exists())
        self.assertFalse((work / ".cleanup-request").exists())


@unittest.skipUnless(PWSH, "Set FINKA_TEST_PWSH for PowerShell cleanup tests")
class PowerShellCleanupTests(Fixture):
    def setUp(self):
        super().setUp()
        self.copy_entries("windows")

    def cleanup(self, yes=True, input_text=None):
        args = [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.bundle / "cleanup.ps1")]
        if yes:
            args.append("-Yes")
        else:
            args.remove("-NonInteractive")
        return subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=20,
                              env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off"})

    def test_removes_only_owned_work_and_can_repeat(self):
        work = workspace_root(self.bundle)
        outside = self.root / "original"
        outside.write_text("keep")
        result = self.cleanup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(work.exists())
        self.assertEqual(outside.read_text(), "keep")
        self.assertEqual(self.cleanup().returncode, 0)

    def test_cancel_preserves_work(self):
        work = workspace_root(self.bundle)
        self.assertEqual(self.cleanup(False, "\n").returncode, 0)
        self.assertTrue(work.exists())

    def test_unknown_files_are_refused(self):
        work = workspace_root(self.bundle)
        (work / "unrelated").write_text("keep")
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertEqual((work / "unrelated").read_text(), "keep")

    def test_active_process_lock_is_refused(self):
        work = workspace_root(self.bundle)
        lock = work / ".running-lock"
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()))
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertTrue(work.exists())

    def test_git_directory_is_refused(self):
        work = workspace_root(self.bundle)
        (work / "tools/.git").mkdir(parents=True)
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertTrue(work.exists())

    def test_orphan_child_process_is_refused(self):
        work = workspace_root(self.bundle)
        lock = work / ".running-lock"
        lock.mkdir()
        (lock / "pid").write_text("99999999")
        (lock / "auth-pid").write_text(str(os.getpid()))
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertTrue(work.exists())

    @unittest.skipUnless(os.name == "posix", "Create test reparse point using POSIX symlink")
    def test_symlink_is_refused_without_touching_target(self):
        work = workspace_root(self.bundle)
        (work / "cache").mkdir()
        outside = self.root / "external"
        outside.mkdir()
        (outside / "original").write_text("keep")
        (work / "cache/link").symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertEqual((outside / "original").read_text(), "keep")


class EndOfInstallTests(Fixture):
    def run_main(self, status, choice=""):
        import main
        from unittest.mock import MagicMock
        work = workspace_root(self.bundle)
        ctrl = MagicMock()
        record = {"bytes": 100, "sha256": "SYNTHETIC-HASH", "app": {"CFBundleShortVersionString": "1.0"}}
        answers = ["test@example.invalid"]
        if status in ("installed_and_readback_verified", "existing_application_preserved"):
            answers.append(choice)
        with patch.dict(os.environ), patch("helper.common.ROOT", self.bundle), \
             patch.object(main, "prepare_environment", side_effect=lambda: prepare_environment(self.bundle)), \
             patch.object(main, "request_cleanup", side_effect=lambda: request_cleanup(self.bundle)), \
             patch.object(main, "environment", return_value="macos-arm64"), \
             patch.object(main, "ensure_ipatool", return_value="synthetic-tool"), \
             patch.object(main, "ensure_package"), patch.object(main, "Controller", return_value=ctrl), \
             patch.object(main, "valid_cached_package", return_value=record), \
             patch.object(main, "install_flow", return_value={"status": status}), \
             patch.object(main, "notify"), patch.object(main, "answer", side_effect=answers), \
             patch.object(main.getpass, "getpass", return_value="SYNTHETIC-PASSWORD"), \
             contextlib.redirect_stdout(io.StringIO()):
            result = main.main([])
        ctrl.close_private_state.assert_called_once()
        return work, result

    def test_install_success_can_request_deferred_cleanup(self):
        work, result = self.run_main("installed_and_readback_verified", "c")
        self.assertEqual(result, 0)
        self.assertTrue((work / ".cleanup-request").exists())
        self.assertTrue(work.exists())  # Python never deletes its own Windows runtime.

    def test_enter_after_success_keeps_workspace(self):
        work, result = self.run_main("installed_and_readback_verified", "")
        self.assertEqual(result, 0)
        self.assertFalse((work / ".cleanup-request").exists())

    def test_failure_never_schedules_automatic_cleanup(self):
        work, result = self.run_main("installation_failed")
        self.assertEqual(result, 1)
        self.assertFalse((work / ".cleanup-request").exists())

    def test_existing_app_still_offers_cleanup(self):
        work, result = self.run_main("existing_application_preserved", "c")
        self.assertEqual(result, 0)
        self.assertTrue((work / ".cleanup-request").exists())


@unittest.skipUnless(SDK_AVAILABLE, "USB SDK is installed by the launcher")
class ActualPairingPersistenceTests(Fixture):
    def test_actual_sdk_saves_only_private_copy_without_usbmux_write(self):
        from helper.device_worker import private_pairing_options
        from pymobiledevice3.lockdown import PlistUsbmuxLockdownClient
        original = PlistUsbmuxLockdownClient.save_pair_record
        try:
            with patch("helper.common.ROOT", self.bundle):
                options = private_pairing_options()
            client = object.__new__(PlistUsbmuxLockdownClient)
            client.identifier = "SYNTHETIC-DEVICE"
            client.pairing_records_cache_folder = options["pairing_records_cache_folder"]
            client.pair_record = {"HostID": "SYNTHETIC-ONLY"}
            with patch("pymobiledevice3.usbmux.create_mux", side_effect=AssertionError("No shared usbmux write allowed")):
                asyncio.run(client.save_pair_record())
            record = client.pairing_records_cache_folder / "SYNTHETIC-DEVICE.plist"
            self.assertTrue(record.is_file())
            self.assertTrue(record.is_relative_to(workspace_root(self.bundle)))
        finally:
            PlistUsbmuxLockdownClient.save_pair_record = original


if __name__ == "__main__":
    unittest.main()
