"""Map source acoustic evidence and already-timed narration without mutating sources."""
import importlib.util
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/video-script/scripts'


def module():
    p=SCRIPTS/'editorial_timing.py';assert p.exists(),'generic output-clock evidence mapping not implemented'
    spec=importlib.util.spec_from_file_location('editorial_timing',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def compiled():
    return {'duration':9,'operations':[
        {'id':'p','type':'play','audio':'original','source_id':'m','source_start':50,'source_end':54,'output_start':0,'output_end':4},
        {'id':'f','type':'freeze','audio':'mute','source_id':'m','source_start':53,'source_end':53,'output_start':4,'output_end':6},
        {'id':'r','type':'replay','audio':'mute','source_id':'m','source_start':50,'source_end':51,'output_start':6,'output_end':7},
        {'id':'p2','type':'play','audio':'original','source_id':'m','source_start':54,'source_end':56,'output_start':7,'output_end':9}],
        'narrations':[{'id':'n','text':'看清这个姿势','narration':'看清这个姿势','start':4.1,'end':5.9,'pause_after_ms':100}]}


def test_original_words_map_only_to_play_not_frozen_repeated_audio():
    asr=[{'start':51,'end':55,'text':'走出去','words':[{'start':51,'end':52,'text':'走'},{'start':54,'end':55,'text':'出去'}]}]
    acoustic={'sentence_anchors':[{'time':52.3,'pause_start':52,'confidence':'high'}]}
    out=module().map_source_evidence(compiled(),asr,acoustic,[])
    assert [(x['start'],x['end']) for x in out['speech_spans']]==[(1,2),(7,8)]
    assert out['sentence_anchors'][0]['time']==2.3
    assert [(x['start'],x['end']) for x in out['quiet_windows']]==[(4,6),(6,7)]


def test_tts_meta_requires_exact_matching_text_and_no_extra_speed():
    m=module();c=compiled();catalog={'n':{'text':'看清这个姿势','path':'ready.wav','duration':1.8,'post_tempo':1.1}}
    out=m.build_tts_meta(c,catalog)
    assert out['segments'][0]['start']==4.1
    assert out['segments'][0]['audio_duration']==1.8
    assert out['segments'][0]['preapplied_tempo']==1.1
    catalog['n']['text']='过期文案'
    import pytest
    with pytest.raises(ValueError,match='text'):m.build_tts_meta(c,catalog)


def test_tts_meta_rejects_time_window_shorter_than_actual_voice():
    c=compiled();catalog={'n':{'text':'看清这个姿势','path':'ready.wav','duration':2.5,'post_tempo':1.1}}
    import pytest
    with pytest.raises(ValueError,match='duration'):module().build_tts_meta(c,catalog)


def test_tts_meta_preserves_mimo_identity_instead_of_labeling_it_doubao():
    c=compiled();catalog={'n':{'text':'看清这个姿势','path':'ready.wav','duration':1.8,'post_tempo':1,
        'provider':'aihub-mimo','model':'api_xiaomi_mimo-v2.5-tts','speaker':'茉莉'}}
    out=module().build_tts_meta(c,catalog)
    assert out['engine']=='mimo-tts'
    assert out['segments'][0]['tts_provider']=='aihub-mimo'
    assert out['segments'][0]['tts_model']=='api_xiaomi_mimo-v2.5-tts'
    assert out['segments'][0]['tts_speaker']=='茉莉'
    assert out['segments'][0]['preapplied_tempo']==1


def test_elevenlabs_audio_keeps_its_provider_and_model_in_delivery_metadata():
    c=compiled();catalog={'n':{'text':'看清这个姿势','path':'ready.wav','duration':1.8,'post_tempo':1,
        'provider':'aihub-elevenlabs','model':'eleven_v3','speaker':'mandarin-voice-id'}}
    out=module().build_tts_meta(c,catalog)
    assert out['engine']=='elevenlabs-tts'
    assert out['segments'][0]['tts_provider']=='aihub-elevenlabs'
    assert out['segments'][0]['tts_model']=='eleven_v3'
