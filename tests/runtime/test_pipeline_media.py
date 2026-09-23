"""Exercise accepted candidate -> real TTS processing -> picture -> subtitle/mix -> delivery.

Only the remote semantic review and remote speech response are substituted.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import wave

from test_pipeline import pipeline, source, write, ROOT


def test_candidate_to_real_video_through_unified_pipeline(tmp_path, monkeypatch):
    module = pipeline()
    video = source(tmp_path)
    project = module.initialize(video, tmp_path / "render-job", title="合成素材")
    config = json.loads(project.read_text())
    config["delivery"] = {"width": 320, "height": 180}
    write(project, config)
    understanding = project.parent / "understanding"
    source_id = config["source"]["id"]
    write(understanding / "vlm_analysis.json", [{"start": 0, "end": 2, "description": "蓝色画面", "frame_facts": {"0.1": ["蓝色画面"]}}])
    write(understanding / "asr_result.json", [])
    for name in ["vlm_analysis.json", "asr_result.json"]:
        write(understanding / (name + ".meta.json"), {
            "source_video_fingerprint": hashlib.sha256(video.read_bytes()).hexdigest(),
            "artifact_fingerprint": hashlib.sha256((understanding / name).read_bytes()).hexdigest(),
        })
    candidate = project.parent / "editorial/recap_story_plan.candidate.json"
    write(candidate, {
        "schema_version": 1, "project_id": config["id"], "source_id": source_id,
        "style": {"id": "guided-discovery", "version": 2},
        "viewer_promise": "识别画面颜色", "selected_angle": "颜色观察",
        "chapters": [{"id": "c1", "title": "颜色", "promise": "看清颜色"}], "questions": [],
        "paragraphs": [{
            "id": "p1", "chapter_id": "c1", "incoming_result": "画面出现", "audience_knows": ["这是一段合成素材"],
            "audience_question": "是什么颜色", "change": "确认蓝色", "added_value": "用声音说明颜色",
            "claims": [{"id": "cl1", "text": "蓝色", "kind": "observation", "evidence_ids": ["frame:0:0.1"], "certainty": "visible"}],
            "presentation": [{"id": "o1", "type": "play", "source_id": source_id, "source_start": 0, "source_end": 2, "audio": "mute", "purpose": "展示颜色"}],
            "narration": {"id": "n1", "text": "蓝色。", "at_operation": "o1", "offset": .2, "allowed_operations": ["o1"]},
            "protected_audio": [], "handoff": {"from_result": "颜色已出现", "through_original": "画面停留", "to_next": "颜色示例结束"},
            "questions_opened": [], "questions_answered": [],
        }],
    })
    sys.path.insert(0, str(ROOT / "skills/video-script/scripts"))
    from editorial_runner import run_project

    def synthesize(text, path, rate):
        with wave.open(str(path), "wb") as handle:
            handle.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            handle.writeframes(b"\x00\x08\x00\xf8" * 6000)

    def execute(command, env):
        if "editorial.py" in command:
            return run_project(project, candidate.parent, candidate_path=candidate,
                               call_model=lambda payload: {"choices": [{"message": {"content": '{"verdict":"pass","findings":[]}'}}]},
                               model_identity="offline-review-fixture")
        spec = importlib.util.spec_from_file_location("pipeline_renderer", ROOT / "scripts/editorial_render.py")
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        prepare = renderer.voiceover.prepare_audio
        monkeypatch.setattr(renderer.voiceover, "prepare_audio", lambda project, plan, work: prepare(project, plan, work, synthesizer=synthesize))
        monkeypatch.setattr(sys, "argv", command[1:])
        for key in ("PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE"):
            monkeypatch.setenv(key, env[key])
        renderer.main()
        return {}

    result = module.run_pipeline(project, executor=execute)
    assert result["status"] == "complete"
    output = Path(result["output"])
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(output)], capture_output=True, text=True, check=True)
    media = json.loads(probe.stdout)
    assert {s["codec_type"] for s in media["streams"]} == {"video", "audio"}
    assert 1.9 < float(media["format"]["duration"]) < 2.2
    assert (candidate.parent / "subtitles.ass").is_file()
    assert read_qc(candidate.parent)["passed"] is True


def read_qc(work):
    return json.loads((work / "editorial_delivery_qc.json").read_text())
