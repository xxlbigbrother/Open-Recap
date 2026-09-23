"""Run upstream stage entrypoints with Gemini understanding and independent speech/editorial routes."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
env = dict(os.environ)
env_file = Path(env.get('RECAP_ENV_FILE', str(ROOT / '.env'))).expanduser()
if env.get('RECAP_ENV_FILE') and not env_file.is_file():
    raise SystemExit('RECAP_ENV_FILE does not point to a file')
for line in env_file.read_text().splitlines() if env_file.exists() else []:
    if line and not line.startswith("#") and "=" in line:
        name, value = line.split("=", 1)
        env.setdefault(name.strip(), value.strip().strip("\"'"))
env.setdefault('UNDERSTANDING_PROVIDER', 'aihub-gemini')
env.update({"VIDEO_RECAP_PROVIDER": "aihub-doubao",
            "PYTHONPATH": str(ROOT / "scripts"), "PATH": str(ROOT / "tools") + os.pathsep + env.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1", "NARRATION_SPEED": "1.0",
            "SUBTITLE_FONT_SIZE": "76", "SUBTITLE_MAX_CHARS": "22"})
env.setdefault('VLM_WORKERS', '2')
args = sys.argv[1:]
if not args or args[0] in {'-h','--help'}:
    print("Usage: run_skill.py <skill-name> <script.py> [arguments...]")
    raise SystemExit(0 if args else 2)
if len(args) < 2:
    raise SystemExit("Provide both skill-name and script.py")
stage, script, *rest = args
entry = ROOT / "skills" / stage / "scripts" / script
if not entry.is_file() or not entry.resolve().is_relative_to(ROOT / "skills"):
    raise SystemExit("Unknown skill entrypoint")
raise SystemExit(subprocess.call([sys.executable, str(entry), *rest], env=env))
