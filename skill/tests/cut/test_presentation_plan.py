"""Behavioral timing regressions for authored evidence, holds and protected audio."""
import copy
import importlib.util
from pathlib import Path
import pytest

path=Path(__file__).resolve().parents[2]/'skills/video-cut/scripts/presentation_plan.py'
spec=importlib.util.spec_from_file_location('presentation_contract_test',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
compile_plan=module.compile_plan


def plan():
 return {'schema_version':1,'fps':24,'sources':{'movie':{'path':'source.mp4','media_origin':45,'media_duration':100}},
 'operations':[
  {'id':'event','type':'play','source_id':'movie','source_start':50,'source_end':60,'audio':'original','purpose':'event'},
  {'id':'replay','type':'replay','source_id':'movie','source_start':52,'source_end':54,'audio':'mute','purpose':'compare','return_to':'resume'},
  {'id':'hold','type':'freeze','source_id':'movie','source_start':53,'duration':3,'audio':'mute','purpose':'read evidence'},
  {'id':'resume','type':'play','source_id':'movie','source_start':60,'source_end':70,'audio':'original','purpose':'consequence'}],
 'narrations':[{'id':'n','text':'Look back at the evidence.','at_operation':'replay','offset':.1,'allowed_operations':['replay','hold'],
 'after_events':[{'operation_id':'event','source_time':59}],
 'proof_cues':[{'audio_start':0,'audio_end':4,'operation_ids':['replay','hold']}]}],
 'protected_audio':[{'operation_id':'resume','source_start':60,'source_end':63,'purpose':'dialogue'}]}


def test_repeat_origin_and_freeze_map_to_monotonic_output():
 p=plan();old=copy.deepcopy(p);c=compile_plan(p,{'n':4})
 assert p==old
 assert [(o['output_start'],o['output_end']) for o in c['operations']]==[(0,10),(10,12),(12,15),(15,25)]
 assert c['operations'][1]['media_start']==7
 assert c['operations'][2]['source_start']==c['operations'][2]['source_end']==53
 assert c['narrations'][0]['start']==10.1
 assert c['protected_audio'][0]['output_start']==15


def test_speech_cannot_silently_shift_past_evidence_window():
 with pytest.raises(ValueError,match='exceeds authored evidence window'):compile_plan(plan(),{'n':6})


def test_early_event_reveal_is_rejected():
 p=plan();p['narrations'][0].update(at_operation='event',offset=1,allowed_operations=['event'],proof_cues=[])
 with pytest.raises(ValueError,match='before it is shown'):compile_plan(p,{'n':4})


def test_proof_must_be_visible_for_the_spoken_clause():
 p=plan();p['narrations'][0]['proof_cues'][0]['operation_ids']=['hold']
 with pytest.raises(ValueError,match='no simultaneous proof'):compile_plan(p,{'n':4})


def test_key_dialogue_including_duck_ramp_is_protected():
 p=plan();p['narrations'][0].update(at_operation='resume',offset=2,allowed_operations=['resume'],proof_cues=[])
 with pytest.raises(ValueError,match='protected original audio'):compile_plan(p,{'n':4})


@pytest.mark.parametrize('field,value',[('duration',float('nan')),('duration',0),('audio','original')])
def test_invalid_hold_rejected(field,value):
 p=plan();p['operations'][2][field]=value
 with pytest.raises(ValueError):compile_plan(p,{'n':4})


def test_replay_cannot_show_unseen_future_as_past():
 p=plan();p['operations'][1].update(source_start=80,source_end=82)
 with pytest.raises(ValueError,match='already-shown'):compile_plan(p,{'n':4})


def test_replay_needs_explicit_return_to_story():
 p=plan();p['operations'][1]['return_to']='event'
 with pytest.raises(ValueError,match='return_to'):compile_plan(p,{'n':4})


def test_two_narrations_cannot_overlap():
 p=plan();n=copy.deepcopy(p['narrations'][0]);n['id']='n2';p['narrations'].append(n)
 with pytest.raises(ValueError,match='overlapping narration'):compile_plan(p,{'n':4,'n2':4})


def test_output_rounding_is_per_frame_not_accumulated_float():
 p=plan();p['operations'][2]['duration']=3.021;c=compile_plan(p,{'n':4})
 assert c['operations'][2]['frames']==73
 assert c['operations'][-1]['output_start']==361/24
