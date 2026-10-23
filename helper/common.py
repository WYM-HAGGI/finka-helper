"""Small standard-library helpers. Never record raw authentication traffic."""
import hashlib
import json
import os
import platform
import re
import ssl
import subprocess
import sys
import sysconfig
import tarfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


class HelperError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def platform_key():
    system = platform.system()
    arch = platform.machine().lower()
    if system not in ("Darwin", "Windows"):
        raise HelperError("UNSUPPORTED_OS", "仅支持 macOS 和 Windows 10/11。")
    if system == "Windows":
        # platform.machine() may report the native ARM host even under x64 emulation.
        if sysconfig.get_platform().lower() != "win-amd64":
            raise HelperError("WINDOWS_RUNTIME", "请使用 Start-Windows.cmd 自动准备 x64 运行时；Windows ARM 需要 Windows 11。")
        return "windows-x64"
    if arch not in ("arm64", "aarch64", "x86_64", "amd64"):
        raise HelperError("UNSUPPORTED_CPU", "需要 Apple Silicon 或 Intel/AMD 64位处理器。")
    return ("macos" if system == "Darwin" else "windows") + ("-arm64" if arch in ("arm64", "aarch64") else "-x64")


def data_root():
    if os.environ.get("FINKA_HELPER_DATA"):
        root = Path(os.environ["FINKA_HELPER_DATA"]).expanduser().absolute()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    else:
        base = Path.home() / "Library/Application Support"
    root = base / "FinkaHelper"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def sha256(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def redact(text, secrets=()):
    text = str(text)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "[已隐藏]")
    text = re.sub(r"https?://[^\s\"'<>]+", "[网络地址已隐藏]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[账号已隐藏]", text)
    text = re.sub(r"(?<!\d)\d{6}(?!\d)", "[六位数字已隐藏]", text)
    text = re.sub(r"(?i)(password|passphrase|token|authorization|cookie)\s*[=:]\s*\S+", r"\1=[已隐藏]", text)
    return text[:1500]


class Log:
    def __init__(self, folder):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = folder / "操作日志.jsonl"
        self.secrets = set()

    def event(self, code, message, **details):
        safe = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "code": code,
                "message": redact(message, self.secrets)}
        safe.update({k: redact(v, self.secrets) if isinstance(v, str) else v for k, v in details.items()})
        print("[" + safe["time"][11:19] + "] " + safe["message"], flush=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")


def context():
    # Some python.org installations have no installed CA bundle. Keep verification on.
    ca = "/etc/ssl/cert.pem"
    return ssl.create_default_context(cafile=ca if sys.platform == "darwin" and Path(ca).is_file() else None)


def fetch_verified(asset, destination, log):
    destination = Path(destination)
    if destination.is_file() and sha256(destination) == asset["sha256"]:
        log.event("CACHE_OK", "已找到校验通过的本地工具缓存。")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = destination.with_name(destination.name + ".partial")
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "FinkaHelper/1.0"})
    log.event("TOOL_DOWNLOAD", "正在从官方发布地址下载所需工具。", expected_bytes=asset["size"])
    try:
        with urllib.request.urlopen(request, timeout=45, context=context()) as response, partial.open("wb") as out:
            total, tick = 0, time.monotonic()
            while chunk := response.read(262144):
                out.write(chunk)
                total += len(chunk)
                if time.monotonic() - tick >= 10:
                    log.event("TOOL_PROGRESS", "工具下载中。", downloaded_bytes=total)
                    tick = time.monotonic()
    except Exception as e:
        raise HelperError("NETWORK_DOWNLOAD", "工具下载失败：" + redact(e)) from e
    if sha256(partial) != asset["sha256"]:
        raise HelperError("CHECKSUM_FAILED", "工具 SHA-256 不匹配，已停止执行；请删除该工具的 .partial 文件后重试。")
    partial.replace(destination)
    log.event("TOOL_VERIFIED", "官方工具 SHA-256 校验通过。")
    return destination


def ensure_ipatool(log):
    key = platform_key()
    asset = MANIFEST["platforms"][key]["ipatool"]
    folder = data_root() / "tools" / ("ipatool-" + asset["version"] + "-" + key)
    archive = fetch_verified(asset, folder / "official.tar.gz", log)
    executable = folder / ("ipatool.exe" if os.name == "nt" else "ipatool")
    marker = folder / "binary-sha256.txt"
    if executable.is_file() and marker.is_file() and sha256(executable) == marker.read_text().strip():
        return executable
    with tarfile.open(archive) as tar:
        members = [m for m in tar.getmembers() if m.isfile() and Path(m.name).name.startswith("ipatool-")
                   and not Path(m.name).name.endswith((".md", ".txt", ".json"))]
        if len(members) != 1:
            raise HelperError("TOOL_ARCHIVE", "官方工具压缩包结构与预期不符，已停止。")
        with tar.extractfile(members[0]) as src, executable.open("wb") as out:
            while chunk := src.read(1048576):
                out.write(chunk)
    executable.chmod(0o700)
    marker.write_text(sha256(executable) + "\n")
    return executable


def notify(title, message):
    print("\a", end="", flush=True)
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x40 | 0x40000)
        elif sys.platform == "darwin":
            def quote(value):
                return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
            subprocess.run(["/usr/bin/osascript", "-e", "display notification " + quote(message) + " with title " + quote(title)],
                           capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        print(title + "：" + message, flush=True)


def run_process(args, log, description):
    """Read verbose non-auth output in memory; show only a sanitized tail on error."""
    import queue
    import threading
    log.event("PROCESS_START", description)
    process = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding="utf-8", errors="replace")
    messages = queue.Queue()
    def drain():
        for line in process.stdout:
            messages.put(line)
        messages.put(None)
    threading.Thread(target=drain, daemon=True).start()
    tail, tick = [], time.monotonic()
    try:
        while True:
            try:
                line = messages.get(timeout=1)
                if line is None:
                    break
                tail = (tail + [redact(line.strip(), log.secrets)])[-8:]
            except queue.Empty:
                pass
            if time.monotonic() - tick >= 15:
                log.event("PROCESS_BUSY", description + "，仍在进行中。")
                tick = time.monotonic()
        code = process.wait()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise
    if code:
        raise HelperError("DEPENDENCY_FAILED", description + "失败。" + " | ".join(tail))
    log.event("PROCESS_DONE", description + "完成。")


def ensure_package(package, version, module, log):
    import importlib.metadata
    try:
        if importlib.metadata.version(package) == version:
            return
    except importlib.metadata.PackageNotFoundError:
        pass
    # Always use this helper's isolated virtual environment, never sudo/global pip.
    if sys.prefix == sys.base_prefix:
        raise HelperError("VENV_REQUIRED", "请使用双击启动文件运行，避免修改系统 Python。")
    run_process([sys.executable, "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
                 "--only-binary=:all:", "--disable-pip-version-check", "--retries", "2", "--timeout", "30", package + "==" + version],
                log, "准备 " + package + " " + version)
    __import__(module)
