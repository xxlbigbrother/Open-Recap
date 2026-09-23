"""Renderer integration, original-audio guards, and delivery contracts."""
import base64
import hashlib
import importlib.util
import json
import math
import os
import struct
import subprocess
from pathlib import Path
import sys
from types import SimpleNamespace
import wave

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def restore_runtime_environment(monkeypatch):
    monkeypatch.setenv('PATH', os.environ['PATH'])
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    monkeypatch.delenv('SUBTITLE_FONT_NAME', raising=False)


def module():
    path = ROOT / 'scripts/editorial_render.py'
    spec = importlib.util.spec_from_file_location('editorial_render', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_actual_duck_restore_cannot_enter_protected_original_audio():
    m=module();compiled={'narrations':[{'id':'n','start':1,'end':3}],
        'protected_audio':[{'output_start':4,'output_end':6,'purpose':'关键台词'}]}
    assembly={'audio_segments':[{'actual_place_start':1,'actual_place_end':3,'source_restore_at':5,'segment_tempo_factor':1}]}
    with pytest.raises(ValueError,match='protected'):m.verify_assembly_timing(compiled,assembly)
    assembly['audio_segments'][0]['source_restore_at']=3.5
    assert m.verify_assembly_timing(compiled,assembly)['passed']


def test_actual_placement_shift_cannot_invalidate_planned_evidence():
    m=module();compiled={'narrations':[{'id':'n','start':1,'end':3}],'protected_audio':[]}
    assembly={'audio_segments':[{'actual_place_start':.5,'actual_place_end':2.5,'segment_tempo_factor':1}]}
    with pytest.raises(ValueError,match='placement'):m.verify_assembly_timing(compiled,assembly)


def test_delivery_defaults_preserve_source_aspect_and_do_not_guess_subtitle_mask():
    settings=module().delivery_settings({}, {'width':1920,'height':1080})
    assert settings['width']==1280 and settings['height']==720
    assert settings['subtitle_band'] is None


def test_project_subtitle_band_is_validated_inside_its_canvas():
    m=module();project={'delivery':{'width':1280,'height':536,'subtitle_band':[430,526]}}
    settings=m.delivery_settings(project,{'width':1280,'height':536})
    assert settings['subtitle_band']==[430,526]
    project['delivery']['subtitle_band']=[430,800]
    with pytest.raises(ValueError,match='subtitle'):m.delivery_settings(project,{'width':1280,'height':536})


def render_fixture(tmp_path, monkeypatch):
    import aihub_adapter

    source = tmp_path / 'source.wav'
    with wave.open(str(source), 'wb') as handle:
        handle.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        handle.writeframes(b''.join(
            struct.pack('<h', int(2000 * math.sin(i * 2 * math.pi * 440 / 24000)))
            for i in range(24000)
        ))
    env_file = tmp_path / 'test.env'
    env_file.write_text('APP_ID=fixture-app\nAPP_KEY=fixture-key\n')
    monkeypatch.setenv('RECAP_ENV_FILE', str(env_file))
    monkeypatch.delenv('APP_ID', raising=False)
    monkeypatch.delenv('APP_KEY', raising=False)
    understanding = tmp_path / 'understanding'
    understanding.mkdir()
    (understanding / 'asr_result.json').write_text('[]')
    project = tmp_path / 'project.json'
    project.write_text(json.dumps({
        'schema_version': 1, 'id': 'demo',
        'source': {'id': 'movie', 'path': 'source.wav', 'media_duration': 1, 'range': [0, 1]},
        'understanding_dir': 'understanding', 'voice': {'provider': 'aihub-mimo'},
    }))
    work = tmp_path / 'work'
    work.mkdir()
    story = {'paragraphs': []}
    (work / 'recap_story_plan.json').write_text(json.dumps(story))
    plan = {'parent_story_sha256': hashlib.sha256(b'{"paragraphs":[]}').hexdigest(),
            'narrations': [{'id': 'n', 'text': '你好'}]}
    (work / 'editorial_plan.json').write_text(json.dumps(plan))
    calls = []

    def transport(url, body, headers, **kwargs):
        calls.append(body)
        return None, {'code': 0, 'answer': [
            {'type': 'audio_base64', 'value': base64.b64encode(source.read_bytes()).decode()},
        ]}

    monkeypatch.setattr(aihub_adapter, 'post', transport)
    return project, work, calls


def test_renderer_loads_explicit_voice_stage_even_with_shadow_module(monkeypatch):
    monkeypatch.setitem(sys.modules, 'voiceover', SimpleNamespace())
    renderer = module()
    assert Path(renderer.voiceover.__file__).resolve() == ROOT / 'skills/video-voiceover/scripts/voiceover.py'


def test_renderer_import_needs_only_voice_stage_until_main(tmp_path):
    package = tmp_path / 'package'
    for relative in ['scripts/editorial_render.py', 'skills/video-voiceover/scripts/voiceover.py']:
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    (package / 'scripts/lib.py').write_text('raise AssertionError("unsafe lib import")\n')
    result = subprocess.run([
        sys.executable, '-c',
        'import runpy,sys; bridge=runpy.run_path(sys.argv[1]); '
        'assert bridge["delivery_settings"]({}, {"width":1920,"height":1080})["height"] == 720',
        str(package / 'scripts/editorial_render.py'),
    ], cwd=tmp_path, env={**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('audio_only', [True, False])
def test_render_cli_reuses_voice_and_preserves_pipeline_arguments(tmp_path, monkeypatch, audio_only):
    renderer = module()
    project, work, calls = render_fixture(tmp_path, monkeypatch)
    real_run = renderer.subprocess.run
    stages = []
    monkeypatch.setenv('PATH', os.pathsep.join(
        p for p in os.environ['PATH'].split(os.pathsep) if p != str(ROOT / 'tools')
    ))

    def execute(command, **kwargs):
        assert os.environ['PATH'].split(os.pathsep)[0] == str(ROOT / 'tools')
        if command[0] == 'ffmpeg':
            return real_run(command, **kwargs)
        assert not audio_only, 'audio-only must stop before presentation or assembly'
        if command[0] == 'ffprobe':
            return SimpleNamespace(returncode=0, stdout='{"streams":[{"width":1920,"height":1080}]}')
        entry = Path(command[1]).name
        stages.append(entry)
        assert kwargs['env']['PYTHONPATH'] == str(ROOT / 'scripts')
        assert kwargs['env']['PATH'].split(os.pathsep)[0] == str(ROOT / 'tools')
        if entry == 'presentation.py':
            assert command[command.index('--audio-durations') + 1] == str(work / 'audio_durations.json')
            assert command[command.index('--width') + 1] == '1280'
            assert command[command.index('--height') + 1] == '720'
            (work / 'presentation_compiled.json').write_text(json.dumps({
                'narrations': [], 'operations': [], 'protected_audio': [],
            }))
        else:
            assert entry == 'assemble.py'
            assert command[2] == str(work / 'presentation_source.mp4')
            assert command[command.index('--output-dir') + 1] == str(tmp_path / 'delivery')
            assert kwargs['env']['NARRATION_SPEED'] == '1'
            assert kwargs['env']['TTS_SEGMENT_TEMPO_MAX'] == '1'
            assert kwargs['env']['SOURCE_SUBTITLE_MASK_POLICY'] == 'off'
            (work / 'assembly_manifest.json').write_text('{"audio_segments":[]}')
        return SimpleNamespace(returncode=0, stdout='done', stderr='')

    monkeypatch.setattr(renderer.subprocess, 'run', execute)
    argv = ['editorial_render.py', '--project', str(project), '--work-dir', str(work),
            '--output-dir', str(tmp_path / 'delivery')]
    if audio_only:
        argv.append('--audio-only')
    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.chdir(tmp_path)
    renderer.main()
    assert len(calls) == 1
    assert os.environ['APP_KEY'] == 'fixture-key'
    catalog = json.loads((work / 'audio_catalog.json').read_text())
    assert catalog['n']['provider'] == 'aihub-mimo'
    assert catalog['n']['duration'] == pytest.approx(1, abs=.01)
    assert stages == ([] if audio_only else ['presentation.py', 'assemble.py'])
    if not audio_only:
        assert json.loads((work / 'editorial_delivery_qc.json').read_text())['passed']


def test_stale_plan_is_rejected_before_voice_request(tmp_path, monkeypatch):
    renderer = module()
    project, work, calls = render_fixture(tmp_path, monkeypatch)
    (work / 'recap_story_plan.json').write_text('{"paragraphs":[],"changed":true}')
    monkeypatch.setattr(sys, 'argv', ['editorial_render.py', '--project', str(project),
                                    '--work-dir', str(work), '--audio-only'])
    with pytest.raises(ValueError, match='stale editorial plan parent'):
        renderer.main()
    assert calls == []
    assert not (work / 'audio_catalog.json').exists()


@pytest.mark.parametrize('platform,explicit,expected', [
    ('darwin', None, 'PingFang SC'),
    ('linux', None, 'Noto Sans CJK SC'),
    ('win32', None, 'Noto Sans CJK SC'),
    ('darwin', 'Custom CJK', 'Custom CJK'),
    ('linux', 'Custom CJK', 'Custom CJK'),
])
def test_render_cli_chooses_platform_font_without_overriding_configuration(
    tmp_path, monkeypatch, platform, explicit, expected,
):
    renderer = module()
    project, work, calls = render_fixture(tmp_path, monkeypatch)
    if explicit is None:
        monkeypatch.delenv('SUBTITLE_FONT_NAME', raising=False)
    else:
        # Check that the existing env-file route also reaches assembly unchanged.
        env_file = Path(os.environ['RECAP_ENV_FILE'])
        env_file.write_text(env_file.read_text() + f'SUBTITLE_FONT_NAME="{explicit}"\n')
        monkeypatch.delenv('SUBTITLE_FONT_NAME', raising=False)
    monkeypatch.setattr(sys, 'platform', platform)
    stages = []
    real_run = renderer.subprocess.run

    import aihub_adapter
    transport = aihub_adapter.post

    def configured_transport(*args, **kwargs):
        assert os.environ['SUBTITLE_FONT_NAME'] == expected
        return transport(*args, **kwargs)

    monkeypatch.setattr(aihub_adapter, 'post', configured_transport)

    def execute(command, **kwargs):
        if command[0] == 'ffmpeg':
            return real_run(command, **kwargs)
        if command[0] == 'ffprobe':
            return SimpleNamespace(returncode=0, stdout='{"streams":[{"width":1280,"height":720}]}')
        entry = Path(command[1]).name
        stages.append(entry)
        if entry == 'presentation.py':
            (work / 'presentation_compiled.json').write_text(
                '{"narrations":[],"operations":[],"protected_audio":[]}'
            )
        else:
            assert entry == 'assemble.py'
            assert kwargs['env']['SUBTITLE_FONT_NAME'] == expected
            (work / 'assembly_manifest.json').write_text('{"audio_segments":[]}')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(renderer.subprocess, 'run', execute)
    monkeypatch.setattr(sys, 'argv', ['editorial_render.py', '--project', str(project),
                                    '--work-dir', str(work)])
    renderer.main()
    assert len(calls) == 1
    assert stages == ['presentation.py', 'assemble.py']
