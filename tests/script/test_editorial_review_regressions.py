"""Concrete remaining findings from the independent fix review."""
import copy
import json
import pytest
from test_editorial_contract import fixture,modules
from test_editorial_runner import real_config,module as runner_module,Model
from test_editorial_inputs import fixture as inputs_fixture,module as inputs_module


def test_missing_required_hash_entry_cannot_turn_incomplete_run_into_cache_hit(tmp_path):
    config,story=real_config(tmp_path);out=tmp_path/'out';m=runner_module()
    m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    p=out/'editorial_run.json';d=json.loads(p.read_text());del d['output_hashes']['evidence_bundle.json'];p.write_text(json.dumps(d));(out/'evidence_bundle.json').unlink()
    result=m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    assert result['cache']=='miss'
    assert (out/'evidence_bundle.json').exists()


@pytest.mark.parametrize('field,value',[('source_start',[]),('source_end',None)])
def test_bad_operation_number_is_a_precise_finding_not_validator_exception(field,value):
    project,evidence,style,story=fixture();story['paragraphs'][1]['presentation'][0][field]=value
    findings=modules()[0].validate_story(story,project,evidence,style)
    assert findings
    assert any('paragraphs[1].presentation[0]' in f['path'] for f in findings)


@pytest.mark.parametrize('operation_ids',[[],['o2']])
def test_static_proof_visibility_failure_is_rejected(operation_ids):
    project,evidence,style,story=fixture();story['paragraphs'][0]['narration']['proof_cues']=[{'audio_start':0,'audio_end':.5,'operation_ids':operation_ids}]
    assert any(f['code'] in {'proof_cue_shape','proof_visibility'} for f in modules()[0].validate_story(story,project,evidence,style))


def test_subframe_freeze_cannot_enter_execution_plan():
    project,evidence,style,story=fixture();op=story['paragraphs'][0]['presentation'][0];op.update(type='freeze',duration=.001,audio='mute')
    assert any(f['code']=='frame_duration' for f in modules()[0].validate_story(story,project,evidence,style))


def test_unimplemented_narration_speed_is_rejected():
    project,evidence,style,story=fixture();story['paragraphs'][0]['narration']['speed']=2
    assert any(f['code']=='unsupported_narration_field' for f in modules()[0].validate_story(story,project,evidence,style))


def test_question_can_only_be_answered_in_its_declared_payoff():
    project,evidence,style,story=fixture();story['paragraphs'][0]['questions_answered']=['q1']
    assert any(f['code']=='question_state' for f in modules()[0].validate_story(story,project,evidence,style))


def test_saved_draft_can_reference_current_movie_word_id(tmp_path):
    config,story=real_config(tmp_path);draft={'viewer_promise':'从一句话认清行动','angle':'对白与行动','passages':[{'id':'p','spoken_text':'他只说了一个字。','evidence_ids':['asr:0:word:0'],'original_audio_job':None,'return_to_story':'接到门后的回答'}]}
    (tmp_path/'draft.json').write_text(json.dumps(draft,ensure_ascii=False));d=json.loads(config.read_text());d.update(authoring_mode='write_then_plan',authoring_draft_path='draft.json');config.write_text(json.dumps(d))
    result=runner_module().run_project(config,tmp_path/'out',Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    assert result['status']=='ready_for_editorial_review'


def test_word_specific_correction_reaches_claim_validation_and_saved_draft(tmp_path):
    path,cfg=inputs_fixture(tmp_path,'film-B');cfg.update(id='film-B',verified_notes='notes.json');cfg['source']['range']=[50,75];path.write_text(json.dumps(cfg))
    (tmp_path/'notes.json').write_text(json.dumps({'schema_version':1,'source_id':'film-B','clock':'movie_source','notes':[{'id':'word-correction','start':55,'end':56,'text':'原声并没有说走','evidence':'回听原片55秒确认','supersedes':['asr:0:word:0']}]}))
    m=inputs_module();project=m.load_project(path);bundle=m.build_evidence(project);_,_,style,story=fixture();story['paragraphs'][0]['claims'][0]['evidence_ids']=['asr:0:word:0']
    assert any(f['code']=='disputed_evidence' for f in modules()[0].validate_story(story,project,bundle,style))


def test_corrected_word_cannot_be_reintroduced_via_saved_draft(tmp_path):
    config,story=real_config(tmp_path);cfg=json.loads(config.read_text());cfg.update(verified_notes='notes.json',authoring_mode='write_then_plan',authoring_draft_path='draft.json');config.write_text(json.dumps(cfg))
    note={'schema_version':1,'source_id':'film-B','clock':'movie_source','notes':[{'id':'fix','start':55,'end':56,'text':'该字识别有误','evidence':'实际回听记录','supersedes':['asr:0:word:0']}]}
    (tmp_path/'notes.json').write_text(json.dumps(note));draft={'viewer_promise':'听清对白','angle':'跟着人物走','passages':[{'id':'p','spoken_text':'听他说什么。','evidence_ids':['asr:0:word:0'],'original_audio_job':None,'return_to_story':'原声回答'}]};(tmp_path/'draft.json').write_text(json.dumps(draft))
    no_calls=Model([]);result=runner_module().run_project(config,tmp_path/'out',no_calls,model_identity='test-model')
    assert result['status']=='needs_review' and no_calls.calls==0
    assert result['findings'][0]['message']=='invalid draft evidence'


def test_invalid_freeze_with_stale_guard_does_not_crash_and_can_be_repaired(tmp_path):
    config,story=real_config(tmp_path);bad=copy.deepcopy(story)
    bad['paragraphs'][1]['presentation'][0].update(type='freeze',audio='mute',duration=10,source_start=[])
    model=Model([bad,story,{'verdict':'pass','findings':[]}])
    result=runner_module().run_project(config,tmp_path/'out',model,model_identity='test-model')
    assert result['status']=='ready_for_editorial_review'
    first=result['attempts'][0]['findings']
    assert any(f['code']=='source_range' for f in first)
    assert not any(f['code']=='validator_internal_error' for f in first)
