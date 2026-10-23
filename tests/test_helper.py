import contextlib
import asyncio
import io
import json
import os
import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helper.auth import Controller, classify_error
from helper.common import HelperError, Log, MANIFEST, redact, platform_key
from helper.package import inspect_package, valid_cached_package
from helper.terminal import PtySession


class Session:
    def __init__(self, chunks, code=0):
        self.chunks = list(chunks)
        self.code, self.sent, self.closed = code, [], False
    def read(self, timeout=0):
        if not self.chunks:
            raise EOFError
        return self.chunks.pop(0)
    def sendline(self, value):
        self.sent.append(value)
    def alive(self):
        return bool(self.chunks)
    def returncode(self):
        return self.code
    def close(self):
        self.closed = True


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = Log(self.root / "logs")
        self.sink = contextlib.redirect_stdout(io.StringIO())
        self.sink.__enter__()
    def tearDown(self):
        self.sink.__exit__(None, None, None)
        self.tmp.cleanup()

class HelperTests(Fixture):
    def controller(self, sessions, codes):
        made, commands = [], []
        def factory(command):
            commands.append(command)
            value = sessions[len(made)]
            made.append(value)
            return value
        values = iter(codes)
        controller = Controller("fake-tool", self.log, session_factory=factory,
                                read_code=lambda *a: next(values), sleep=lambda _: None)
        return controller, made, commands

    def challenge(self, end=None, code=0):
        chunks = ["enter Apple ID (email address or phone number): ", "enter password: ", "enter 2FA code: "]
        if end:
            chunks.append(json.dumps(end))
        return Session(chunks, code)

    def test_blank_restarts_fresh_process_without_submitting_blank(self):
        first = self.challenge()
        second = self.challenge({"success": True})
        ctrl, made, commands = self.controller([first, second], ["", "654321"])
        ctrl.login("test@example.invalid", "SYNTHETIC-SECRET")
        self.assertEqual(len(made), 2)
        self.assertEqual(first.sent, ["test@example.invalid", "SYNTHETIC-SECRET"])
        self.assertEqual(second.sent[-1], "654321")
        self.assertTrue(all(s.closed for s in made))
        self.assertTrue(all("SYNTHETIC-SECRET" not in str(command) for command in commands))

    def test_timeout_reissues_without_blank_code(self):
        ctrl, made, _ = self.controller([self.challenge(), self.challenge({"success": True})], [None, "654321"])
        ctrl.login("test@example.invalid", "test-secret")
        self.assertEqual(len(made[0].sent), 2)
        self.assertIn("OTP_REISSUE", self.log.path.read_text())

    def test_wrong_code_retries_and_error_output_is_not_logged(self):
        ctrl, made, _ = self.controller([
            self.challenge({"success": False, "error": "Incorrect verification code 111111 secret-token"}, 1),
            self.challenge({"success": True})], ["111111", "654321"])
        ctrl.login("test@example.invalid", "test-secret")
        self.assertEqual(len(made), 2)
        text = self.log.path.read_text()
        for secret in ["111111", "654321", "test-secret", "test@example.invalid", "secret-token"]:
            self.assertNotIn(secret, text)

    def test_blank_retry_limit(self):
        ctrl, made, _ = self.controller([self.challenge() for _ in range(3)], ["", "", ""])
        with self.assertRaises(HelperError) as caught:
            ctrl.login("test@example.invalid", "test-secret")
        self.assertEqual(caught.exception.code, "OTP_RETRIES_EXHAUSTED")
        self.assertEqual(len(made), 3)

    def test_zero_exit_without_success_does_not_pass(self):
        ctrl, _, _ = self.controller([Session([json.dumps({"message": "2FA code is required"})]) for _ in range(3)], [])
        with self.assertRaises(HelperError):
            ctrl.login("test@example.invalid", "test-secret")

    def test_rate_limit_stops_immediately(self):
        ctrl, made, _ = self.controller([Session([json.dumps({"success": False, "error": "429 too many requests"})], 1)], [])
        with self.assertRaises(HelperError) as caught:
            ctrl.login("test@example.invalid", "test-secret")
        self.assertEqual(caught.exception.code, "LOGIN_RATE_LIMIT")
        self.assertEqual(len(made), 1)

    def test_cancel_closes_child(self):
        ctrl, made, _ = self.controller([self.challenge()], ["q"])
        with self.assertRaises(KeyboardInterrupt):
            ctrl.login("test@example.invalid", "test-secret")
        self.assertTrue(made[0].closed)

    def test_cache_prompt_does_not_misclassify_later_otp_error(self):
        first = Session(["enter passphrase to unlock C:\\test (this is separate from your Apple ID password): ",
                         "enter Apple ID (email address or phone number): ", "enter password: ", "enter 2FA code: ",
                         json.dumps({"success": False, "error": "Incorrect verification code"})], 1)
        ctrl, made, _ = self.controller([first, self.challenge({"success": True})], ["111111", "654321"])
        ctrl.read_password = lambda *a: "SYNTHETIC-CACHE"
        ctrl.login("test@example.invalid", "test-secret")
        self.assertEqual(len(made), 2)
        self.assertEqual(first.sent[0], "SYNTHETIC-CACHE")

    def make_ipa(self, binary=True, bid=None):
        path = self.root / "sample.ipa"
        info = {"CFBundleIdentifier": bid or MANIFEST["bundle_id"], "CFBundleExecutable": "Aloha",
                "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0", "MinimumOSVersion": "13.0"}
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("Payload/Aloha.app/Info.plist", plistlib.dumps(info))
            if binary:
                z.writestr("Payload/Aloha.app/Aloha", bytes.fromhex("cffaedfe") + bytes(100))
        return path

    def test_metadata_only_package_is_rejected(self):
        with self.assertRaises(HelperError) as caught:
            inspect_package(self.make_ipa(False))
        self.assertEqual(caught.exception.code, "NO_EXECUTABLE")

    def test_wrong_app_is_rejected(self):
        with self.assertRaises(HelperError) as caught:
            inspect_package(self.make_ipa(bid="example.other"))
        self.assertEqual(caught.exception.code, "WRONG_APP")

    def test_package_cache_is_scoped_to_account_and_hash(self):
        path = self.make_ipa()
        record = inspect_package(path)
        record["owner_tag"] = "owner-a"
        meta = self.root / "check.json"
        meta.write_text(json.dumps(record))
        self.assertIsNotNone(valid_cached_package(path, meta, "owner-a"))
        self.assertIsNone(valid_cached_package(path, meta, "owner-b"))
        with path.open("ab") as f:
            f.write(b"tamper")
        self.assertIsNone(valid_cached_package(path, meta, "owner-a"))

    def test_redaction(self):
        value = redact("test@example.invalid password=secret 123456 https://user:pass@example.invalid/a?token=x", ("secret",))
        for secret in ("test@example.invalid", "secret", "123456", "user:pass", "token=x"):
            self.assertNotIn(secret, value)

    def test_no_purchase_or_credential_arguments_in_main(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text()
        self.assertNotIn('"--purchase"', source)
        self.assertNotIn('"--password"', source)
        self.assertNotIn('"--auth-code"', source)

    def test_windows_arm_host_uses_actual_x64_runtime(self):
        with patch("helper.common.platform.system", return_value="Windows"), \
             patch("helper.common.platform.machine", return_value="ARM64"), \
             patch("helper.common.sysconfig.get_platform", return_value="win-amd64"):
            self.assertEqual(platform_key(), "windows-x64")


class InstallVerificationTests(Fixture):
    def run_install(self, before=None, after=None, ios="27.2", minimum="13.0", install_error=None):
        from helper import device_worker
        calls, events = [], []
        package = self.root / "synthetic.ipa"
        package.write_bytes(b"synthetic-test")
        app = {"CFBundleIdentifier": MANIFEST["bundle_id"], "CFBundleVersion": "10",
               "CFBundleShortVersionString": "1.0", "MinimumOSVersion": minimum}
        class Lock:
            all_values = {"ProductVersion": ios}
            async def close(self):
                calls.append("closed")
        async def create(**kwargs):
            return Lock()
        class Service:
            def __init__(self, lock):
                self.count = 0
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def get_apps(self, **kwargs):
                value = before if self.count == 0 else after
                self.count += 1
                return {MANIFEST["bundle_id"]: value} if value else {}
            async def install_from_local(self, *args, **kwargs):
                calls.append("install")
                if install_error:
                    raise RuntimeError(install_error)
        modules = {"pymobiledevice3.lockdown": SimpleNamespace(create_using_usbmux=create),
                   "pymobiledevice3.services.installation_proxy": SimpleNamespace(InstallationProxyService=Service)}
        self.calls, self.events = calls, events
        with patch.dict(sys.modules, modules), \
             patch("helper.package.inspect_package", return_value={"app": app}), \
             patch.object(device_worker, "emit", side_effect=lambda **kw: events.append(kw)):
            asyncio.run(device_worker.install(SimpleNamespace(package=package, device="SYNTHETIC-DEVICE")))

    def test_success_requires_matching_readback(self):
        self.run_install(after={"CFBundleVersion": "10", "CFBundleShortVersionString": "1.0"})
        self.assertEqual(self.events[-1]["event"], "installed")
        self.assertEqual(self.events[-1]["readback"], "passed")
        self.assertEqual(self.calls, ["install", "closed"])

    def test_successful_service_with_missing_app_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "READBACK_FAILED"):
            self.run_install(after=None)
        self.assertFalse(any(e["event"] == "installed" for e in self.events))
        self.assertEqual(self.calls[-1], "closed")

    def test_wrong_readback_version_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "VERSION_MISMATCH"):
            self.run_install(after={"CFBundleVersion": "9", "CFBundleShortVersionString": "1.0"})
        self.assertFalse(any(e["event"] == "installed" for e in self.events))

    def test_existing_real_app_is_preserved(self):
        self.run_install(before={"CFBundleShortVersionString": "0.9"})
        self.assertNotIn("install", self.calls)
        self.assertEqual(self.events[-1]["event"], "existing")

    def test_install_failure_retains_phone_error(self):
        with self.assertRaisesRegex(RuntimeError, "ApplicationVerificationFailed"):
            self.run_install(install_error="ApplicationVerificationFailed: test refusal")
        self.assertFalse(any(e["event"] == "installed" for e in self.events))
        self.assertEqual(self.calls[-1], "closed")


@unittest.skipUnless(os.name == "posix", "Native PTY test requires POSIX; Windows uses its own native run")
class NativePtyTests(Fixture):
    def test_real_child_blank_then_success(self):
        fake = Path(__file__).with_name("fake_ipatool.py")
        counter = self.root / "counter"
        codes = iter(["", "654321"])
        with patch.dict(os.environ, {"FINKA_TEST_COUNTER": str(counter)}):
            ctrl = Controller("unused", self.log,
                              session_factory=lambda _: PtySession([sys.executable, str(fake)]),
                              read_code=lambda *a: next(codes), sleep=lambda _: None)
            ctrl.login("test@example.invalid", "SYNTHETIC-SECRET")
        self.assertEqual(counter.read_text(), "2")

    def test_real_child_wrong_code_then_success(self):
        fake = Path(__file__).with_name("fake_ipatool.py")
        counter = self.root / "counter"
        codes = iter(["111111", "654321"])
        with patch.dict(os.environ, {"FINKA_TEST_COUNTER": str(counter), "FINKA_TEST_WRONG_FIRST": "1"}):
            ctrl = Controller("unused", self.log,
                              session_factory=lambda _: PtySession([sys.executable, str(fake)]),
                              read_code=lambda *a: next(codes), sleep=lambda _: None)
            ctrl.login("test@example.invalid", "SYNTHETIC-SECRET")
        self.assertEqual(counter.read_text(), "2")
        self.assertNotIn("111111", self.log.path.read_text())


if __name__ == "__main__":
    unittest.main()
