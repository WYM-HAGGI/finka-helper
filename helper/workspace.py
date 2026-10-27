"""All helper-owned state lives beneath the unpacked bundle."""
import os
import shutil
import uuid
from pathlib import Path

LEAF = ".finka-work"
MARKER = ".finka-workspace"
MAGIC = "FinkaHelper-owned-workspace-v1"
ALLOWED = {"auth", "cache", "downloads", "logs", "pairing", "tmp", "tools", "venvs",
           "python-3.13-macos-arm64", "python-3.13-macos-x64", "python-3.13-windows-x64",
           MARKER, ".running-lock", ".cleanup-request", ".DS_Store", "desktop.ini", "Thumbs.db"}


def workspace_root(bundle=None, create=True):
    bundle = Path(bundle or Path(__file__).resolve().parents[1]).resolve()
    root = bundle / LEAF
    if root.is_symlink():
        raise RuntimeError("工作目录是符号链接，已停止，避免操作其他目录。")
    if not root.exists():
        if not create:
            return root
        root.mkdir(mode=0o700)
    if not root.is_dir() or root.resolve().parent != bundle:
        raise RuntimeError("工作目录边界检查失败。")
    marker = root / MARKER
    if not marker.exists() and create and not any(root.iterdir()):
        marker.write_text(MAGIC + "\n", encoding="ascii")
    if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="ascii").strip() != MAGIC:
        raise RuntimeError("目录没有本工具的所有权标记；不会覆盖或清理未知文件。")
    for entry in root.iterdir():
        if entry.name not in ALLOWED or entry.is_symlink():
            raise RuntimeError("工作区出现未知内容或重定向链接，请先另存并移除该项目：" + entry.name)
    return root


def prepare_environment(bundle=None):
    root = workspace_root(bundle)
    for name in ("cache", "tmp"):
        (root / name).mkdir(exist_ok=True, mode=0o700)
    # Changes apply only to this process and its children, never the user's profile.
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                      PIP_CONFIG_FILE=os.devnull, PIP_CACHE_DIR=str(root / "cache" / "pip"),
                      PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
                      TMPDIR=str(root / "tmp"), TMP=str(root / "tmp"), TEMP=str(root / "tmp"))
    for key in ("PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PYTHONUSERBASE"):
        os.environ.pop(key, None)
    return root


def new_auth_session(root):
    parent = root / "auth"
    parent.mkdir(exist_ok=True, mode=0o700)
    path = parent / ("session-" + uuid.uuid4().hex)
    path.mkdir(mode=0o700)
    (path / MARKER).write_text(MAGIC + "\n", encoding="ascii")
    return path


def remove_auth_session(path, root):
    path, root = Path(path), Path(root)
    if path.is_symlink() or path.parent != root / "auth" or not path.name.startswith("session-"):
        raise RuntimeError("认证缓存边界检查失败。")
    marker = path / MARKER
    if path.exists():
        if marker.is_symlink() or marker.read_text(encoding="ascii").strip() != MAGIC:
            raise RuntimeError("认证缓存缺少所有权标记。")
        shutil.rmtree(path)  # A newly allocated session, never ~/.ipatool or the system keychain.


def request_cleanup(bundle=None):
    root = workspace_root(bundle)
    (root / ".cleanup-request").write_text(MAGIC + "\n", encoding="ascii")


def register_process(root, role, pid=None):
    if role not in ("bootstrap", "main", "worker", "auth"):
        raise ValueError("Unknown process role")
    lock = Path(root) / ".running-lock"
    if not lock.exists():
        return None
    if lock.is_symlink() or not lock.is_dir():
        raise RuntimeError("运行锁目录不可信，已停止。")
    pid = os.getpid() if pid is None else int(pid)
    path = lock / (role + "-pid")
    if path.is_symlink():
        raise RuntimeError("运行锁内容不可信，已停止。")
    path.write_text(str(pid), encoding="ascii")
    return path, pid


def clear_process(record):
    if record:
        path, pid = record
        if not path.is_symlink() and path.is_file() and path.read_text().strip() == str(pid):
            path.unlink()
