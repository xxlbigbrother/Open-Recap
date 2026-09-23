"""Setup contracts exercised offline, with all mutable state in temporary fixtures."""
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import venv

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_setup():
    source = ROOT / "scripts/setup.py"
    assert source.is_file(), "The offline-testable setup orchestrator is missing"
    spec = importlib.util.spec_from_file_location("colleague_setup", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def media_tools(directory, *, filters=True):
    executable(directory / "ffmpeg", "printf ' T.. subtitles V->V subtitle renderer\\n T.. ass V->V ASS renderer\\n'" if filters else "printf ' T.. scale V->V scaler\\n'")
    executable(directory / "ffprobe", "printf 'ffprobe version fixture\\n'")


def snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        content = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
        result[str(path.relative_to(root))] = (info.st_mode, info.st_mtime_ns, content)
    return result


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "colleague project"
    root.mkdir()
    (root / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/setup_tools.py", root / "scripts/setup_tools.py")
    for relative in ("setup.sh", "scripts/setup.py"):
        if (ROOT / relative).exists():
            shutil.copyfile(ROOT / relative, root / relative)
    (root / "requirements.txt").write_text("pytest>=8,<10\n", encoding="utf-8")
    (root / ".env.example").write_text("API_KEY=replace-me\n", encoding="utf-8")
    # No private configuration or model credentials reach child processes.
    for name in list(os.environ):
        monkeypatch.delenv(name)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PYTHON", sys.executable)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    return root


def fixture_venv(root):
    """Make a real disposable venv without installing pip or downloading anything."""
    target = root / ".venv"
    venv.EnvBuilder(with_pip=False).create(target)
    packages = next(target.glob("lib/python*/site-packages"))
    supplied_packages = [path for path in sys.path if path.endswith("site-packages")]
    (packages / "fixture.pth").write_text("\n".join(supplied_packages) + "\n", encoding="utf-8")
    return target / "bin/python"


def prepared_project(root):
    fixture_venv(root)
    media_tools(root / "tools")
    (root / ".env").write_text("API_KEY=existing-secret\n", encoding="utf-8")
    (root / ".env").chmod(0o640)


def shell_setup(root, *args):
    return subprocess.run(["/bin/bash", str(root / "setup.sh"), *args], cwd=root.parent,
                          capture_output=True, text=True, timeout=30)


def test_repeated_setup_preserves_secrets_venv_and_tools(project, capsys):
    setup = load_setup()
    prepared_project(project)
    before = snapshot(project)
    for _ in range(2):
        assert setup.run_setup(project) == 0
        assert snapshot(project) == before
    assert "existing-secret" not in capsys.readouterr().out


def test_new_env_is_private_and_never_replaced(project):
    setup = load_setup()
    prepared_project(project)
    (project / ".env").unlink()
    assert setup.run_setup(project) == 0
    assert (project / ".env").read_bytes() == b"API_KEY=replace-me\n"
    assert stat.S_IMODE((project / ".env").stat().st_mode) == 0o600
    (project / ".env.example").write_text("NEW=value\n", encoding="utf-8")
    assert setup.run_setup(project) == 0
    assert (project / ".env").read_bytes() == b"API_KEY=replace-me\n"


def test_check_only_ready_is_read_only_and_does_not_validate_credentials(project):
    prepared_project(project)
    (project / ".env").write_text("API_KEY=replace-me\n", encoding="utf-8")
    before = snapshot(project)
    result = shell_setup(project, "--check-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(project) == before
    assert "run.py doctor" in result.stdout


def test_check_only_missing_environment_reports_failures_without_writes(project):
    before = snapshot(project)
    result = shell_setup(project, "--check-only")
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert ".venv" in output and ".env" in output and "ffprobe" in output
    assert snapshot(project) == before


@pytest.mark.parametrize("python_kind", ["old", "missing"])
def test_explicit_wrong_python_fails_before_any_write(project, monkeypatch, python_kind):
    selected = project / "old python"
    if python_kind == "old":
        executable(selected, "printf '3.10.14\\n'; exit 1")
    monkeypatch.setenv("PYTHON", str(selected))
    before = snapshot(project)
    result = shell_setup(project)
    assert result.returncode != 0
    assert "Python" in result.stderr and "3.11" in result.stderr
    if python_kind == "old":
        assert "3.10.14" in result.stderr
    assert snapshot(project) == before


@pytest.mark.parametrize("venv_kind", ["old", "broken"])
def test_invalid_existing_venv_is_never_recreated(project, capsys, venv_kind):
    setup = load_setup()
    target = project / ".venv"
    target.mkdir()
    (target / "keep-me").write_text("existing environment", encoding="utf-8")
    if venv_kind == "old":
        executable(target / "bin/python", "printf '3.10.14\\n'; exit 1")
    before = snapshot(project)
    assert setup.run_setup(project) != 0
    assert snapshot(project) == before
    output = capsys.readouterr().out
    assert ".venv" in output
    if venv_kind == "old":
        assert "3.11" in output


@pytest.mark.parametrize("requirement", ["definitely-missing-recap-package>=1", "pytest>=999"])
def test_check_only_rejects_missing_or_wrong_package_version(project, requirement):
    setup = load_setup()
    prepared_project(project)
    (project / "requirements.txt").write_text(requirement + "\n", encoding="utf-8")
    before = snapshot(project)
    assert setup.run_setup(project, check_only=True) != 0
    assert snapshot(project) == before


def test_first_setup_creates_venv_and_installs_root_requirements(project, monkeypatch):
    setup = load_setup()
    media_tools(project / "tools")
    actual_run = subprocess.run
    commands = []

    def offline_run(command, *args, **kwargs):
        words = list(map(str, command))
        if "venv" in words and "-m" in words:
            commands.append(words)
            fixture_venv(project)
            return subprocess.CompletedProcess(command, 0)
        if "pip" in words and "install" in words:
            commands.append(words)
            return subprocess.CompletedProcess(command, 0)
        return actual_run(command, *args, **kwargs)

    monkeypatch.setattr(setup.subprocess, "run", offline_run)
    assert setup.run_setup(project) == 0
    assert (project / ".venv/bin/python").exists()
    assert (project / ".env").exists()
    assert len(commands) == 2
    assert "venv" in commands[0] and commands[0][-1] == str(project / ".venv")
    assert commands[1][0] == str(project / ".venv/bin/python")
    assert commands[1][-2:] == ["-r", str(project / "requirements.txt")]


def test_failed_dependency_install_stops_without_copying_env_or_replacing_venv(project, monkeypatch):
    setup = load_setup()
    prepared_project(project)
    (project / ".env").unlink()
    (project / "requirements.txt").write_text("definitely-missing-recap-package>=1\n", encoding="utf-8")
    before = snapshot(project)
    actual_run = subprocess.run

    def offline_run(command, *args, **kwargs):
        if "pip" in command and "install" in command:
            raise subprocess.CalledProcessError(1, command)
        return actual_run(command, *args, **kwargs)

    monkeypatch.setattr(setup.subprocess, "run", offline_run)
    assert setup.run_setup(project) != 0
    assert snapshot(project) == before


def test_check_only_reports_unconfigured_local_tools_even_when_candidates_exist(project, monkeypatch, capsys):
    setup = load_setup()
    prepared_project(project)
    (project / "tools").rename(project / "system bin")
    monkeypatch.setenv("PATH", str(project / "system bin"))
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(project / "system bin/ffmpeg"))
    before = snapshot(project)
    assert setup.run_setup(project, check_only=True) != 0
    assert "tools/ffmpeg" in capsys.readouterr().out
    assert snapshot(project) == before


def test_check_only_reports_missing_venv_module_before_any_creation(project, monkeypatch, capsys):
    setup = load_setup()
    actual_run = subprocess.run

    def missing_venv(command, *args, **kwargs):
        if "-c" in command and "import venv, ensurepip" in command:
            return subprocess.CompletedProcess(command, 1, "", "No module named ensurepip")
        return actual_run(command, *args, **kwargs)

    monkeypatch.setattr(setup.subprocess, "run", missing_venv)
    before = snapshot(project)
    assert setup.run_setup(project, check_only=True) != 0
    assert "ensurepip" in capsys.readouterr().out
    assert snapshot(project) == before


def test_check_only_never_invokes_installer_creator_or_app_entrypoints(project, monkeypatch):
    setup = load_setup()
    prepared_project(project)
    (project / "requirements.txt").write_text("definitely-missing-recap-package>=1\n", encoding="utf-8")
    actual_run = subprocess.run

    def read_only_run(command, *args, **kwargs):
        words = list(map(str, command))
        assert not {"install", "venv", "ensurepip", "brew", "apt", "curl", "wget"}.intersection(words)
        assert not any(word.endswith(("run.py", "run_skill.py")) for word in words)
        return actual_run(command, *args, **kwargs)

    monkeypatch.setattr(setup.subprocess, "run", read_only_run)
    before = snapshot(project)
    assert setup.run_setup(project, check_only=True) != 0
    assert snapshot(project) == before


def test_shell_reuses_existing_venv_without_python_override(project, monkeypatch):
    prepared_project(project)
    monkeypatch.delenv("PYTHON")
    before = snapshot(project)
    result = shell_setup(project, "--check-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(project) == before


def test_new_env_has_exact_private_permissions_even_with_restrictive_umask(project):
    setup = load_setup()
    previous_umask = os.umask(0o777)
    try:
        assert setup.prepare_env(project, check_only=False)
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE((project / ".env").stat().st_mode) == 0o600


def test_existing_env_symlink_is_preserved(project):
    setup = load_setup()
    prepared_project(project)
    (project / ".env").unlink()
    # Even a dangling link belongs to the colleague and must not be overwritten.
    (project / ".env").symlink_to("not-yet-mounted.env")
    before = snapshot(project)
    assert setup.run_setup(project) != 0
    assert snapshot(project) == before


def tool_helper(root, *args, env=None):
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/setup_tools.py"),
                           "--tools-dir", str(root / "tools"), *args],
                          env=env, capture_output=True, text=True, timeout=30)


def test_helper_preserves_and_validates_actual_local_tools(project):
    media_tools(project / "tools")
    before = snapshot(project)
    result = tool_helper(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(project) == before


@pytest.mark.parametrize("fault, expected", [("filters", "libass"), ("broken", "ffmpeg"), ("probe", "ffprobe")])
def test_helper_rejects_broken_preserved_tools(project, fault, expected):
    media_tools(project / "tools", filters=fault != "filters")
    if fault == "broken":
        (project / "tools/ffmpeg").unlink()
        (project / "tools/ffmpeg").symlink_to("missing-binary")
    if fault == "probe":
        executable(project / "tools/ffprobe", "exit 1")
    before = snapshot(project)
    result = tool_helper(project, "--check-only")
    assert result.returncode != 0
    assert expected in result.stdout + result.stderr
    assert snapshot(project) == before


def test_helper_links_discovered_tools_and_check_only_never_links(project):
    media_tools(project / "system bin")
    env = dict(os.environ, PATH=str(project / "system bin"),
               IMAGEIO_FFMPEG_EXE=str(project / "system bin/ffmpeg"))
    before = snapshot(project)
    assert tool_helper(project, "--check-only", env=env).returncode == 0
    assert snapshot(project) == before
    assert tool_helper(project, env=env).returncode == 0
    assert (project / "tools/ffmpeg").resolve() == project / "system bin/ffmpeg"
    assert (project / "tools/ffprobe").resolve() == project / "system bin/ffprobe"
    after = snapshot(project)
    assert tool_helper(project, env=env).returncode == 0
    assert snapshot(project) == after


def test_helper_names_missing_ffprobe_and_does_not_create_tools(project):
    executable(project / "ffmpeg", "printf ' T.. subtitles V->V subtitle renderer\\n T.. ass V->V ASS renderer\\n'")
    env = dict(os.environ, PATH="", IMAGEIO_FFMPEG_EXE=str(project / "ffmpeg"))
    result = tool_helper(project, env=env)
    assert result.returncode != 0
    assert "ffprobe" in result.stdout + result.stderr
    assert not (project / "tools").exists()
