"""Changing Gemini behavior must invalidate each layer of understanding cache."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-understanding/scripts'))
from lib import CONFIG
import consolidate
import understanding_cache
import vlm
import brief_inputs
import brief_context
from test_vlm_fixes import _three_scene_setup


def test_outer_cache_records_reasoning_profile(monkeypatch,tmp_path):
    video=tmp_path/'source.mp4';video.write_bytes(b'video')
    scenes=tmp_path/'scenes.json';scenes.write_text('[]')
    monkeypatch.setitem(CONFIG,'understanding_provider_settings',{'reasoning_effort':'low'})
    a=understanding_cache._vlm_cache_payload(video,tmp_path,scenes,[])
    monkeypatch.setitem(CONFIG,'understanding_provider_settings',{'reasoning_effort':'high'})
    b=understanding_cache._vlm_cache_payload(video,tmp_path,scenes,[])
    assert a!=b


def test_consolidated_index_invalidates_when_profile_changes(monkeypatch,tmp_path):
    (tmp_path/'vlm_analysis.json').write_text('[]')
    monkeypatch.setitem(CONFIG,'understanding_provider_settings',{'reasoning_effort':'low'})
    consolidate._write_index_meta(tmp_path,[])
    assert consolidate._index_cache_matches(tmp_path,[])
    monkeypatch.setitem(CONFIG,'understanding_provider_settings',{'reasoning_effort':'high'})
    assert not consolidate._index_cache_matches(tmp_path,[])


def test_brief_readers_agree_with_generator_provider_identity(monkeypatch,tmp_path):
    monkeypatch.setitem(CONFIG,'understanding_provider_settings',{'reasoning_effort':'low'})
    assert brief_inputs._mimo_video_settings_fingerprint()==vlm.mimo_video_settings_fingerprint()
    (tmp_path/'vlm_analysis.json').write_text('[]')
    (tmp_path/'understanding_index.json').write_text(json.dumps({'characters':[],'relationships':[],'plot_points':[],'entities':[]}))
    consolidate._write_index_meta(tmp_path,[])
    monkeypatch.setitem(CONFIG,'understanding_provider_settings',{'reasoning_effort':'high'})
    assert brief_context._load_consolidation(tmp_path,[])=={}


def test_partial_scene_cache_does_not_mix_reasoning_profiles(monkeypatch,tmp_path):
    scenes,frames=_three_scene_setup(monkeypatch,tmp_path,workers=1)
    monkeypatch.setitem(CONFIG,'understanding_provider_settings',{'reasoning_effort':'low'})
    state={'fail':True,'calls':0}
    def api(payload):
        state['calls']+=1
        if state['fail'] and '2.0s' in payload['messages'][0]['content'][-1]['text']:
            raise RuntimeError('HTTP429')
        return {'choices':[{'message':{'content':'【描述】人物站在门旁。'}}]}
    monkeypatch.setattr(vlm,'api_call',api)
    with pytest.raises(RuntimeError):vlm.analyze_scenes(scenes,frames,tmp_path)
    assert len(json.loads((tmp_path/'vlm_scene_cache.json').read_text()))==2
    state['fail']=False;before=state['calls']
    monkeypatch.setitem(CONFIG,'understanding_provider_settings',{'reasoning_effort':'high'})
    vlm.analyze_scenes(scenes,frames,tmp_path)
    assert state['calls']-before==3
