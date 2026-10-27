# Shared by the Mac launcher and cleanup entry. Never executes Python.
FINKA_MAGIC='FinkaHelper-owned-workspace-v1'
FINKA_WORK="$FINKA_BUNDLE/.finka-work"
FINKA_LOCK_OWNED=0

function finka_validate() {
  [[ ! -L "$FINKA_WORK" && -d "$FINKA_WORK" ]] || { print '工作目录不是普通目录，已停止。'; return 1; }
  [[ "${FINKA_WORK:A}" == "$FINKA_BUNDLE/.finka-work" ]] || { print '工作目录边界检查失败。'; return 1; }
  [[ ! -L "$FINKA_WORK/.finka-workspace" && -f "$FINKA_WORK/.finka-workspace" ]] || { print '缺少工作目录所有权标记。'; return 1; }
  [[ "$(cat "$FINKA_WORK/.finka-workspace")" == "$FINKA_MAGIC" ]] || { print '工作目录标记不匹配。'; return 1; }
  local FINKA_TOP
  for FINKA_TOP in "$FINKA_WORK"/*(DN); do
    [[ ! -L "$FINKA_TOP" ]] || { print '工作区顶层存在重定向链接，已停止。'; return 1; }
  done
}

function finka_initialize() {
  [[ ! -L "$FINKA_WORK" ]] || { print '工作目录为符号链接，已停止。'; return 1; }
  if [[ ! -e "$FINKA_WORK" ]]; then
    mkdir -m 700 "$FINKA_WORK" || return 1
  fi
  [[ -d "$FINKA_WORK" ]] || return 1
  if [[ ! -e "$FINKA_WORK/.finka-workspace" ]]; then
    [[ -z "$(find "$FINKA_WORK" -mindepth 1 -maxdepth 1 -print -quit)" ]] || { print '已有未知文件，不接管该目录。'; return 1; }
    print -r -- "$FINKA_MAGIC" > "$FINKA_WORK/.finka-workspace"
  fi
  finka_validate
}

function finka_release_lock() {
  if [[ "$FINKA_LOCK_OWNED" == 1 && -d "$FINKA_WORK/.running-lock" && ! -L "$FINKA_WORK/.running-lock" ]]; then
    if [[ "$(cat "$FINKA_WORK/.running-lock/pid" 2>/dev/null)" == "$$" ]]; then
      finka_check_lock 1 || return 1
      /bin/rm -f "$FINKA_WORK/.running-lock/"*pid(N)
      rmdir "$FINKA_WORK/.running-lock" 2>/dev/null
    fi
  fi
  FINKA_LOCK_OWNED=0
}

function finka_check_lock() {
  local FINKA_IGNORE_SELF=${1:-0} FINKA_PID_FILE FINKA_PROCESS_PID
  for FINKA_PID_FILE in "$FINKA_WORK/.running-lock"/*(DN); do
    case "${FINKA_PID_FILE:t}" in
      pid|bootstrap-pid|main-pid|worker-pid|auth-pid) ;;
      *) print '运行锁内有未知内容，已停止。'; return 1 ;;
    esac
    [[ ! -L "$FINKA_PID_FILE" && -f "$FINKA_PID_FILE" ]] || return 1
    FINKA_PROCESS_PID=$(cat "$FINKA_PID_FILE")
    [[ "$FINKA_PROCESS_PID" == <-> ]] || return 1
    if [[ "$FINKA_IGNORE_SELF" == 1 && "$FINKA_PROCESS_PID" == "$$" ]]; then continue; fi
    if kill -0 "$FINKA_PROCESS_PID" 2>/dev/null; then
      print '助手子进程仍在运行，请等待或结束相关窗口后重试。'
      return 1
    fi
  done
}

function finka_acquire_lock() {
  finka_validate || return 1
  local FINKA_OLD_PID
  if ! mkdir "$FINKA_WORK/.running-lock" 2>/dev/null; then
    [[ ! -L "$FINKA_WORK/.running-lock" && -d "$FINKA_WORK/.running-lock" ]] || return 1
    FINKA_OLD_PID=$(cat "$FINKA_WORK/.running-lock/pid" 2>/dev/null)
    if [[ "$FINKA_OLD_PID" != <-> ]] || kill -0 "$FINKA_OLD_PID" 2>/dev/null; then
      print '助手或清理任务仍在运行。请结束原窗口后重试，不会删除使用中的文件。'
      return 1
    fi
    finka_check_lock || return 1
    /bin/rm -f "$FINKA_WORK/.running-lock/"*pid(N)
    rmdir "$FINKA_WORK/.running-lock" || return 1
    mkdir "$FINKA_WORK/.running-lock" || return 1
  fi
  print -r -- "$$" > "$FINKA_WORK/.running-lock/pid"
  FINKA_LOCK_OWNED=1
}

function finka_cleanup_preflight() {
  finka_validate || return 1
  local FINKA_ENTRY FINKA_NAME
  for FINKA_ENTRY in "$FINKA_WORK"/*(DN); do
    FINKA_NAME=${FINKA_ENTRY:t}
    case "$FINKA_NAME" in
      auth|cache|downloads|logs|pairing|tmp|tools|venvs|python-3.13-macos-arm64|python-3.13-macos-x64|python-3.13-windows-x64|.finka-workspace|.running-lock|.cleanup-request|.DS_Store|desktop.ini|Thumbs.db) ;;
      *) print -r -- "工作区出现未知内容，停止清理：$FINKA_NAME"; return 1 ;;
    esac
  done
  local FINKA_GIT_PATH
  FINKA_GIT_PATH=$(find -P "$FINKA_WORK" -name .git -print -quit) || return 1
  [[ -z "$FINKA_GIT_PATH" ]] || { print '工作区内发现Git资料，不自动清理。'; return 1; }
}
