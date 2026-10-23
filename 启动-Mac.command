#!/bin/zsh
emulate -L zsh
setopt PIPE_FAIL
FINKA_SCRIPT_DIR=${0:A:h}
cd "$FINKA_SCRIPT_DIR" || exit 1
FINKA_RC=1
function finka_finish() {
  local FINKA_LAST_EXIT=$?
  if [[ -n "${FINKA_BOOT_LOG:-}" ]]; then
    print -r -- "[$(date '+%Y-%m-%d %H:%M:%S')] Launcher exit=$FINKA_LAST_EXIT" >> "$FINKA_BOOT_LOG"
    print -r -- "启动日志：$FINKA_BOOT_LOG"
  fi
  if [[ -t 0 && "${FINKA_HELPER_NO_PAUSE:-0}" != 1 ]]; then
    print '\n操作结束。按回车关闭窗口。'
    read -r FINKA_FINISH_INPUT
  fi
}
trap finka_finish EXIT
print '翻咔一键助手：正在检查 Mac 处理器和运行环境。'
case "$(uname -m)" in
  arm64) FINKA_PLATFORM='macos-arm64' ;;
  x86_64) FINKA_PLATFORM='macos-x64' ;;
  *) print '暂不支持此处理器。'; exit 1 ;;
esac
FINKA_DATA_DIR=${FINKA_HELPER_DATA:-"$HOME/Library/Application Support/FinkaHelper"}
mkdir -p "$FINKA_DATA_DIR/logs" || exit 1
FINKA_BOOT_LOG="$FINKA_DATA_DIR/logs/launcher-$(date '+%Y%m%d-%H%M%S').log"
function finka_log() {
  print -r -- "$1"
  print -r -- "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$FINKA_BOOT_LOG"
}
finka_log "Mac runtime check: $FINKA_PLATFORM"
FINKA_MAC_MAJOR=$(/usr/bin/sw_vers -productVersion | cut -d . -f 1)
[[ "$FINKA_MAC_MAJOR" -ge 11 ]] || { finka_log '需要 macOS 11 或更高版本。'; exit 1; }
FINKA_RUNTIME_DIR="$FINKA_DATA_DIR/python-3.13-$FINKA_PLATFORM"
FINKA_URL=$(/usr/bin/plutil -extract "platforms.$FINKA_PLATFORM.python.url" raw -o - manifest.json) || exit 1
FINKA_HASH=$(/usr/bin/plutil -extract "platforms.$FINKA_PLATFORM.python.sha256" raw -o - manifest.json) || exit 1
FINKA_PYTHON="$FINKA_RUNTIME_DIR/python/bin/python3"
mkdir -p "$FINKA_RUNTIME_DIR" || exit 1
chmod 700 "$FINKA_DATA_DIR" "$FINKA_RUNTIME_DIR"
if [[ ! -x "$FINKA_PYTHON" || ! -f "$FINKA_RUNTIME_DIR/runtime.ready" || "$(cat "$FINKA_RUNTIME_DIR/runtime.ready")" != "$FINKA_HASH" ]]; then
  finka_log '首次运行：从 Astral 官方发布准备独立 Python（约25MB），不需要安装 Xcode。'
  FINKA_ARCHIVE="$FINKA_RUNTIME_DIR/official-python.tar.gz"
  if [[ ! -f "$FINKA_ARCHIVE" || "$(shasum -a 256 "$FINKA_ARCHIVE" | cut -d ' ' -f 1)" != "$FINKA_HASH" ]]; then
    /usr/bin/curl --fail --location --retry 2 --connect-timeout 20 --max-time 1200 --progress-bar "$FINKA_URL" -o "$FINKA_ARCHIVE.partial" || { finka_log '运行时下载失败，请检查网络后重试。'; exit 1; }
    FINKA_ACTUAL=$(shasum -a 256 "$FINKA_ARCHIVE.partial" | cut -d ' ' -f 1)
    [[ "$FINKA_ACTUAL" == "$FINKA_HASH" ]] || { finka_log '运行时校验失败，已停止，不会运行下载内容。'; exit 1; }
    mv "$FINKA_ARCHIVE.partial" "$FINKA_ARCHIVE" || exit 1
  fi
  finka_log '运行时 SHA-256 校验通过，正在解压。'
  /usr/bin/tar -xzf "$FINKA_ARCHIVE" -C "$FINKA_RUNTIME_DIR" || { finka_log '运行时解压失败。'; exit 1; }
  [[ -x "$FINKA_PYTHON" ]] || { print '未找到运行时。'; exit 1; }
  print -r -- "$FINKA_HASH" > "$FINKA_RUNTIME_DIR/runtime.ready"
fi
export PYTHONUTF8=1
finka_log '开始准备独立环境并运行助手。'
"$FINKA_PYTHON" -u "$FINKA_SCRIPT_DIR/bootstrap.py" "$@"
FINKA_RC=$?
exit "$FINKA_RC"
