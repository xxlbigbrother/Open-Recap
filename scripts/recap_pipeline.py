"""Project initialization and resumable orchestration for the complete skill bundle."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def environment():
    env = dict(os.environ)
    path = Path(env.get("RECAP_ENV_FILE", ROOT / ".env")).expanduser().resolve()
    if env.get("RECAP_ENV_FILE") and not path.is_file():
        raise ValueError("RECAP_ENV_FILE does not point to a file")
    for line in path.read_text().splitlines() if path.is_file() else []:
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip("\"'"))
    env["PATH"] = str(ROOT / "tools") + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("UNDERSTANDING_PROVIDER", "aihub-gemini")
    return env


def redact(value, env):
    """Redact both parsed diagnostics and their JSON-escaped log representation."""
    if isinstance(value, dict):
        return {redact(key, env): redact(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, env) for item in value]
    if not isinstance(value, str):
        return value
    for key in ("AIHUB_API_KEY", "APP_ID", "APP_KEY", "MIMO_API_KEY"):
        secret = env.get(key)
        if secret:
            for text in {secret, json.dumps(secret, ensure_ascii=True)[1:-1], json.dumps(secret, ensure_ascii=False)[1:-1]}:
                value = value.replace(text, "[REDACTED]")
    return re.sub(r"data:(?:audio|video|image)/[^;,\s]+;base64,[A-Za-z0-9+/=]+", "[MEDIA OMITTED]", value)


def doctor():
    env = environment()
    missing = []
    if sys.version_info < (3, 11):
        missing.append("Python 3.11+")
    for module in ("requests", "PIL", "imageio_ffmpeg"):
        if importlib.util.find_spec(module) is None:
            missing.append(module)
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool, path=env["PATH"]):
            missing.append(tool)
    if "ffmpeg" not in missing:
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], env=env, capture_output=True, text=True)
        if filters.returncode or " subtitles " not in filters.stdout:
            missing.append("FFmpeg subtitles/libass")
    credentials = {key: bool(env.get(key, "").strip()) for key in ("AIHUB_API_KEY", "APP_ID", "APP_KEY")}
    required = ["APP_ID", "APP_KEY"]
    if env["UNDERSTANDING_PROVIDER"] == "aihub-gemini":
        required.append("AIHUB_API_KEY")
    missing += [key for key in required if not credentials[key]]
    return {"status": "ready" if not missing else "configuration_required", "missing": missing,
            "credentials_configured": credentials, "understanding_provider": env["UNDERSTANDING_PROVIDER"],
            "scope": "Local dependency and configuration check; no model requests were made."}


def initialize(video, work_dir, *, title=None, start=0, end=None):
    video = Path(video).expanduser().resolve()
    work = Path(work_dir).expanduser().resolve()
    if not video.is_file():
        raise ValueError("source video does not exist")
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                             "-of", "json", str(video)], env=environment(), capture_output=True, text=True, check=True)
    media = json.loads(result.stdout)
    duration = float(media["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0 or not any(s.get("codec_type") == "video" for s in media["streams"]):
        raise ValueError("source has no valid video duration")
    if not any(s.get("codec_type") == "audio" for s in media["streams"]):
        raise ValueError("source audio is required for the movie commentary workflow")
    end = duration if end is None else end
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= duration:
        raise ValueError("source range must be inside actual media duration")
    project_path = work / "project.json"
    if project_path.exists():
        old = read(project_path)
        source = old.get("source", {})
        if (project_path.parent / source.get("path", "")).resolve() != video or source.get("range") != [start, end]:
            raise ValueError("existing project differs; choose another work directory")
        if abs(source.get("media_duration", 0) - duration) > .01:
            raise ValueError("existing project source duration changed")
        return project_path
    if work.exists() and any(work.iterdir()):
        raise ValueError("existing project directory is not empty")
    ident = re.sub(r"[^a-zA-Z0-9_-]+", "-", work.name).strip("-") or "recap"
    profile = ROOT / "skills/video-script/references/styles/guided-discovery-v2.json"
    project = {"schema_version": 1, "id": ident, "title": title or video.stem,
               "source": {"id": ident + "-source", "path": os.path.relpath(video, work),
                          "media_origin": 0, "media_duration": duration, "range": [start, end]},
               "understanding_dir": "understanding", "style_path": os.path.relpath(profile, work),
               "audience": "没看过原片也能跟上，并获得额外理解", "reveal_policy": "progressive",
               "authoring_mode": "agent_workflow", "fps": 24,
               "voice": {"provider": "aihub-mimo", "speaker": "茉莉", "tempo": 1}}
    write(project_path, project)
    return project_path


def context(project_path):
    path = Path(project_path).expanduser().resolve()
    project = read(path)
    if project.get("schema_version") != 1 or not re.fullmatch(r"[a-zA-Z0-9_-]+", project.get("id", "")):
        raise ValueError("project requires schema_version 1 and a file-safe id")
    source = (path.parent / project["source"]["path"]).resolve()
    understanding = (path.parent / project["understanding_dir"]).resolve()
    if not source.is_file():
        raise ValueError("project source media is missing")
    return path, project, source, understanding, path.parent / "editorial"


def input_hashes(path, project, source, understanding, candidate=None):
    files = {path, source}
    files.update(understanding.glob("*.json"))
    for key in ("style_path", "verified_notes", "research_path", "development_path", "review_decision_path", "authoring_draft_path"):
        if project.get(key):
            target = (path.parent / project[key]).resolve()
            files.add(target)
            if key == "style_path" and target.is_file():
                files.update((target.parent / name).resolve() for name in read(target).get("case_files", []))
    if candidate:
        files.add(Path(candidate).resolve())
    return {str(p): digest(p) if p.is_file() else None for p in sorted(files)}


def project_status(project_path):
    path, project, source, understanding, work = context(project_path)
    state_path = path.parent / "run_state.json"
    if not state_path.is_file():
        return {"status": "initialized", "project": str(path)}
    state = read(state_path)
    if "input_hashes" not in state:
        return state
    if state.get("input_hashes") != input_hashes(path, project, source, understanding, state.get("candidate")):
        return {**state, "status": "stale", "next_action": "项目或输入已变化，重新运行当前项目。"}
    if state.get("status") == "complete":
        if any(not Path(p).is_file() or digest(p) != sha for p, sha in state.get("delivery_hashes", {}).items()):
            return {**state, "status": "stale", "next_action": "交付文件已变化或缺失，重新运行并检查。"}
    return state


def execute(command, env):
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    stdout, stderr = redact(result.stdout, env), redact(result.stderr, env)
    log_path = None
    if "--work-dir" in command:
        directory = Path(command[command.index("--work-dir") + 1]) / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / f"stage-{uuid.uuid4().hex[:12]}.log"
        log_path.write_text(stdout + "\n" + stderr, encoding="utf-8")
    if stdout:
        print(stdout, end="", flush=True)
    parsed = {}
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\s*\{", result.stdout):
        try:
            value, _ = decoder.raw_decode(result.stdout[match.end() - 1:])
            if isinstance(value, dict) and "status" in value:
                parsed = redact(value, env)
        except ValueError:
            continue
    if result.returncode and parsed.get("status") != "needs_review":
        # Child logs can contain provider data; keep the entrypoint's exception bounded.
        detail = f"; log: {log_path}" if log_path else ""
        raise RuntimeError(f"stage {Path(command[1]).name} failed (exit {result.returncode}){detail}")
    return parsed


def run_pipeline(project_path, *, candidate=None, executor=None):
    path, project, source, understanding, work = context(project_path)
    env = environment()
    if executor is None:
        check = doctor()
        if check["missing"]:
            raise ValueError("缺少配置或依赖: " + ", ".join(check["missing"]))
        executor = execute
    work.mkdir(parents=True, exist_ok=True)
    state_path = path.parent / "run_state.json"
    explicit_candidate = candidate is not None
    prior = read(state_path) if state_path.is_file() else {}
    retained_candidate = prior.get("candidate") if not explicit_candidate else None
    candidate = Path(candidate or retained_candidate).expanduser().resolve() if explicit_candidate or retained_candidate else work / "recap_story_plan.candidate.json"
    if not candidate.is_file():
        # An explicit missing path is an input error, never a request to generate.
        if explicit_candidate or retained_candidate:
            raise ValueError("candidate file does not exist")
        candidate = None
    base = [sys.executable, str(ROOT / "scripts/run_skill.py")]
    state = {"status": "running", "project": str(path), "work_dir": str(work), "candidate": str(candidate) if candidate else None}
    write(state_path, state)
    try:
        required = ["vlm_analysis.json", "asr_result.json", "vlm_analysis.json.meta.json", "asr_result.json.meta.json"]
        if not all((understanding / name).is_file() for name in required):
            executor([*base, "video-understanding", "understand.py", str(source), "--work-dir", str(understanding),
                      "--context", project.get("title", source.stem)], env)
        hashes = input_hashes(path, project, source, understanding, candidate)
        editorial = [*base, "video-script", "editorial.py", "--project", str(path), "--work-dir", str(work)]
        if candidate is None:
            result = executor([*editorial, "--prepare-only"], env)
            if result.get("status") != "prepared":
                state.update(status="needs_review", findings=redact(result.get("findings", []), env), input_hashes=hashes)
            else:
                handoff = work / "AGENT_TASK.md"
                handoff.write_text(
                    f"# 继续制作电影解说\n\n项目：{path}\n证据目录：{result['prepared_dir']}\n\n"
                    f"读取 {ROOT / 'skills/video-script/SKILL.md'}，核对本片理解和原片。检索片名与背景，"
                    "采用前打开来源，将核实知识写入项目 research_path；更新资料后重跑本命令准备新证据。"
                    "先写 author_draft.md 连续稿，再编排有证据的候选。不能把搜索摘要当核实事实。\n\n"
                    f"候选写入：{work / 'recap_story_plan.candidate.json'}\n"
                    f"完成后继续：{shlex.join([sys.executable, str(ROOT / 'run.py'), 'run', '--project', str(path)])}\n"
                    "需要修稿时检查具体 findings，改候选后重跑；技术通过后仍需看片。\n", encoding="utf-8")
                state.update(status="needs_authoring", handoff=str(handoff), prepared_dir=result["prepared_dir"],
                             candidate_path=str(work / "recap_story_plan.candidate.json"), input_hashes=hashes)
        else:
            result = executor([*editorial, "--candidate", str(candidate)], env)
            if result.get("status") != "ready_for_editorial_review":
                state.update(status="needs_review", findings=redact(result.get("findings", []), env), input_hashes=hashes)
            else:
                delivery = path.parent / "delivery"
                executor([sys.executable, str(ROOT / "scripts/editorial_render.py"), "--project", str(path),
                          "--work-dir", str(work), "--output-dir", str(delivery)], env)
                qc = work / "editorial_delivery_qc.json"
                assembly = work / "assembly_manifest.json"
                output = delivery / f"recap_{project['id']}.mp4"
                if not qc.is_file() or read(qc).get("passed") is not True or not assembly.is_file() or read(assembly).get("qc_verdict") != "PASS" or not output.is_file():
                    raise ValueError("delivery QC or output is missing/failed")
                subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], env=env, check=True, capture_output=True)
                state.update(status="complete", output=str(output), input_hashes=hashes,
                             delivery_hashes={str(p): digest(p) for p in (output, qc, assembly)},
                             viewing_status="技术检查与完整解码通过；艺术效果仍需连续看片。")
    except Exception as exc:
        state.update(status="failed", error=redact(str(exc), env))
        write(state_path, state)
        raise
    write(state_path, state)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="OpenRecap 完整技能链：项目、理解、创作接力、配音与成片")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="检查本机依赖与密钥是否配置，不请求模型")
    status = sub.add_parser("status", help="查看当前任务及输入是否变化")
    status.add_argument("--project", required=True)
    for action in ("init", "run"):
        command = sub.add_parser(action, help="初始化项目" if action == "init" else "启动或继续完整流程")
        command.add_argument("--video")
        command.add_argument("--work-dir")
        command.add_argument("--title")
        command.add_argument("--start", type=float, default=0)
        command.add_argument("--end", type=float)
        if action == "run":
            command.add_argument("--project")
            command.add_argument("--candidate", help="已写好的候选；默认使用工作目录内候选")
    args = parser.parse_args(argv)
    try:
        if args.action == "doctor":
            result = doctor()
        elif args.action == "status":
            result = project_status(args.project)
        else:
            project = getattr(args, "project", None)
            if project and (args.video or args.work_dir):
                parser.error("--project 与 --video/--work-dir 二选一")
            if not project:
                if not args.video or not args.work_dir:
                    parser.error("需要 --video 与 --work-dir，或 --project")
                project = initialize(args.video, args.work_dir, title=args.title, start=args.start, end=args.end)
            result = {"status": "initialized", "project": str(project)} if args.action == "init" else run_pipeline(project, candidate=args.candidate)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"ready", "initialized", "needs_authoring", "complete"} else 2
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenRecap: {exc}", file=sys.stderr)
        return 2
