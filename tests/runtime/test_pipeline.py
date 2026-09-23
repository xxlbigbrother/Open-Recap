"""Colleague entrypoint: real project artifacts, external stages substituted at the boundary."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def pipeline():
    path = ROOT / "scripts/recap_pipeline.py"
    assert path.is_file(), "unified pipeline is missing"
    spec = importlib.util.spec_from_file_location("recap_pipeline_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source(tmp_path):
    path = tmp_path / "movie sample.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "2",
        "-c:v", "libx264", "-c:a", "aac", str(path),
    ], check=True)
    return path


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def test_init_measures_media_and_uses_portable_project_paths(tmp_path):
    module = pipeline()
    video = source(tmp_path)
    project_path = module.initialize(video, tmp_path / "job", title="示例电影", start=.5, end=1.8)
    project = json.loads(project_path.read_text())
    assert project["source"]["range"] == [.5, 1.8]
    assert 1.9 < project["source"]["media_duration"] < 2.2
    assert (project_path.parent / project["source"]["path"]).resolve() == video
    assert project["voice"] == {"provider": "aihub-mimo", "speaker": "茉莉", "tempo": 1}
    assert (project_path.parent / project["style_path"]).is_file()
    assert module.initialize(video, tmp_path / "job", title="示例电影", start=.5, end=1.8) == project_path
    before = project_path.read_bytes()
    with pytest.raises(ValueError, match="existing project"):
        module.initialize(video, tmp_path / "job", title="示例电影", start=0, end=1)
    assert project_path.read_bytes() == before


def test_invalid_range_writes_no_project(tmp_path):
    module = pipeline()
    with pytest.raises(ValueError, match="range"):
        module.initialize(source(tmp_path), tmp_path / "bad", title="电影", end=9)
    assert not (tmp_path / "bad/project.json").exists()


def prepared_project(tmp_path):
    module = pipeline()
    project = module.initialize(source(tmp_path), tmp_path / "job", title="电影")
    cfg = json.loads(project.read_text())
    understanding = (project.parent / cfg["understanding_dir"]).resolve()
    for name in ["vlm_analysis.json", "asr_result.json"]:
        write(understanding / name, [])
        write(understanding / (name + ".meta.json"), {"source_video_fingerprint": "fixture"})
    return module, project


def test_agent_run_prepares_handoff_without_automatic_authoring(tmp_path):
    module, project = prepared_project(tmp_path)

    def stage(command, env):
        assert "editorial.py" in command and "--prepare-only" in command
        work = Path(command[command.index("--work-dir") + 1])
        prepared = work / "attempts/fixture"
        write(prepared / "evidence_bundle.json", {"records": []})
        write(prepared / "style_snapshot.json", {})
        return {"status": "prepared", "prepared_dir": str(prepared)}

    result = module.run_pipeline(project, executor=stage)
    assert result["status"] == "needs_authoring"
    assert Path(result["handoff"]).is_file()
    assert Path(result["prepared_dir"]).is_dir()
    assert Path(result["candidate_path"]).parent.is_dir()
    assert not Path(result["candidate_path"]).exists()
    assert module.project_status(project)["status"] == "needs_authoring"


def test_failed_candidate_never_renders_previous_accepted_artifacts(tmp_path):
    module, project = prepared_project(tmp_path)
    candidate = project.parent / "new.candidate.json"
    write(candidate, {"invalid": True})
    work = project.parent / "editorial"
    write(work / "editorial_run.json", {"status": "ready_for_editorial_review"})
    write(work / "recap_story_plan.json", {"old": "keep"})
    old = (work / "recap_story_plan.json").read_bytes()

    def stage(command, env):
        assert "editorial.py" in command and "--candidate" in command
        return {"status": "needs_review", "findings": [{"code": "bad_evidence"}]}

    result = module.run_pipeline(project, candidate=candidate, executor=stage)
    assert result["status"] == "needs_review"
    assert result["findings"][0]["code"] == "bad_evidence"
    assert (work / "recap_story_plan.json").read_bytes() == old


def test_success_requires_delivery_qc_and_real_output_then_detects_changed_candidate(tmp_path):
    module, project = prepared_project(tmp_path)
    candidate = project.parent / "candidate.json"
    write(candidate, {"version": 1})
    work = project.parent / "editorial"
    output = project.parent / "delivery/recap_job.mp4"

    def stage(command, env):
        if "editorial.py" in command:
            write(work / "editorial_run.json", {"status": "ready_for_editorial_review"})
            return {"status": "ready_for_editorial_review"}
        assert Path(command[1]).name == "editorial_render.py"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(project.parent.parent.joinpath("movie sample.mp4").read_bytes())
        write(work / "editorial_delivery_qc.json", {"passed": True})
        write(work / "assembly_manifest.json", {"qc_verdict": "PASS", "output_path": str(output)})
        return {}

    result = module.run_pipeline(project, candidate=candidate, executor=stage)
    assert result["status"] == "complete"
    assert result["output"] == str(output)
    assert module.project_status(project)["status"] == "complete"
    candidate.write_text('{"version":2}')
    assert module.project_status(project)["status"] == "stale"


def test_executor_does_not_treat_failed_stage_as_success(tmp_path):
    module = pipeline()
    result = module.execute([sys.executable, "-c", 'print("diagnostic\\n{\\"status\\": \\"needs_review\\", \\"findings\\": []}"); raise SystemExit(2)'], {})
    assert result["status"] == "needs_review"
    with pytest.raises(RuntimeError, match="stage"):
        module.execute([sys.executable, "-c", 'print("no result"); raise SystemExit(1)'], {})


def test_status_detects_new_evidence_files_and_missing_candidate(tmp_path):
    module, project = prepared_project(tmp_path)
    def stage(command, env):
        return {"status": "prepared", "prepared_dir": str(project.parent / "editorial/attempt")}
    module.run_pipeline(project, executor=stage)
    write(project.parent / "understanding/understanding_index.json", {"new": "context"})
    assert module.project_status(project)["status"] == "stale"
    with pytest.raises(ValueError, match="candidate"):
        module.run_pipeline(project, candidate=project.parent / "missing.json", executor=stage)


def test_initialization_rejects_audio_less_media_before_creating_project(tmp_path):
    module = pipeline()
    video = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=160x90:r=24", "-t", "1", str(video)], check=True)
    with pytest.raises(ValueError, match="audio"):
        module.initialize(video, tmp_path / "job")
    assert not (tmp_path / "job/project.json").exists()


def test_doctor_only_reports_key_presence(tmp_path, monkeypatch):
    module = pipeline()
    config = tmp_path / "private.env"
    config.write_text('AIHUB_API_KEY=not-to-display\nAPP_ID=internal-id\nAPP_KEY=another-secret\n')
    monkeypatch.setenv("RECAP_ENV_FILE", str(config))
    for name in ["AIHUB_API_KEY", "APP_ID", "APP_KEY"]:
        monkeypatch.delenv(name, raising=False)
    result = module.doctor()
    assert all(result["credentials_configured"].values())
    assert "not-to-display" not in json.dumps(result)
    assert "another-secret" not in json.dumps(result)


def test_failed_stage_saves_sanitized_diagnostics_and_keeps_failure_status(tmp_path, capsys):
    module, project = prepared_project(tmp_path)
    command = [sys.executable, "-c", 'import sys; print("server echoed a-private-key", file=sys.stderr); raise SystemExit(1)',
               "--work-dir", str(project.parent / "editorial")]
    with pytest.raises(RuntimeError, match="log"):
        module.execute(command, {"APP_KEY": "a-private-key"})
    logs = list((project.parent / "editorial/logs").glob("*.log"))
    assert len(logs) == 1 and "server echoed" in logs[0].read_text()
    assert "a-private-key" not in logs[0].read_text() + capsys.readouterr().out
    def failure(command, env):
        raise RuntimeError("model unavailable")
    with pytest.raises(RuntimeError):
        module.run_pipeline(project, executor=failure)
    assert module.project_status(project)["status"] == "failed"


def test_resume_keeps_latest_explicit_candidate_after_rejection(tmp_path):
    module, project = prepared_project(tmp_path)
    default = project.parent / "editorial/recap_story_plan.candidate.json"
    revised = project.parent / "revision.json"
    write(default, {"version": "older"})
    write(revised, {"version": "latest"})
    def stage(command, env):
        current = Path(command[command.index("--candidate") + 1])
        assert current == revised, "resume silently selected the old default candidate"
        return {"status": "needs_review", "findings": []}
    module.run_pipeline(project, candidate=revised, executor=stage)
    assert module.run_pipeline(project, executor=stage)["status"] == "needs_review"
    revised.unlink()
    with pytest.raises(ValueError, match="candidate"):
        module.run_pipeline(project, executor=stage)


def test_structured_stage_findings_are_redacted_before_persisting(tmp_path, monkeypatch):
    module, project = prepared_project(tmp_path)
    private = 'synthetic-key-with-"quote'
    monkeypatch.setenv("APP_KEY", private)
    candidate = project.parent / "candidate.json"
    write(candidate, {"bad": True})
    child = "import json; print(json.dumps({'status':'needs_review','findings':[{'message':" + repr(private) + "}]})); raise SystemExit(2)"
    def stage(command, env):
        return module.execute([sys.executable, "-c", child, "--work-dir", str(candidate.parent / "editorial")], env)
    result = module.run_pipeline(project, candidate=candidate, executor=stage)
    assert result["findings"][0]["message"] == "[REDACTED]"
    assert private not in (project.parent / "run_state.json").read_text()
