#!/bin/zsh
emulate -L zsh
setopt PIPE_FAIL
FINKA_BUNDLE=${0:A:h}
source "$FINKA_BUNDLE/scripts/workspace.zsh" || exit 1
FINKA_YES=0
FINKA_PAUSE=1
for FINKA_ARG in "$@"; do
  case "$FINKA_ARG" in
    --yes) FINKA_YES=1 ;;
    --no-pause) FINKA_PAUSE=0 ;;
    *) print '未知清理参数。'; exit 1 ;;
  esac
done
function finka_cleanup_finish() {
  finka_release_lock
  if [[ "$FINKA_PAUSE" == 1 && -t 0 ]]; then
    print '按回车关闭窗口。'
    read -r FINKA_FINISH_INPUT
  fi
}
trap finka_cleanup_finish EXIT
if [[ ! -e "$FINKA_WORK" && ! -L "$FINKA_WORK" ]]; then
  print '没有本版工作目录，无需清理。解压目录和最初下载的ZIP可自行删除。'
  exit 0
fi
finka_cleanup_preflight || exit 1
print -r -- "即将永久删除：$FINKA_WORK"
print '包含专用Python、下载工具、IPA、登录缓存、本工具的配对副本、临时文件和日志。'
print '请先另存需要保留的IPA或日志。不会卸载iPhone应用、Apple Devices或修改系统注册表。'
if [[ "$FINKA_YES" != 1 ]]; then
  read -r 'FINKA_CHOICE?输入 CLEAN 确认；回车保留：'
  [[ "$FINKA_CHOICE" == CLEAN ]] || { print '已保留工作目录。'; exit 0; }
fi
finka_acquire_lock || exit 1
finka_cleanup_preflight || exit 1
cd "$FINKA_BUNDLE" || exit 1
# The exact marked child is removed; rm does not traverse symlinks inside it.
/bin/rm -rf -- "$FINKA_WORK" || { print '清理未完成，请关闭使用文件的程序后重试。'; exit 1; }
[[ ! -e "$FINKA_WORK" && ! -L "$FINKA_WORK" ]] || { print '仍有残留，请查看权限或占用情况。'; exit 1; }
FINKA_LOCK_OWNED=0
print '工作目录清理完成。现在可以直接删除整个解压文件夹和原始ZIP。'
print '若使用过1.0.0，旧版资料的检查方法见《清理与卸载说明.md》。'
