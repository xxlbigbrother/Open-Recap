"""Voice stage contracts exercised with real WAVs and fake external transport."""
import importlib.util
import base64
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import wave
import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_credentials_and_transport(tmp_path, monkeypatch):
    import aihub_adapter

    env_file = tmp_path / 'test.env'
    env_file.write_text('APP_ID=fixture-app\nAPP_KEY=fixture-key\n')
    monkeypatch.setenv('RECAP_ENV_FILE', str(env_file))
    monkeypatch.setenv('PATH', os.environ['PATH'])
    for key in ('APP_ID', 'APP_KEY', 'DOUBAO_TTS_VOICE', 'SUBTITLE_FONT_NAME'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(aihub_adapter, 'ROOT', tmp_path / 'adapter')

    def forbidden(*args, **kwargs):
        raise AssertionError('test attempted external transport')

    monkeypatch.setattr(aihub_adapter, 'post', forbidden)


def module():
    p=ROOT/'skills/video-voiceover/scripts/voiceover.py';assert p.exists(),'standalone voice stage missing'
    spec=importlib.util.spec_from_file_location('voiceover_test',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def synth(text,path,rate='+0%'):
    with wave.open(str(path),'wb') as f:
        f.setparams((1,2,24000,0,'NONE','not compressed'))
        f.writeframes(b''.join(struct.pack('<h',int(2000*math.sin(i*2*math.pi*440/24000))) for i in range(24000)))


def test_audio_cache_bound_to_text_voice_and_rate(tmp_path):
    m=module();project={'voice':{'speaker':'voice-a','tempo':1.1}};plan={'narrations':[{'id':'n1','text':'一句话'}]}
    cat=m.prepare_audio(project,plan,tmp_path,synthesizer=synth)
    assert Path(cat['n1']['path']).name == '3dd5e194771f63355164-ready.wav'
    assert .85<cat['n1']['duration']<.95
    assert cat['n1']['post_tempo']==1.1
    def forbidden(*args,**kwargs):raise AssertionError('valid cache should skip remote request')
    reused=m.prepare_audio(project,plan,tmp_path,synthesizer=forbidden)
    assert reused['n1']['path']==cat['n1']['path']
    plan['narrations'][0]['text']='另一句话';changed=m.prepare_audio(project,plan,tmp_path,synthesizer=synth)
    assert changed['n1']['path']!=cat['n1']['path']
    project['voice']['speaker']='voice-b';voice=m.prepare_audio(project,plan,tmp_path,synthesizer=synth)
    assert voice['n1']['path']!=changed['n1']['path']
    project['voice']['tempo']=1
    speed=m.prepare_audio(project,plan,tmp_path,synthesizer=synth)
    assert speed['n1']['path']!=voice['n1']['path']
    assert .99<speed['n1']['duration']<1.01


def test_tampered_wave_cache_regenerated(tmp_path):
    m=module();project={'voice':{'speaker':'voice-a','tempo':1.1}};plan={'narrations':[{'id':'n1','text':'一句话'}]}
    first=m.prepare_audio(project,plan,tmp_path,synthesizer=synth);Path(first['n1']['path']).write_bytes(b'broken')
    second=m.prepare_audio(project,plan,tmp_path,synthesizer=synth)
    with wave.open(second['n1']['path']) as f:assert f.getnframes()>0


def test_mimo_provider_has_separate_cache_and_truthful_catalog(tmp_path):
    m=module();plan={'narrations':[{'id':'n1','text':'同一句话'}]}
    doubao=m.prepare_audio({'voice':{'speaker':'茉莉','tempo':1}},plan,tmp_path,synthesizer=synth)
    mimo=m.prepare_audio({'voice':{'provider':'aihub-mimo','speaker':'茉莉','tempo':1}},plan,tmp_path,synthesizer=synth)
    assert mimo['n1']['path']!=doubao['n1']['path']
    assert mimo['n1']['provider']=='aihub-mimo'
    assert mimo['n1']['model']=='api_xiaomi_mimo-v2.5-tts'
    assert mimo['n1']['post_tempo']==1
    def forbidden(*args,**kwargs):raise AssertionError('MiMo cache should avoid another synthesis')
    assert m.prepare_audio({'voice':{'provider':'aihub-mimo','speaker':'茉莉','tempo':1}},plan,tmp_path,synthesizer=forbidden)['n1']['path']==mimo['n1']['path']


def test_unknown_provider_is_not_silently_sent_to_doubao(tmp_path):
    with pytest.raises(ValueError,match='provider'):
        module().prepare_audio({'voice':{'provider':'typo'}},{'narrations':[{'id':'n','text':'你好'}]},tmp_path,synthesizer=synth)


def test_voice_uses_external_env_without_copying_keys_into_package(tmp_path,monkeypatch):
    m=module();package=tmp_path/'package';package.mkdir();monkeypatch.setattr(m,'ROOT',package)
    private=tmp_path/'private.env';private.write_text('# outside package\nAPP_ID="file-app"\nAPP_KEY=file-key\nSUBTITLE_FONT_NAME="Noto Sans CJK SC"\n')
    monkeypatch.setenv('RECAP_ENV_FILE',str(private));monkeypatch.setenv('APP_ID','environment-app')
    monkeypatch.delenv('APP_KEY',raising=False);monkeypatch.delenv('SUBTITLE_FONT_NAME',raising=False)
    import aihub_adapter
    original=tmp_path/'fixture.wav';synth('x',original)
    response={'code':0,'answer':[{'type':'audio_base64','value':base64.b64encode(original.read_bytes()).decode()}]}
    monkeypatch.setattr(aihub_adapter,'post',lambda *args,**kwargs:(None,response))
    cat=m.prepare_audio({'voice':{'provider':'aihub-mimo','speaker':'茉莉','tempo':1}},
        {'narrations':[{'id':'n','text':'你好'}]},package/'work')
    import os
    assert os.environ['APP_ID']=='environment-app'
    assert os.environ['APP_KEY']=='file-key'
    assert os.environ['SUBTITLE_FONT_NAME']=='Noto Sans CJK SC'
    assert cat['n']['provider']=='aihub-mimo'
    assert not (package/'.env').exists()


def test_mimo_request_keeps_only_spoken_text_and_records_actual_voice(tmp_path,monkeypatch):
    import aihub_adapter
    monkeypatch.setenv('APP_ID','test-app');monkeypatch.setenv('APP_KEY','test-key')
    original=tmp_path/'fixture.wav';synth('x',original);raw=original.read_bytes();captured={}
    def transport(url,body,headers,**kwargs):
        captured.update(body)
        return None,{'code':0,'answer':[{'type':'audio_base64','value':base64.b64encode(raw).decode()}]}
    monkeypatch.setattr(aihub_adapter,'post',transport)
    destination=tmp_path/'generated.wav'
    module().synthesize_mimo_preset('你好',destination,speaker='茉莉')
    assert destination.read_bytes()==raw
    assert captured['model_marker']=='api_xiaomi_mimo-v2.5-tts'
    assert captured['params']['audio']=={'format':'wav','voice':'茉莉'}
    assert captured['messages']==[{'content':[{'type':'text','role':'assistant','value':'你好'}]}]
    record=json.loads(destination.with_suffix('.provider.json').read_text())
    assert record['provider']=='aihub-mimo' and record['voice']=='茉莉'
    assert 'test-key' not in json.dumps(record)


def test_negative_or_nonfinite_tempo_rejected(tmp_path):
    m=module()
    for speed in [0,-1,float('nan')]:
        with pytest.raises(ValueError,match='tempo'):m.prepare_audio({'voice':{'speaker':'a','tempo':speed}},{'narrations':[]},tmp_path,synthesizer=synth)


@pytest.mark.parametrize('provider,speaker,tempo,model', [
    ('aihub-mimo', '茉莉', 1, 'api_xiaomi_mimo-v2.5-tts'),
    ('aihub-doubao', 'zh_male_cixingjieshuonan_uranus_bigtts', 1.1, 'api_doubao_doubao-tts-2.0'),
])
def test_provider_defaults_use_real_transport_audio_and_measured_catalog(
    tmp_path, monkeypatch, provider, speaker, tempo, model,
):
    import aihub_adapter

    source = tmp_path / 'source.wav'
    synth('fixture', source)
    captured = []

    def transport(url, body, headers, **kwargs):
        captured.append(body)
        return None, {'code': 0, 'answer': [
            {'type': 'audio_base64', 'value': base64.b64encode(source.read_bytes()).decode()},
        ]}

    monkeypatch.setattr(aihub_adapter, 'post', transport)
    work = tmp_path / 'work'
    project = {'voice': {'provider': provider}} if provider == 'aihub-mimo' else {}
    stage = module()
    catalog = stage.prepare_audio(project, {'narrations': [{'id': 'n1', 'text': '真实配音'}]}, work)
    assert catalog['n1']['provider'] == provider
    assert catalog['n1']['speaker'] == speaker
    assert catalog['n1']['post_tempo'] == tempo
    assert catalog['n1']['model'] == captured[0]['model_marker'] == model
    assert len(captured) == 1
    if provider == 'aihub-doubao':
        assert captured[0]['params'] == {
            'speaker': speaker, 'resource_id': 'seed-tts-2.0', 'format': 'mp3',
            'sample_rate': 24000, 'bit_rate': 128000, 'speech_rate': 0, 'loudness_rate': 0,
        }
        assert captured[0]['messages'] == [{'content': [{'type': 'text', 'value': '真实配音'}]}]
    ready = Path(catalog['n1']['path'])
    with wave.open(str(ready)) as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (1, 2, 44100)
        assert catalog['n1']['duration'] == handle.getnframes() / 44100
    assert catalog['n1']['duration'] == pytest.approx(1 / tempo, abs=.02)
    reference_input = source
    if provider == 'aihub-doubao':
        # The verified adapter resamples first; combining both filters changes PCM.
        reference_input = tmp_path / 'reference-raw.wav'
        subprocess.run([
            'ffmpeg', '-v', 'error', '-y', '-i', str(source), '-ar', '44100',
            '-ac', '1', '-c:a', 'pcm_s16le', str(reference_input),
        ], check=True)
    expected = tmp_path / 'expected.wav'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-i', str(reference_input), '-af',
        f'atempo={tempo},loudnorm=I=-20:TP=-2:LRA=11', '-ar', '44100',
        '-ac', '1', '-c:a', 'pcm_s16le', str(expected),
    ], check=True)
    assert ready.read_bytes() == expected.read_bytes()
    assert json.loads((work / 'audio_catalog.json').read_text()) == catalog
    assert json.loads((work / 'audio_durations.json').read_text()) == {'n1': catalog['n1']['duration']}


def test_project_paths_resolve_from_json_without_needing_movie_or_understanding(tmp_path, monkeypatch):
    directory = tmp_path / 'project'
    directory.mkdir()
    path = directory / 'project.json'
    path.write_text(json.dumps({
        'schema_version': 1, 'id': 'demo', 'source': {'path': 'media/movie.mp4'},
        'understanding_dir': 'work/understanding', 'style_path': 'style.json',
        'verified_notes': 'notes.json', 'research_path': 'research.json',
        'authoring_draft_path': 'draft.json', 'development_path': 'development.json',
        'review_decision_path': 'review.json',
        'source_binding': {'proxy_meta': 'proxy.json', 'render_manifest': 'render.json',
                           'analysis_source_map': 'map.json'},
        'voice': {'provider': 'aihub-mimo'},
    }))
    monkeypatch.chdir(tmp_path)
    project = module().load_project(path)
    assert project['source']['path'] == str(directory / 'media/movie.mp4')
    assert project['understanding_dir'] == str(directory / 'work/understanding')
    for key, name in [('style_path', 'style'), ('verified_notes', 'notes'),
                      ('research_path', 'research'), ('authoring_draft_path', 'draft'),
                      ('development_path', 'development'), ('review_decision_path', 'review')]:
        assert project[key] == str(directory / (name + '.json'))
    assert project['source_binding'] == {
        'proxy_meta': str(directory / 'proxy.json'),
        'render_manifest': str(directory / 'render.json'),
        'analysis_source_map': str(directory / 'map.json'),
    }
    assert not (directory / 'media/movie.mp4').exists()


def test_cli_uses_explicit_plan_and_writes_real_catalog(tmp_path, monkeypatch, capsys):
    import aihub_adapter

    project_dir = tmp_path / 'project'
    project_dir.mkdir()
    project_path = project_dir / 'project.json'
    project_path.write_text(json.dumps({'voice': {'provider': 'aihub-mimo'}}))
    plan_path = project_dir / 'approved.json'
    plan_path.write_text(json.dumps({'narrations': [{'id': 'n', 'text': '你好'}]}))
    raw = tmp_path / 'fixture.wav'
    synth('fixture', raw)

    def transport(url, body, headers, **kwargs):
        assert os.environ['APP_ID'] == 'fixture-app'
        assert os.environ['APP_KEY'] == 'fixture-key'
        assert os.environ['PATH'].split(os.pathsep)[0] == str(ROOT / 'tools')
        return None, {'code': 0, 'answer': [
            {'type': 'audio_base64', 'value': base64.b64encode(raw.read_bytes()).decode()},
        ]}

    monkeypatch.setattr(aihub_adapter, 'post', transport)
    monkeypatch.chdir(tmp_path)
    module().main(['--project', 'project/project.json', '--plan', 'project/approved.json',
                   '--work-dir', 'output'])
    work = tmp_path / 'output'
    catalog = json.loads((work / 'audio_catalog.json').read_text())
    assert catalog['n']['text'] == '你好'
    assert catalog['n']['duration'] == pytest.approx(1, abs=.01)
    assert Path(catalog['n']['path']).is_file()
    output = capsys.readouterr()
    assert 'fixture-key' not in output.out + output.err
    assert 'fixture-key' not in ''.join(p.read_text() for p in work.rglob('*.json'))


def test_package_env_is_fallback_and_process_environment_wins(tmp_path, monkeypatch):
    stage = module()
    package = tmp_path / 'package'
    package.mkdir()
    (package / '.env').write_text('APP_ID=package-app\nAPP_KEY="package-key"\n')
    monkeypatch.setattr(stage, 'ROOT', package)
    monkeypatch.delenv('RECAP_ENV_FILE')
    monkeypatch.setenv('APP_ID', 'process-app')
    stage.prepare_audio({'voice': {'provider': 'aihub-mimo'}}, {'narrations': []}, tmp_path / 'work')
    assert os.environ['APP_ID'] == 'process-app'
    assert os.environ['APP_KEY'] == 'package-key'


def test_missing_explicit_env_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv('RECAP_ENV_FILE', str(tmp_path / 'missing.env'))
    with pytest.raises(FileNotFoundError, match='RECAP_ENV_FILE'):
        module().prepare_audio({}, {'narrations': []}, tmp_path / 'work')


def test_stage_import_and_cli_work_without_sibling_modules(tmp_path):
    stage = module()
    isolated = tmp_path / 'package/skills/video-voiceover/scripts/voiceover.py'
    isolated.parent.mkdir(parents=True)
    isolated.write_bytes(Path(stage.__file__).read_bytes())
    # Any eager adapter or generic lib import makes the standalone CLI fail here.
    (isolated.parent / 'lib.py').write_text('raise AssertionError("unsafe shared lib")\n')
    (isolated.parent / 'aihub_adapter.py').write_text('raise AssertionError("eager adapter import")\n')
    project = tmp_path / 'project.json'
    project.write_text('{"voice":{"provider":"aihub-mimo"}}')
    plan = tmp_path / 'plan.json'
    plan.write_text('{"narrations":[{"id":"n","text":"一句话"}]}')
    work = tmp_path / 'work'
    cached = stage.prepare_audio(json.loads(project.read_text()), json.loads(plan.read_text()),
                                 work, synthesizer=synth)
    assert Path(cached['n']['path']).name == 'f71c412d0cb9b4019a4c-ready.wav'
    result = subprocess.run([
        sys.executable, str(isolated), '--project', str(project), '--plan', str(plan),
        '--work-dir', str(work),
    ], cwd=tmp_path, env={**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((work / 'audio_catalog.json').read_text()) == cached
    assert json.loads((work / 'audio_durations.json').read_text()) == {'n': cached['n']['duration']}
