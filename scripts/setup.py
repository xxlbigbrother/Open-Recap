"""Idempotent local setup; --check-only only inspects files and installed software."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


# Run in the project's interpreter, without importing application code or .env.
REQUIREMENTS_CHECK = r'''
import sys
from importlib.metadata import PackageNotFoundError, version
try:
    from pip._vendor.packaging.requirements import Requirement
except ImportError:
    print("缺少 pip（请修复此虚拟环境的 pip/ensurepip）。")
    sys.exit(1)
problems = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    try:
        requirement = Requirement(line)
    except ValueError:
        problems.append("无法离线检查 requirements.txt 条目：" + line)
        continue
    if requirement.marker and not requirement.marker.evaluate():
        continue
    try:
        installed = version(requirement.name)
    except PackageNotFoundError:
        problems.append("缺少 Python 包：" + str(requirement))
        continue
    if not requirement.specifier.contains(installed, prereleases=True):
        problems.append(f"Python 包版本不符：{requirement}（已安装 {installed}）")
print("\n".join(problems))
sys.exit(bool(problems))
'''


def inspect_python(python):
    code = (
        'import sys; print(".".join(map(str, sys.version_info[:3]))); '
        'sys.exit(0 if sys.version_info >= (3, 11) and sys.prefix != sys.base_prefix else 1)'
    )
    try:
        result = subprocess.run([str(python), "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return "已有 .venv/bin/python 无法运行；需要 Python >=3.11 的虚拟环境。原环境已保留，请手动修复。"
    if result.returncode:
        return f"已有 .venv 不是可用的 Python >=3.11 虚拟环境（版本：{result.stdout.strip() or '未知'}）。原环境已保留，请手动修复。"
    return None


def dependency_problems(python, requirements):
    result = subprocess.run([str(python), "-I", "-B", "-c", REQUIREMENTS_CHECK, str(requirements)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        return result.stdout.strip() or "无法检查 Python 依赖，请修复 .venv 中的 pip。"
    # pip check reads installed metadata only. Disable config, cache and version checks.
    env = dict(os.environ, PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([str(python), "-I", "-B", "-m", "pip", "--disable-pip-version-check",
                             "--no-cache-dir", "check"], env=env,
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        return "Python 依赖不完整或冲突：\n" + result.stdout.strip()
    return None


def prepare_env(root, check_only):
    target = root / ".env"
    if os.path.lexists(target):
        if not target.is_file() or not os.access(target, os.R_OK):
            print("[缺少] .env 不是可读文件（可能是失效链接）；已保留，请手动修复。")
            return False
        print("[就绪] 已保留现有 .env，不读取或显示凭据。")
        return True
    if check_only:
        print("[缺少] .env；运行 bash setup.sh 可从 .env.example 创建。")
        return False
    # O_EXCL also protects dangling symlinks and concurrent setup invocations.
    with (root / ".env.example").open("rb") as source:
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return prepare_env(root, check_only=True)
        with os.fdopen(fd, "wb") as destination:
            os.fchmod(destination.fileno(), 0o600)
            shutil.copyfileobj(source, destination)
    print("[完成] 已创建 .env（权限 0600），请填写自己的凭据。")
    return True


def run_setup(root, check_only=False):
    root = Path(root).resolve()
    if sys.version_info < (3, 11) or sys.platform not in {"darwin", "linux"}:
        print("[缺少] 本安装入口需要 macOS/Linux 和 Python >=3.11。")
        return 1
    requirements = root / "requirements.txt"
    if not requirements.is_file():
        print(f"[缺少] {requirements}；请恢复完整项目。")
        return 1
    target = root / ".venv"
    python = target / "bin/python"
    ready = True
    try:
        created = False
        if os.path.lexists(target):
            problem = inspect_python(python)
            if problem:
                print("[缺少] " + problem)
                return 1
            print("[就绪] 复用已有 .venv。")
        else:
            probe = subprocess.run([sys.executable, "-I", "-B", "-c", "import venv, ensurepip"],
                                   capture_output=True, text=True, timeout=15)
            if probe.returncode:
                version = f"{sys.version_info.major}.{sys.version_info.minor}"
                print(f"[缺少] Python {version} 的 venv/ensurepip。Debian/Ubuntu 通常需手动安装 python{version}-venv；其他系统请安装包含 venv 和 pip 的完整 Python。")
                if not check_only:
                    return 1
            if check_only:
                print("[缺少] .venv；运行 bash setup.sh 创建 Python 虚拟环境并安装 requirements.txt。")
                ready = False
            else:
                subprocess.run([sys.executable, "-I", "-B", "-m", "venv", str(target)], check=True)
                created = True
                print("[完成] 已创建 .venv。")

        if python.is_file():
            problem = dependency_problems(python, requirements)
            if not check_only and (created or problem):
                print("[安装] requirements.txt 中的 Python 依赖；保留已有虚拟环境。", flush=True)
                subprocess.run([str(python), "-I", "-B", "-m", "pip", "--disable-pip-version-check",
                                "--no-cache-dir", "install", "-r", str(requirements)], check=True)
                problem = dependency_problems(python, requirements)
            if problem:
                print("[缺少] " + problem)
                ready = False
            else:
                print("[就绪] requirements.txt 和已安装依赖检查通过。")

        # Even without .venv, report missing local/system tools in check-only mode.
        tool_python = python if python.is_file() else Path(sys.executable)
        command = [str(tool_python), "-I", "-B", str(root / "scripts/setup_tools.py"),
                   "--tools-dir", str(root / "tools")]
        if check_only:
            command.append("--check-only")
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        ready = result.returncode == 0 and ready
        if check_only:
            for name in ("ffmpeg", "ffprobe"):
                if not os.path.lexists(root / "tools" / name):
                    print(f"[缺少] tools/{name} 尚未配置；运行 bash setup.sh 创建本地工具链接。")
                    ready = False
        ready = prepare_env(root, check_only) and ready
    except subprocess.CalledProcessError as exc:
        step = "Python 依赖安装" if "pip" in exc.cmd else ".venv 创建"
        print(f"[失败] {step}失败（退出码 {exc.returncode}），请按上方错误修复后重试；已有文件未删除。")
        return 1
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[失败] 本地安装/检查无法完成：{exc}")
        return 1
    print("凭据与服务检查是独立步骤：填写 .env 后，在项目根目录运行 .venv/bin/python run.py doctor；本命令不验证密钥或请求模型。")
    print("[完成] 本地依赖已就绪。" if ready else "[未完成] 请处理以上缺项后重试；不会自动安装系统软件包。")
    return 0 if ready else 1


def main():
    parser = argparse.ArgumentParser(description="安装本地依赖；保留现有 .env、.venv 和 tools。")
    parser.add_argument("--check-only", action="store_true", help="仅离线检查，不写入文件或安装依赖")
    args = parser.parse_args()
    return run_setup(Path(__file__).resolve().parents[1], check_only=args.check_only)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
