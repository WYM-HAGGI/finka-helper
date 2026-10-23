"""A separate process keeps Apple login secrets out of USB operations."""
import argparse
import asyncio
import json
from pathlib import Path


def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


async def devices():
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux
    found = []
    for device in await list_devices():
        if not device.is_usb:
            continue
        lock = None
        try:
            lock = await create_using_usbmux(serial=device.serial, connection_type="USB", pair_timeout=25)
            values = lock.all_values
            if values.get("ProductType", "").startswith("iPhone"):
                found.append({"udid": device.serial, "name": values.get("DeviceName", "iPhone"),
                              "product_type": values.get("ProductType"), "ios": values.get("ProductVersion")})
        finally:
            if lock:
                await lock.close()
    emit(event="devices", devices=found)


async def install(args):
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    from helper.package import inspect_package
    from helper.common import MANIFEST
    package = inspect_package(args.package)["app"]
    lock = await create_using_usbmux(serial=args.device, connection_type="USB", pair_timeout=25)
    try:
        def version_tuple(value):
            parts = tuple(int(p) for p in value.split("."))
            return (parts + (0, 0, 0))[:3]
        ios = version_tuple(lock.all_values["ProductVersion"])
        minimum = version_tuple(package.get("MinimumOSVersion", "0"))
        if ios < minimum:
            raise RuntimeError("IOS_TOO_OLD: 此包要求 iOS " + package["MinimumOSVersion"] + " 或更高版本")
        async with InstallationProxyService(lock) as service:
            bid = MANIFEST["bundle_id"]
            existing = (await service.get_apps(bundle_identifiers=[bid])).get(bid)
            if existing and not existing.get("IsDemotedApp", False):
                emit(event="existing", version=existing.get("CFBundleShortVersionString"),
                     message="设备已安装翻咔，为保留现有数据，本次不重复安装或替换。")
                return
            def progress(percent):
                emit(event="progress", percent=percent)
            emit(event="upload", bytes=Path(args.package).stat().st_size)
            await service.install_from_local(Path(args.package), developer=False, handler=progress)
            app = (await service.get_apps(bundle_identifiers=[bid])).get(bid)
            if not app or app.get("IsDemotedApp", False):
                raise RuntimeError("READBACK_FAILED: 安装服务结束后未读到实际安装的翻咔")
            if any(str(app.get(k)) != str(package.get(k)) for k in ("CFBundleShortVersionString", "CFBundleVersion")):
                raise RuntimeError("VERSION_MISMATCH: 安装后版本与本次IPA不一致")
            emit(event="installed", bundle_id=bid, version=app.get("CFBundleShortVersionString"),
                 build=app.get("CFBundleVersion"), readback="passed")
    finally:
        await lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["list", "install"])
    parser.add_argument("--device")
    parser.add_argument("--package")
    args = parser.parse_args()
    try:
        asyncio.run(devices() if args.action == "list" else install(args))
        return 0
    except Exception as error:
        # Do not dump all_values, pairing records, or full tracebacks.
        emit(event="error", error_type=type(error).__name__, message=str(error)[:1200])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
