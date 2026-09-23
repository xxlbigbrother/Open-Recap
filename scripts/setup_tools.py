"""Prepare local FFmpeg/ffprobe launchers without bundling machine-specific binaries."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def find_tool(name, tools_dir):
    local = tools_dir / name
    if os.path.lexists(local):
        return local  # Validate exactly the tool the runtime will use, even broken links.
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            selected = imageio_ffmpeg.get_ffmpeg_exe()  # Local discovery; never downloads.
            return Path(shutil.which(selected) or selected).absolute()
        except (ImportError, RuntimeError):
            pass
    selected = shutil.which(name)
    return Path(selected) if selected else None


def tool_problem(name, path):
    if path is None:
        return f"缺少 {name}：tools/ 和 PATH 中均未找到可用程序。"
    args = ["-hide_banner", "-filters"] if name == "ffmpeg" else ["-version"]
    try:
        result = subprocess.run([str(path), *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return f"{name} 无法运行：{path}；请检查权限、架构或失效链接（原文件已保留）。"
    if result.returncode:
        return f"{name} 检查失败：{path}（退出码 {result.returncode}，原文件已保留）。"
    if name == "ffmpeg":
        filters = {fields[1] for line in result.stdout.splitlines() if len(fields := line.split()) >= 2}
        missing = {"subtitles", "ass"} - filters
        if missing:
            return f"ffmpeg 缺少 libass 字幕滤镜：{', '.join(sorted(missing))}（{path}）；请手动安装支持这些滤镜的 FFmpeg。"
    elif "ffprobe version" not in result.stdout:
        return f"ffprobe 版本检查未返回有效结果：{path}。"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-dir", type=Path, default=Path(__file__).resolve().parents[1] / "tools")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if os.name != "posix":
        print("此工具配置入口仅支持 macOS/Linux。")
        return 1
    selected = {name: find_tool(name, args.tools_dir) for name in ("ffmpeg", "ffprobe")}
    problems = [problem for name, path in selected.items() if (problem := tool_problem(name, path))]
    if problems:
        for problem in problems:
            print("[缺少] " + problem)
        print("请自行安装系统 FFmpeg（包含 ffprobe 和 libass）：macOS 可用 brew install ffmpeg；Debian/Ubuntu 可用 sudo apt install ffmpeg，其他 Linux 请使用对应发行版的软件源。此脚本不会执行这些命令。")
        return 1
    if not args.check_only:
        try:
            args.tools_dir.mkdir(parents=True, exist_ok=True)
            for name, target in selected.items():
                output = args.tools_dir / name
                if os.path.lexists(output):
                    print(f"[就绪] {name}：保留已有本地工具。")
                    continue
                try:
                    output.symlink_to(target.resolve())
                except FileExistsError:
                    # Another setup may have populated it while we checked.
                    problem = tool_problem(name, output)
                    if problem:
                        print("[缺少] " + problem)
                        return 1
        except OSError as exc:
            print(f"[失败] 无法配置本地 tools：{exc}")
            return 1
    print("[就绪] ffmpeg 的 subtitles/ass（libass）可用，ffprobe 可用。中文字体请自行准备，本检查不验证字体覆盖。")
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
