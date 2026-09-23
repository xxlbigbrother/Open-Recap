"""Real WAV/TTS bridge tests; no remote service or movie-specific constants."""
import importlib.util
import base64
import json
import math
from pathlib import Path
import struct
import sys
import wave
import pytest

ROOT=Path(__file__).resolve().parents[1]


def module():
    p=ROOT/'editorial_render.py';assert p.exists(),'generic editorial renderer bridge missing'
    spec=importlib.util.spec_from_file_location('editorial_render',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def synth(text,path,rate='+0%'):
    with wave.open(str(path),'wb') as f:
        f.setparams((1,2,24000,0,'NONE','not compressed'))
        f.writeframes(b''.join(struct.pack('<h',int(2000*math.sin(i*2*math.pi*440/24000))) for i in range(24000)))


def test_audio_cache_bound_to_text_voice_and_rate(tmp_path):
    m=module();project={'voice':{'speaker':'voice-a','tempo':1.1}};plan={'narrations':[{'id':'n1','text':'一句话'}]}
    cat=m.prepare_audio(project,plan,tmp_path,synthesizer=synth)
    assert .85<cat['n1']['duration']<.95
    assert cat['n1']['post_tempo']==1.1
    def forbidden(*args,**kwargs):raise AssertionError('valid cache should skip remote request')
    reused=m.prepare_audio(project,plan,tmp_path,synthesizer=forbidden)
    assert reused['n1']['path']==cat['n1']['path']
    plan['narrations'][0]['text']='另一句话';changed=m.prepare_audio(project,plan,tmp_path,synthesizer=synth)
    assert changed['n1']['path']!=cat['n1']['path']
    project['voice']['speaker']='voice-b';voice=m.prepare_audio(project,plan,tmp_path,synthesizer=synth)
    assert voice['n1']['path']!=changed['n1']['path']


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


def test_render_uses_external_env_without_copying_keys_into_package(tmp_path,monkeypatch):
    m=module();package=tmp_path/'package';package.mkdir();monkeypatch.setattr(m,'ROOT',package)
    private=tmp_path/'private.env';private.write_text('# outside package\nAPP_ID="file-app"\nAPP_KEY=file-key\nSUBTITLE_FONT_NAME="Noto Sans CJK SC"\n')
    monkeypatch.setenv('RECAP_ENV_FILE',str(private));monkeypatch.setenv('APP_ID','environment-app')
    monkeypatch.delenv('APP_KEY',raising=False);monkeypatch.delenv('SUBTITLE_FONT_NAME',raising=False)
    monkeypatch.setattr(m,'synthesize_mimo_preset',lambda text,path,**kwargs:synth(text,path))
    cat=m.prepare_audio({'voice':{'provider':'aihub-mimo','speaker':'茉莉','tempo':1}},
        {'narrations':[{'id':'n','text':'你好'}]},package/'work')
    import os
    assert os.environ['APP_ID']=='environment-app'
    assert os.environ['APP_KEY']=='file-key'
    assert os.environ['SUBTITLE_FONT_NAME']=='Noto Sans CJK SC'
    assert cat['n']['provider']=='aihub-mimo'
    assert not (package/'.env').exists()


def test_mimo_request_keeps_only_spoken_text_and_records_actual_voice(tmp_path,monkeypatch):
    sys.path.insert(0,str(ROOT))
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
