import json
import plistlib
import zipfile
from pathlib import Path

from helper.common import HelperError, MANIFEST, sha256


def inspect_package(path):
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise HelperError("INVALID_IPA", "下载文件不是有效的 IPA ZIP，不能用于安装。")
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise HelperError("ZIP_CRC", "下载包未通过 CRC 校验，可能下载不完整。")
        candidates = [name for name in archive.namelist() if name.startswith("Payload/")
                      and name.count("/") == 2 and name.endswith(".app/Info.plist")]
        selected = None
        for name in candidates:
            info = plistlib.loads(archive.read(name))
            if info.get("CFBundleIdentifier") != MANIFEST["bundle_id"]:
                continue
            executable = name[:-len("Info.plist")] + info.get("CFBundleExecutable", "")
            if not info.get("CFBundleExecutable") or executable not in archive.namelist():
                raise HelperError("NO_EXECUTABLE", "文件只有应用信息，缺少真正的应用程序本体。")
            with archive.open(executable) as binary:
                magic = binary.read(4)
            if magic not in (bytes.fromhex("cffaedfe"), bytes.fromhex("feedfacf"), bytes.fromhex("cafebabe")):
                raise HelperError("BAD_EXECUTABLE", "应用主程序格式与预期不符。")
            selected = {key: info.get(key) for key in ("CFBundleIdentifier", "CFBundleShortVersionString",
                        "CFBundleVersion", "MinimumOSVersion", "CFBundleExecutable")}
            selected["executable_bytes"] = archive.getinfo(executable).file_size
            break
        if selected is None:
            raise HelperError("WRONG_APP", "下载包的应用标识不是翻咔，已停止。")
    return {"bytes": path.stat().st_size, "sha256": sha256(path), "zip_crc": "passed", "app": selected}


def valid_cached_package(path, metadata_path, owner_tag):
    path, metadata_path = Path(path), Path(metadata_path)
    if not path.is_file() or not metadata_path.is_file():
        return None
    try:
        record = json.loads(metadata_path.read_text(encoding="utf-8"))
        if record.get("owner_tag") != owner_tag:
            return None
        checked = inspect_package(path)
        return checked if record.get("sha256") == checked["sha256"] else None
    except (ValueError, OSError, HelperError, zipfile.BadZipFile):
        return None
