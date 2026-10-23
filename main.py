#!/usr/bin/env python3
"""Download Finka with the user's own Apple Account, then optionally install it."""
import argparse
import getpass
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from helper.auth import Controller, ERROR_TEXT
from helper.common import (HelperError, Log, MANIFEST, ROOT, data_root, ensure_ipatool,
                           ensure_package, notify, platform_key, redact, run_process)
from helper.package import inspect_package, valid_cached_package

APPLE_DEVICES = "https://apps.microsoft.com/detail/9NP83LWLPZ9K"


def answer(prompt, default=""):
    try:
        return input(prompt).strip() or default
    except EOFError:
        raise KeyboardInterrupt from None


def environment(log):
    key = platform_key()
    if os.name == "nt" and sys.getwindowsversion().build < 19045:
        raise HelperError("WINDOWS_TOO_OLD", "需要 Windows 10 22H2 或 Windows 11。")
    free = shutil.disk_usage(data_root()).free
    log.event("ENVIRONMENT", "已检测当前环境。", platform=key, os_version=platform.platform(),
              python=platform.python_version(), cpu_count=os.cpu_count(), free_bytes=free)
    if free < 2 * 1024 ** 3:
        raise HelperError("LOW_DISK_SPACE", "可用空间不足2GB，请先腾出空间再运行。")
    if not sys.stdin.isatty():
        raise HelperError("CONSOLE_REQUIRED", "需要交互终端输入账号和验证码。请解压后双击对应平台启动文件。")
    return key


def ensure_usb(log):
    try:
        version = importlib.metadata.version("pymobiledevice3")
    except importlib.metadata.PackageNotFoundError:
        version = None
    marker = Path(sys.prefix) / "finka-usb-ready.txt"
    if version == MANIFEST["pymobiledevice3"] and marker.is_file():
        return
    if sys.prefix == sys.base_prefix:
        raise HelperError("VENV_REQUIRED", "请使用启动文件自动准备隔离环境。")
    # USB-only module set. Firmware/backup/debugging extras requiring C/Rust are not used.
    run_process([sys.executable, "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
                 "--only-binary=:all:", "--disable-pip-version-check", "-r", ROOT / "requirements-usb.txt"],
                log, "准备 iPhone USB 安装所需组件")
    run_process([sys.executable, "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
                 "--only-binary=:all:", "--no-deps", "--disable-pip-version-check",
                 "pymobiledevice3==" + MANIFEST["pymobiledevice3"]], log, "准备手机通信模块")
    run_process([sys.executable, "-c", "from pymobiledevice3.lockdown import create_using_usbmux; "
                 "from pymobiledevice3.services.installation_proxy import InstallationProxyService"],
                log, "检查手机组件可加载性")
    marker.write_text(MANIFEST["pymobiledevice3"] + "\n", encoding="utf-8")


def apple_driver(log):
    if os.name != "nt":
        return True
    try:
        with socket.create_connection(("127.0.0.1", 27015), timeout=2):
            log.event("APPLE_DRIVER_OK", "已检测到 Apple 手机连接服务。")
            return True
    except OSError:
        log.event("APPLE_DRIVER_MISSING", "未检测到 Apple 手机连接服务。请安装/打开官方 Apple Devices，并在手机点信任。")
    print("官方 Apple Devices：" + APPLE_DEVICES, flush=True)
    choice = answer("回车打开官方商店页面；输入 D 只保留下载包，Q 退出：").lower()
    if choice in ("d", "q"):
        return False
    import webbrowser
    webbrowser.open(APPLE_DEVICES)
    while True:
        if answer("安装/打开 Apple Devices 后按回车重试；D 跳过安装：").lower() == "d":
            return False
        try:
            with socket.create_connection(("127.0.0.1", 27015), timeout=2):
                return True
        except OSError:
            log.event("APPLE_DRIVER_WAIT", "Apple 连接服务仍未就绪，请完成官方驱动安装后再试。")


def worker(arguments, log):
    """Return structured USB events; never log pairing records or all device values."""
    process = subprocess.Popen([sys.executable, "-u", "-m", "helper.device_worker", *map(str, arguments)],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding="utf-8", errors="replace")
    import queue
    import threading
    messages = queue.Queue()
    errors = []
    def read_stdout():
        for line in process.stdout:
            messages.put(line)
        messages.put(None)
    def read_stderr():
        for line in process.stderr:
            errors.append(redact(line.strip(), log.secrets))
            errors[:] = errors[-4:]
    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()
    events = []
    import time
    tick = started = time.monotonic()
    try:
        while True:
            if time.monotonic() - started > (90 if arguments[0] == "list" else 600):
                raise HelperError("DEVICE_TIMEOUT", "手机操作超时，已停止等待。请检查手机提示和数据线后重试；如手机端已出现应用，请先尝试打开。")
            try:
                line = messages.get(timeout=1)
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                events.append(event)
                if event.get("event") == "progress":
                    log.event("INSTALL_PROGRESS", "安装进度：" + str(event.get("percent")) + "%")
                elif event.get("event") == "upload":
                    log.event("IPA_UPLOAD", "正在通过数据线上传完整安装包。", bytes=event.get("bytes"))
            except queue.Empty:
                pass
            if time.monotonic() - tick >= 15:
                log.event("DEVICE_WAIT", "正在等待手机响应；请保持连接、解锁并处理手机上的信任提示。")
                tick = time.monotonic()
        code = process.wait()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise
    failure = next((e for e in reversed(events) if e.get("event") == "error"), None)
    if code or failure:
        error_type = failure.get("error_type", "") if failure else "USB_WORKER"
        message = failure.get("message", "") if failure else " | ".join(errors)
        text = redact(error_type + ": " + message, log.secrets)
        lower = text.lower()
        if any(s in lower for s in ("pair", "passwordprotected", "deviceislocked", "passcode", "userdenied")):
            hint = "请解锁 iPhone，在手机点击信任并输入锁屏密码。"
        elif any(s in lower for s in ("connection", "mux", "notconnected", "nodevice")):
            hint = "请检查数据线、USB端口和 Apple Devices/驱动，再重试。"
        elif any(s in lower for s in ("verification", "license", "authentication")):
            hint = "Apple 拒绝了应用校验或授权，请核对下载账号、手机媒体与购买项目授权和系统版本。"
        elif "space" in lower or "storage" in lower:
            hint = "手机存储空间不足，请释放空间后重试。"
        else:
            hint = "请根据下方手机返回的错误处理后重试。"
        raise HelperError("INSTALL_OR_DEVICE_FAILED", hint + "\n手机返回：" + text)
    return events


def install_flow(package, record, log):
    if not apple_driver(log):
        return {"status": "download_only"}
    ensure_usb(log)
    while True:
        choice = answer("连接要安装的 iPhone，解锁并信任此电脑后按回车；D 仅下载，Q 退出：").lower()
        if choice in ("d", "q"):
            return {"status": "download_only"}
        try:
            events = worker(["list"], log)
            devices = next((e["devices"] for e in events if e.get("event") == "devices"), [])
            if not devices:
                log.event("NO_IPHONE", "没有找到可访问的 USB iPhone。请检查连接、解锁与信任提示。")
                continue
            for index, device in enumerate(devices, 1):
                print(str(index) + ". " + device["name"] + " / " + device["product_type"] + " / iOS " + device["ios"])
            selection = answer("选择目标设备编号（Q退出）：", "1" if len(devices) == 1 else "")
            if selection.lower() == "q":
                return {"status": "download_only"}
            if not selection.isdigit() or not 1 <= int(selection) <= len(devices):
                log.event("BAD_DEVICE_SELECTION", "请输入列表中的设备编号。")
                continue
            device = devices[int(selection) - 1]
            if answer("将在上述设备安装翻咔，不抹除手机。回车继续，Q取消：").lower() == "q":
                return {"status": "download_only"}
            log.event("INSTALL_START", "开始安装到已选择的 iPhone。", product_type=device["product_type"], ios=device["ios"])
            events = worker(["install", "--device", device["udid"], "--package", package], log)
            result = next((e for e in events if e.get("event") in ("installed", "existing")), None)
            if not result:
                raise HelperError("NO_INSTALL_RESULT", "未收到可确认的安装结果，不会报告安装成功。")
            if result["event"] == "installed":
                log.event("INSTALL_OK", "安装成功，已读回核对新机版本。", version=result["version"], build=result["build"])
                notify("翻咔安装成功", "已确认安装及版本。请在手机打开翻咔并自行登录应用账号。")
                return {"status": "installed_and_readback_verified", "version": result["version"], "build": result["build"]}
            log.event("ALREADY_INSTALLED", result["message"], version=result.get("version"))
            return {"status": "existing_application_preserved", "version": result.get("version")}
        except HelperError as error:
            log.event(error.code, error.message)
            notify("翻咔安装未完成", error.message[:350])
            if answer("R 重新检查设备并重试；其他输入结束（IPA会保留）：").lower() != "r":
                return {"status": "installation_failed", "reason": error.message}


def main(argv=None):
    parser = argparse.ArgumentParser(description="翻咔下载与安装助手")
    parser.add_argument("--doctor", action="store_true", help="只检测环境和工具，不登录、不操作手机")
    parser.add_argument("--download-only", action="store_true", help="只下载不安装")
    args = parser.parse_args(argv)
    os.umask(0o077)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = data_root() / "logs" / stamp
    log = Log(run)
    result = {"tool_version": MANIFEST["version"], "status": "started", "app_id": MANIFEST["app_id"]}
    controller = None
    print("翻咔下载与安装助手 " + MANIFEST["version"])
    print("首次运行将从官方来源准备组件。账号密码和验证码只在本机输入，不记录到日志。")
    print("需要曾经获取翻咔的 Apple 帐号；Apple 是否继续提供下载由账号资格和服务器决定。")
    try:
        key = environment(log)
        executable = ensure_ipatool(log)
        if os.name == "nt":
            ensure_package("pywinpty", MANIFEST["pywinpty"], "winpty", log)
        if args.doctor:
            ensure_usb(log)
            result.update(status="environment_ready", platform=key, iphone_driver_check="deferred_until_device_connection")
            log.event("DOCTOR_OK", "环境与组件检测完成；未登录，也未操作手机。")
            return 0
        print("ipatool 会使用本机钥匙串或加密登录缓存；不会把凭据上传给工具作者。")
        email = answer("最初获取翻咔的 Apple 帐号（邮箱或手机号）：")
        if not email:
            raise HelperError("EMPTY_ACCOUNT", "未输入 Apple 帐号，已停止。")
        password = getpass.getpass("Apple 帐号密码（输入不显示）：")
        if not password:
            raise HelperError("EMPTY_PASSWORD", "未输入密码，已停止。")
        log.secrets.update((email, password))
        owner = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
        output = data_root() / "downloads" / owner
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        package, metadata = output / "翻咔-Apple原包.ipa", output / "安装包校验.json"
        controller = Controller(executable, log)
        controller.login(email, password)
        password = ""  # No credentials are passed to a process command line or written by this helper.
        record = valid_cached_package(package, metadata, owner)
        if record:
            log.event("IPA_CACHED", "本账号已有完整且校验通过的IPA，本次复用。", bytes=record["bytes"])
        else:
            partial = output / ("翻咔-下载中-" + stamp + ".ipa")
            log.event("APP_DOWNLOAD", "登录完成，正在向 Apple 请求翻咔原版安装包。")
            downloaded = controller.run(["download", "--app-id", str(MANIFEST["app_id"]), "--platform", "iphone", "--output", str(partial)], mode="download")
            if downloaded.status != "success":
                raise HelperError("APP_DOWNLOAD_FAILED", ERROR_TEXT.get(downloaded.reason, ERROR_TEXT["tool_error"]))
            record = inspect_package(partial)
            # Keep a previous package instead of overwriting it if the account's cached file was invalid.
            if package.exists():
                package.replace(output / ("翻咔-旧文件保留-" + stamp + ".ipa"))
            partial.replace(package)
            record["owner_tag"] = owner
            metadata.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log.event("IPA_VERIFIED", "下载完成：ZIP、应用标识、主程序和SHA-256均已校验。", bytes=record["bytes"], version=record["app"]["CFBundleShortVersionString"])
        result.update(status="download_verified", package_path=str(package), sha256=record["sha256"], bytes=record["bytes"],
                      package_version=record["app"]["CFBundleShortVersionString"])
        print("安装包位置：" + str(package), flush=True)
        notify("翻咔下载完成", "完整IPA已下载并校验，现在可以连接新的 iPhone 进行安装。")
        if not args.download_only:
            result.update(install_flow(package, record, log))
        return 1 if result["status"] == "installation_failed" else 0
    except KeyboardInterrupt:
        result["status"] = "cancelled"
        log.event("CANCELLED", "已取消操作；已有下载文件和日志保留。")
        return 130
    except HelperError as error:
        result.update(status="failed", error_code=error.code, reason=error.message)
        log.event(error.code, error.message)
        notify("翻咔助手未完成操作", error.message[:350])
        return 1
    except Exception as error:
        message = redact(type(error).__name__ + ": " + str(error), log.secrets)
        result.update(status="failed", error_code="UNEXPECTED", reason=message)
        log.event("UNEXPECTED", "操作未完成：" + message)
        return 1
    finally:
        if controller:
            controller.cache_phrase = None
        (run / "执行结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("\n结果：" + result["status"] + "\n脱敏日志：" + str(run), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
