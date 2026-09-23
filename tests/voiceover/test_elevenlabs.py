"""Offline ElevenLabs contracts: fake HTTP, real generated MP3 and PCM audio."""
import base64
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
import wave

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
MODELS = ('eleven_v3', 'eleven_multilingual_v2', 'eleven_flash_v2_5')
DEFAULTS = {'stability': .5, 'similarity_boost': .75, 'style': 0.0,
            'speed': 1.0, 'use_speaker_boost': True}
V3_DEFAULTS = {key: value for key, value in DEFAULTS.items() if key != 'use_speaker_boost'}


def adapter():
    assert (ROOT / 'scripts/elevenlabs_adapter.py').exists(), 'ElevenLabs adapter missing'
    return importlib.import_module('elevenlabs_adapter')


def stage():
    spec = importlib.util.spec_from_file_location(
        'elevenlabs_voice_stage', ROOT / 'skills/video-voiceover/scripts/voiceover.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    import aihub_adapter

    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected network or credential access')

    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(aihub_adapter, 'credentials', lambda: ('fixture-app', 'fixture-key'))
    monkeypatch.setattr(aihub_adapter, 'post', forbidden)
    monkeypatch.setattr(aihub_adapter, 'BASE', 'http://fixture-gateway.invalid:8080')
    monkeypatch.setenv('APP_ID', 'fixture-app')
    monkeypatch.setenv('APP_KEY', 'fixture-key')
    config = tmp_path / 'fixture.env'
    config.write_text('# isolated test configuration\n')
    monkeypatch.setenv('RECAP_ENV_FILE', str(config))


@pytest.fixture(scope='module')
def mp3(tmp_path_factory):
    path = tmp_path_factory.mktemp('generated-audio') / 'tone.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=44100:duration=1', '-ac', '2',
                    '-c:a', 'libmp3lame', '-b:a', '128k', str(path)],
                   check=True, capture_output=True)
    return path.read_bytes()


def alignment():
    return {'characters': ['你', '好'], 'character_start_times_seconds': [0.0, .4],
            'character_end_times_seconds': [.4, .9]}


def payload(mp3, **updates):
    return {'audio_base64': base64.b64encode(mp3).decode(), 'alignment': alignment(),
            'normalized_alignment': alignment(), 'usage': {'characters': 2},
            'requestid': 'request-123', **updates}


class Response:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status_code = data, status
        self.headers = headers or {}

    @property
    def text(self):
        raise AssertionError('response body must not be echoed')

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def transport(monkeypatch, data, status=200, headers=None):
    calls = []

    def post(url, **kwargs):
        calls.append({'url': url, **kwargs})
        if isinstance(data, requests.RequestException):
            raise data
        return Response(data, status, headers)

    monkeypatch.setattr(requests, 'post', post)
    return calls


@pytest.mark.parametrize('model', MODELS)
def test_native_model_routes_in_header_and_body_and_decodes_real_mp3(tmp_path, monkeypatch, mp3, model):
    api = adapter()
    calls = transport(monkeypatch, payload(mp3))
    wav = tmp_path / 'speech.wav'
    sidecar = api.synthesize('你好', wav, speaker='voice123', model=model)
    assert len(calls) == 1
    request = calls[0]
    assert request['url'] == 'http://fixture-gateway.invalid:8080/v1/text-to-speech/voice123/with-timestamps'
    assert request['params'] == {'output_format': 'mp3_44100_128'}
    expected_settings = V3_DEFAULTS if model == 'eleven_v3' else DEFAULTS
    assert request['json'] == {'text': '你好', 'model_id': model, 'voice_settings': expected_settings}
    assert request['headers'].keys() == {'Authorization'}
    assert re.fullmatch(r'Bearer fixture-app:fixture-key\?provider=elevenlabs&model=' + model
                        + r'&cache_task_id=[0-9a-f]{32}&timeout=120&usage=1', request['headers']['Authorization'])
    assert request['timeout'] == (10, 120)
    assert request['allow_redirects'] is False
    with wave.open(str(wav)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) == (1, 2, 44100, 'NONE')
        duration = audio.getnframes() / audio.getframerate()
        assert duration == pytest.approx(1, abs=.01)
    assert sidecar == wav.with_suffix('.provider.json')
    record = json.loads(sidecar.read_text())
    assert record['provider'] == 'aihub-elevenlabs'
    assert record['model'] == model
    assert record['voice'] == 'voice123'
    assert record['voice_settings'] == expected_settings
    assert record['source_text'] == '你好'
    assert record['alignment'] == record['normalized_alignment'] == alignment()
    assert record['clock'] == 'raw_audio'
    assert record['duration'] == duration
    assert record['usage'] == {'characters': 2}
    assert record['requestid'] == 'request-123'
    assert not list(tmp_path.rglob('*.mp3'))
    assert set(tmp_path.iterdir()) == {wav, sidecar, tmp_path / 'fixture.env'}


@pytest.mark.parametrize('voice_settings', [
    {'speed': .69}, {'speed': 1.21}, {'speed': True}, {'speed': '1'}, {'speed': float('nan')},
    {'stability': -.01}, {'stability': 1.01}, {'stability': .4},
    {'similarity_boost': -1}, {'similarity_boost': float('inf')}, {'similarity_boost': 1.1},
    {'style': -.1}, {'style': 1.1}, {'style': False}, {'use_speaker_boost': 1},
    {'speed': 10 ** 1000},
    {'unknown': 0}, [], 'settings',
])
def test_invalid_native_settings_fail_before_transport(voice_settings, tmp_path):
    with pytest.raises(ValueError, match='voice_settings|speed|stability|similarity_boost|style|use_speaker_boost'):
        adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123', voice_settings=voice_settings)
    assert not (tmp_path / 'speech.wav').exists()


@pytest.mark.parametrize('model', MODELS)
@pytest.mark.parametrize('stability', [0, .5, 1])
@pytest.mark.parametrize('speed', [.7, 1.2])
def test_native_setting_bounds_are_accepted_without_mutating_input(model, stability, speed):
    original = {'stability': stability, 'speed': speed, 'use_speaker_boost': False}
    effective = adapter().tts_settings(speaker='voice123', model=model, voice_settings=original)
    assert effective['voice_settings'] == {**DEFAULTS, **original}
    assert original == {'stability': stability, 'speed': speed, 'use_speaker_boost': False}
    assert effective['model'] == model


@pytest.mark.parametrize('model', MODELS[1:])
def test_non_v3_stability_is_continuous(model):
    assert adapter().tts_settings(speaker='voice123', model=model, voice_settings={'stability': .4})['voice_settings']['stability'] == .4


@pytest.mark.parametrize('kwargs', [
    {'speaker': None}, {'speaker': ''}, {'speaker': '  '}, {'speaker': 'voice/../../x'},
    {'speaker': 'voice?api=wrong'}, {'speaker': 'voice123', 'model': 'api_elevenlabs_eleven_v3'},
    {'speaker': 'voice123', 'model': 'eleven_v3&provider=wrong'},
])
def test_invalid_voice_and_model_rejected_before_transport(kwargs, tmp_path):
    with pytest.raises(ValueError, match='speaker|model'):
        adapter().synthesize('你好', tmp_path / 'speech.wav', **kwargs)


@pytest.mark.parametrize('bad', [None, '', '%%%notbase64%%%', 'a', 12, '你好', base64.b64encode(b'not MP3').decode()])
def test_bad_audio_never_publishes_output(tmp_path, monkeypatch, bad):
    calls = transport(monkeypatch, {'audio_base64': bad})
    with pytest.raises(RuntimeError, match='audio|MP3'):
        adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123')
    assert len(calls) == 1
    assert not list(tmp_path.glob('speech*'))
    assert not list(tmp_path.rglob('*.mp3'))


@pytest.mark.parametrize('response,status', [
    ({'detail': 'Bearer fixture-app:fixture-key'}, 400),
    ({'detail': 'Bearer fixture-app:fixture-key'}, 503),
    ({'detail': 'Bearer fixture-app:fixture-key'}, 302),
    (ValueError('fixture-app:fixture-key'), 200),
    (['fixture-app:fixture-key'], 200),
    (requests.Timeout('fixture-app:fixture-key'), 200),
])
def test_request_errors_are_redacted_and_never_retried(tmp_path, monkeypatch, capsys, response, status):
    calls = transport(monkeypatch, response, status)
    with pytest.raises(RuntimeError) as caught:
        adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123')
    assert len(calls) == 1
    output = ''.join(traceback.format_exception(caught.type, caught.value, caught.tb))
    captured = capsys.readouterr()
    assert 'fixture-key' not in output + captured.out + captured.err
    assert 'fixture-app' not in output + captured.out + captured.err
    assert not list(tmp_path.glob('speech*'))


@pytest.mark.parametrize('bad_alignment', [
    {}, [], {'characters': ['你']},
    {**alignment(), 'characters': ['你']},
    {**alignment(), 'characters': [1, '好']},
    {**alignment(), 'character_start_times_seconds': [0, float('nan')]},
    {**alignment(), 'character_start_times_seconds': [0, True]},
    {**alignment(), 'character_start_times_seconds': [.5, .4]},
    {**alignment(), 'character_start_times_seconds': [-.1, .4]},
    {**alignment(), 'character_end_times_seconds': [.6, .5]},
    {**alignment(), 'character_end_times_seconds': [0, .3]},
    {**alignment(), 'character_end_times_seconds': [.4, 10]},
])
@pytest.mark.parametrize('field', ['alignment', 'normalized_alignment'])
def test_malformed_alignment_is_rejected_before_publishing_wav(tmp_path, monkeypatch, mp3, field, bad_alignment):
    transport(monkeypatch, payload(mp3, **{field: bad_alignment}))
    with pytest.raises(RuntimeError, match='alignment'):
        adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123')
    assert not list(tmp_path.glob('speech*'))


@pytest.mark.parametrize('present', [False, True])
def test_alignment_is_optional_and_can_be_null(tmp_path, monkeypatch, mp3, present):
    data = payload(mp3, alignment=None, normalized_alignment=None)
    if not present:
        del data['alignment'], data['normalized_alignment']
    transport(monkeypatch, data)
    sidecar = adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123')
    record = json.loads(sidecar.read_text())
    assert record['alignment'] is record['normalized_alignment'] is None


def test_metadata_allowlist_excludes_payload_secrets_even_in_allowed_fields(tmp_path, monkeypatch, mp3):
    data = payload(mp3, requestid='fixture-app:fixture-key',
                   usage={'characters': 2, 'request_detail': {'Authorization': 'fixture-key'}},
                   request_detail={'Authorization': 'fixture-key'}, audio='fixture-key')
    data['alignment']['request_detail'] = 'fixture-app:fixture-key'
    transport(monkeypatch, data)
    sidecar = adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123')
    saved = sidecar.read_text()
    assert not any(value in saved for value in ('fixture-key', 'fixture-app', 'audio_base64', 'request_detail', 'Authorization'))
    assert json.loads(saved)['alignment'] == alignment()


def test_gateway_usage_headers_are_numeric_allowlisted_and_separate_from_json_usage(tmp_path, monkeypatch, mp3):
    headers = {
        'X-Usage-Cost': ' 1.25e-3 ', 'x-usage-prompt-tokens': '69',
        'X-USAGE-COMPLETION-TOKENS': '0', 'X-Usage-Total-Tokens': '69',
        'X-Usage-Cache-Hit-Tokens': '0', 'X-Usage-Cache-Write-Tokens': '0',
        'X-Usage-Created-Unix': '1780000000', 'X-Usage-First-Token-Unix': '1780000000.5',
        'X-Usage-Finished-Unix': '1780000001',
        'X-Usage-Request-Detail': 'fixture-key', 'Authorization': 'fixture-key',
    }
    transport(monkeypatch, payload(mp3), headers=headers)
    sidecar = adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123')
    record = json.loads(sidecar.read_text())
    assert record['usage'] == {'characters': 2}
    assert record['usage_headers'] == {
        name.lower(): float(value) for name, value in headers.items()
        if name not in {'X-Usage-Request-Detail', 'Authorization'}
    }
    assert 'fixture-key' not in sidecar.read_text()
    bad_values = ['NaN', 'Inf', '-Infinity', '1e9999', 'fixture-key', '', None, True, '9' * 500]
    transport(monkeypatch, payload(mp3), headers=dict(zip(list(headers)[:9], bad_values)))
    sidecar = adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123')
    assert json.loads(sidecar.read_text())['usage_headers'] == {}


@pytest.mark.parametrize('model,limit', [('eleven_v3', 5000), ('eleven_multilingual_v2', 10000),
                                        ('eleven_flash_v2_5', 40000)])
def test_model_text_limit_rejects_before_credentials_and_accepts_exact_boundary(tmp_path, monkeypatch, mp3, model, limit):
    import aihub_adapter

    def forbidden_credentials():
        raise AssertionError('oversized input must be rejected before credentials')

    original = aihub_adapter.credentials
    monkeypatch.setattr(aihub_adapter, 'credentials', forbidden_credentials)
    calls = transport(monkeypatch, payload(mp3))
    with pytest.raises(ValueError, match=str(limit)) as caught:
        adapter().synthesize('你' * (limit + 1), tmp_path / 'speech.wav', speaker='voice123', model=model)
    assert '你' not in str(caught.value)
    assert calls == []
    monkeypatch.setattr(aihub_adapter, 'credentials', original)
    adapter().synthesize('你' * limit, tmp_path / 'speech.wav', speaker='voice123', model=model)
    assert len(calls) == 1


def project(**overrides):
    return {'voice': {'provider': 'aihub-elevenlabs', 'speaker': 'voice123', **overrides}}


def test_voice_stage_routes_effective_settings_and_reports_raw_clock_mapping(tmp_path, monkeypatch, mp3):
    calls = transport(monkeypatch, payload(mp3))
    catalog = stage().prepare_audio(project(tempo=1.1, model=MODELS[1], voice_settings={'speed': .9}),
                                    {'narrations': [{'id': 'n1', 'text': '你好'}]}, tmp_path)
    row = catalog['n1']
    assert row['provider'] == 'aihub-elevenlabs'
    assert row['model'] == calls[0]['json']['model_id'] == MODELS[1]
    assert row['voice_settings'] == calls[0]['json']['voice_settings'] == {**DEFAULTS, 'speed': .9}
    assert row['post_tempo'] == 1.1
    assert row['duration'] == pytest.approx(1 / 1.1, abs=.03)
    assert Path(row['provider_metadata_path']).is_file()
    record = json.loads(Path(row['provider_metadata_path']).read_text())
    assert record['alignment'] == alignment()
    assert row['alignment_clock'] == 'raw_audio'
    assert row['alignment_time_mapping'] == {
        'source_clock': 'raw_audio', 'target_clock': 'ready_audio',
        'scale': 1 / 1.1, 'offset_seconds': 0, 'exact': False,
    }
    assert json.loads((tmp_path / 'audio_catalog.json').read_text()) == catalog
    reused = stage().prepare_audio(project(tempo=1.1, model=MODELS[1], voice_settings={'speed': .9}),
                                   {'narrations': [{'id': 'n1', 'text': '你好'}]}, tmp_path)
    assert reused == catalog
    assert len(calls) == 1


def test_every_effective_setting_participates_in_elevenlabs_cache_identity(tmp_path, monkeypatch, mp3):
    calls = transport(monkeypatch, payload(mp3))
    plan = {'narrations': [{'id': 'n1', 'text': '你好'}]}
    prepare = stage().prepare_audio
    baseline = prepare(project(), plan, tmp_path)['n1']
    explicit = prepare(project(voice_settings=V3_DEFAULTS), plan, tmp_path)['n1']
    assert baseline == explicit
    assert len(calls) == 1
    variants = [{'voice_settings': {name: value}} for name, value in
                [('stability', 1), ('similarity_boost', .8), ('style', .2), ('speed', .9), ('use_speaker_boost', False)]]
    variants += [{'model': MODELS[1]}, {'model': MODELS[2]}, {'speaker': 'voice456'}, {'tempo': 1.1}]
    paths = {baseline['path']}
    for overrides in variants:
        row = prepare(project(**overrides), plan, tmp_path)['n1']
        assert row['path'] not in paths
        paths.add(row['path'])
    assert len(calls) == len(variants) + 1
    changed = prepare(project(), {'narrations': [{'id': 'n1', 'text': '新正文'}]}, tmp_path)['n1']
    assert changed['path'] not in paths


@pytest.mark.parametrize('model', ['eleven_v3', 'eleven_flash_v2_5'])
def test_language_code_forwarded_and_saved(tmp_path, monkeypatch, mp3, model):
    calls = transport(monkeypatch, payload(mp3))
    sidecar = adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='j2FxFb20sd3xlm2pRaM5',
                                   model=model, language_code='zh', voice_settings={'speed': 1.1})
    assert calls[0]['json']['language_code'] == 'zh'
    assert json.loads(sidecar.read_text())['language_code'] == 'zh'
    assert adapter().tts_settings(speaker='voice123', model=model, language_code='zh')['language_code'] == 'zh'


@pytest.mark.parametrize('language_code', ['', 'ZH', 'zh-CN', 1, True, 'z', 'zh&model=other'])
def test_invalid_language_code_rejected_before_transport(tmp_path, language_code):
    with pytest.raises(ValueError, match='language_code'):
        adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123', language_code=language_code)


def test_multilingual_rejects_unsupported_language_override(tmp_path):
    with pytest.raises(ValueError, match='language_code'):
        adapter().synthesize('你好', tmp_path / 'speech.wav', speaker='voice123',
                             model='eleven_multilingual_v2', language_code='zh')


def test_probe_voice_config_and_language_changes_use_separate_caches(tmp_path, monkeypatch, mp3):
    calls = transport(monkeypatch, payload(mp3))
    plan = {'narrations': [{'id': 'n1', 'text': '你好'}]}
    config = project(speaker='j2FxFb20sd3xlm2pRaM5', language_code='zh',
                     voice_settings={'stability': .5, 'similarity_boost': .75, 'style': 0, 'speed': 1.1}, tempo=1)
    prepare = stage().prepare_audio
    catalog = prepare(config, plan, tmp_path)
    assert calls[0]['json'] == {'text': '你好', 'model_id': 'eleven_v3', 'language_code': 'zh',
                                'voice_settings': {**V3_DEFAULTS, 'speed': 1.1}}
    assert catalog['n1']['post_tempo'] == 1
    assert catalog['n1']['language_code'] == 'zh'
    assert catalog['n1']['duration'] == pytest.approx(1, abs=.01)
    assert prepare(config, plan, tmp_path) == catalog
    config['voice']['language_code'] = 'en'
    english = prepare(config, plan, tmp_path)
    del config['voice']['language_code']
    autodetect = prepare(config, plan, tmp_path)
    assert len({row['n1']['path'] for row in (catalog, english, autodetect)}) == 3
    assert len(calls) == 3
    assert 'language_code' not in calls[2]['json']


def test_elevenlabs_env_convention_loads_before_credentials(tmp_path, monkeypatch, mp3):
    import aihub_adapter

    config = tmp_path / 'fixture.env'
    config.write_text('APP_ID=file-app\nAPP_KEY=file-key\n')
    monkeypatch.delenv('APP_KEY')
    monkeypatch.setattr(aihub_adapter, 'credentials',
                        lambda: (os.environ['APP_ID'], os.environ['APP_KEY']))
    calls = transport(monkeypatch, payload(mp3))
    stage().prepare_audio(project(), {'narrations': [{'id': 'n1', 'text': '你好'}]}, tmp_path / 'work')
    assert calls[0]['headers']['Authorization'].startswith('Bearer fixture-app:file-key?')
    assert not (tmp_path / 'work/.env').exists()


def test_elevenlabs_requires_explicit_speaker_even_with_injected_synthesizer(tmp_path):
    with pytest.raises(ValueError, match='speaker'):
        stage().prepare_audio({'voice': {'provider': 'aihub-elevenlabs'}}, {'narrations': []},
                              tmp_path, synthesizer=lambda *a, **k: None)


def test_independent_stage_import_and_help_does_not_import_any_adapter(tmp_path):
    entry = tmp_path / 'package/skills/video-voiceover/scripts/voiceover.py'
    entry.parent.mkdir(parents=True)
    entry.write_bytes((ROOT / 'skills/video-voiceover/scripts/voiceover.py').read_bytes())
    for name in ('aihub_adapter', 'elevenlabs_adapter', 'lib'):
        (entry.parent / (name + '.py')).write_text('raise AssertionError("eager import")\n')
    result = subprocess.run([sys.executable, str(entry), '--help'], capture_output=True, text=True,
                            env={**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 0, result.stderr


def test_metadata_publication_failure_restores_previous_complete_pair(tmp_path, monkeypatch, mp3):
    wav = tmp_path / 'speech.wav'
    sidecar = wav.with_suffix('.provider.json')
    wav.write_bytes(b'previous audio')
    sidecar.write_text('{"previous":"metadata"}')
    before = (wav.read_bytes(), sidecar.read_bytes())
    transport(monkeypatch, payload(mp3))
    replace = Path.replace
    def fail_metadata_once(self, target):
        if self.name == 'speech.provider.json' and self.parent != tmp_path:
            raise OSError('simulated metadata publication failure')
        return replace(self, target)
    monkeypatch.setattr(Path, 'replace', fail_metadata_once)
    with pytest.raises(OSError):
        adapter().synthesize('你好', wav, speaker='voice123')
    assert (wav.read_bytes(), sidecar.read_bytes()) == before


def test_cache_rejects_mismatched_raw_audio_and_provider_alignment(tmp_path, monkeypatch, mp3):
    calls = transport(monkeypatch, payload(mp3))
    voice = stage();plan = {'narrations': [{'id':'n1', 'text':'你好'}]}
    first = voice.prepare_audio(project(), plan, tmp_path/'voice')
    sidecar = Path(first['n1']['provider_metadata_path'])
    metadata = json.loads(sidecar.read_text())
    metadata['audio_sha256'] = '0' * 64
    sidecar.write_text(json.dumps(metadata))
    second = voice.prepare_audio(project(), plan, tmp_path/'voice')
    assert len(calls) == 2
    fixed = json.loads(Path(second['n1']['provider_metadata_path']).read_text())
    import hashlib
    raw = sidecar.with_name(sidecar.name.replace('.provider.json', '.wav'))
    assert fixed['audio_sha256'] == hashlib.sha256(raw.read_bytes()).hexdigest()


def test_interrupted_postprocessing_cannot_reuse_older_processed_generation(tmp_path, monkeypatch, mp3):
    calls = transport(monkeypatch, payload(mp3))
    voice = stage();plan = {'narrations': [{'id':'n1', 'text':'你好'}]}
    first = voice.prepare_audio(project(), plan, tmp_path/'voice')
    sidecar = Path(first['n1']['provider_metadata_path'])
    record = json.loads(sidecar.read_text())
    record.pop('audio_sha256')  # Legacy entry now requires a new complete generation.
    sidecar.write_text(json.dumps(record))
    real_run = voice.run
    def interrupted(*args, **kwargs):
        raise RuntimeError('postprocessing interrupted after new raw audio')
    monkeypatch.setattr(voice, 'run', interrupted)
    with pytest.raises(RuntimeError, match='postprocessing'):
        voice.prepare_audio(project(), plan, tmp_path/'voice')
    monkeypatch.setattr(voice, 'run', real_run)
    recovered = voice.prepare_audio(project(), plan, tmp_path/'voice')
    assert len(calls) == 3, 'partial new raw generation must not validate older processed audio'
    with wave.open(recovered['n1']['path']) as audio:
        assert audio.getnframes() > 0
