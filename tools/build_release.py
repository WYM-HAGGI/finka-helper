#!/usr/bin/env python3
"""Build a clean source-only launcher ZIP; downloaded tools remain separate assets."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "README.md", "先读我.md", "清理与卸载说明.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "启动-Mac.command", "清理-Mac.command", "Start-Windows.cmd", "Cleanup-Windows.cmd",
    "launcher.ps1", "cleanup.ps1", "scripts/workspace.zsh", "scripts/Workspace.ps1",
    "bootstrap.py", "main.py", "manifest.json", "requirements-usb.txt",
    "helper/common.py", "helper/auth.py", "helper/terminal.py", "helper/package.py",
    "helper/device_worker.py", "helper/workspace.py",
    "tests/test_helper.py", "tests/test_workspace.py", "tests/fake_ipatool.py",
    "tools/build_release.py", "tools/build_portable_ipatool.py",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = json.loads((ROOT / "manifest.json").read_text())["version"]
    target = output / ("FinkaHelper-v" + version + ".zip")
    rows = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in FILES:
            data = (ROOT / relative).read_bytes()
            if str(Path.home()).encode() in data or (b"-----BEGIN " + b"PRIVATE KEY-----") in data:
                raise RuntimeError("Unexpected personal path or key: " + relative)
            item = zipfile.ZipInfo("FinkaHelper/" + relative, (2026, 10, 27, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (0o100755 if relative.endswith(".command") else 0o100644) << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, data)
            rows.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        archive.writestr("FinkaHelper/SHA256-files.txt",
                         "\n".join(r["sha256"] + "  " + r["path"] for r in rows) + "\n")
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / "FinkaHelper-package.json").write_text(
        json.dumps({"file": target.name, "version": version, "bytes": target.stat().st_size,
                    "sha256": digest, "files": rows}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"file": str(target), "bytes": target.stat().st_size, "sha256": digest,
                      "source_files": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
