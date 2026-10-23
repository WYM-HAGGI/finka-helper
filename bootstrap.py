"""Create a private, versioned venv, then run the assistant. No admin/global pip."""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helper.common import Log, data_root, platform_key


def main():
    log = Log(data_root() / "logs" / ("bootstrap-" + datetime.now().strftime("%Y%m%d-%H%M%S")))
    if not (3, 11) <= sys.version_info[:2] <= (3, 14):
        raise RuntimeError("需要 Python 3.11—3.14；请使用双击启动文件自动准备运行时。")
    key = platform_key()
    environment = data_root() / "venvs" / ("helper-1-py" + str(sys.version_info.major) + str(sys.version_info.minor) + "-" + key)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        log.event("VENV_CREATE", "正在建立本工具专用 Python 环境，首次运行需要一点时间。")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as error:
            log.event("VENV_FAILED", "独立环境创建失败：" + str(error))
            raise
    log.event("VENV_READY", "独立环境就绪，不需要 Xcode，也不修改系统 Python。")
    command = [str(python), "-u", str(Path(__file__).with_name("main.py")), *sys.argv[1:]]
    return subprocess.call(command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("环境准备失败：", str(error), flush=True)
        raise SystemExit(1)
