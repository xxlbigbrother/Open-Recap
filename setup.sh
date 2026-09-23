#!/usr/bin/env bash
# macOS/Linux entrypoint; Python handles paths, inspection and safe file creation.
set -eu

case "${1-}" in
  -h|--help)
    printf '%s\n' '用法：bash setup.sh [--check-only]' '可用 PYTHON=/path/to/python3.11 指定 Python；--check-only 仅离线检查，不写入文件。'
    exit 0 ;;
  ''|--check-only) ;;
  *) printf '未知参数：%s；用法：bash setup.sh [--check-only]\n' "$1" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  printf '%s\n' '用法：bash setup.sh [--check-only]' >&2
  exit 2
fi

setup_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
version_probe='import sys; print(".".join(map(str, sys.version_info[:3]))); sys.exit(0 if sys.version_info >= (3, 11) else 1)'
setup_python=''
if [ "${PYTHON+x}" = x ]; then
  if ! setup_version="$("$PYTHON" -I -B -c "$version_probe" 2>/dev/null)"; then
    printf 'PYTHON=%s 无法使用（版本：%s）；需要 Python >=3.11，请指定可执行文件路径。\n' "$PYTHON" "${setup_version:-不可用}" >&2
    exit 1
  fi
  setup_python="$PYTHON"
elif [ -e "$setup_root/.venv" ] || [ -L "$setup_root/.venv" ]; then
  setup_python="$setup_root/.venv/bin/python"
  if ! setup_version="$("$setup_python" -I -B -c "$version_probe" 2>/dev/null)"; then
    printf '已有 .venv 不可用（版本：%s）；需要 Python >=3.11。已保留原环境，请手动修复。\n' "${setup_version:-不可用}" >&2
    exit 1
  fi
else
  for setup_candidate in python3 python3.14 python3.13 python3.12 python3.11 python; do
    if "$setup_candidate" -I -B -c "$version_probe" >/dev/null 2>&1; then
      setup_python="$setup_candidate"
      break
    fi
  done
fi
if [ -z "$setup_python" ]; then
  printf '%s\n' '缺少 Python >=3.11。请先安装，再用 PYTHON=/path/to/python bash setup.sh 指定解释器。' >&2
  exit 1
fi
exec "$setup_python" -I -B "$setup_root/scripts/setup.py" "$@"
