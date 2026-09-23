"""Exercise generation, bounded repair and artifact cache via an external-model seam."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/video-script/scripts';sys.path.insert(0,str(SCRIPTS))
from test_editorial_contract import fixture as story_fixture
from test_editorial_inputs import fixture as input_fixture
from test_editorial_catalog import bundle as style_fixture


def module():
    path=SCRIPTS/'editorial_runner.py';assert path.exists(),'reference-driven runner not implemented'
    spec=importlib.util.spec_from_file_location('editorial_runner',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def response(obj):return {'choices':[{'message':{'content':json.dumps(obj,ensure_ascii=False)}}]}


class Model:
    def __init__(self,outputs):self.outputs=list(outputs);self.calls=0
    def __call__(self,payload):
        self.calls+=1
        assert payload['messages'] and payload['max_tokens']>0
        value=self.outputs.pop(0)
        if isinstance(value,Exception):raise value
        return response(value)


def real_config(tmp_path):
    path,cfg=input_fixture(tmp_path,'film-B');cfg['id']='film-B';cfg['source']['range']=[50,75]
    path.write_text(json.dumps(cfg));style_fixture(tmp_path)
    _,_,_,story=story_fixture();story['style']={'id':'style','version':1}
    return path,story


def test_bad_evidence_is_repaired_and_only_valid_artifacts_published(tmp_path):
    config,story=real_config(tmp_path);bad=copy.deepcopy(story);bad['paragraphs'][0]['claims'][0]['evidence_ids']=['nonexistent']
    model=Model([bad,story,{'verdict':'pass','findings':[]}]);out=tmp_path/'out'
    result=module().run_project(config,out,call_model=model,model_identity='test-model')
    assert result['status']=='ready_for_editorial_review'
    published=json.loads((out/'recap_story_plan.json').read_text())
    assert published['paragraphs'][0]['claims'][0]['evidence_ids']==['frame:0:55']
    assert json.loads((out/'editorial_plan.json').read_text())['narrations'][0]['text']=='门锁转了，门却没开。'
    assert model.calls==3


def test_failed_regeneration_preserves_previous_accepted_artifacts(tmp_path):
    config,story=real_config(tmp_path);out=tmp_path/'out';m=module()
    m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    original=(out/'recap_story_plan.json').read_bytes()
    bad=copy.deepcopy(story);bad['paragraphs'][0]['claims'][0]['evidence_ids']=['wrong']
    result=m.run_project(config,out,Model([bad,bad,bad]),model_identity='test-model',force=True)
    assert result['status']=='needs_review'
    assert (out/'recap_story_plan.json').read_bytes()==original
    assert len(result['attempts'])==3


def test_failed_regeneration_preserves_accepted_input_snapshots(tmp_path):
    config,story=real_config(tmp_path);out=tmp_path/'out';m=module()
    m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    names=['input_manifest.json','evidence_bundle.json','style_snapshot.json','recap_story_plan.json']
    original={name:(out/name).read_bytes() for name in names}
    (tmp_path/'understanding/asr_result.json').write_text('[]')
    import hashlib
    metadata=tmp_path/'understanding/asr_result.json.meta.json';data=json.loads(metadata.read_text())
    data['artifact_fingerprint']=hashlib.sha256(b'[]').hexdigest();metadata.write_text(json.dumps(data))
    bad=copy.deepcopy(story);bad['paragraphs'][0]['claims'][0]['evidence_ids']=['wrong']
    m.run_project(config,out,Model([bad,bad,bad]),model_identity='test-model')
    assert original=={name:(out/name).read_bytes() for name in names}
    m.run_project(config,out,Model([]),model_identity='test-model',prepare_only=True)
    assert original=={name:(out/name).read_bytes() for name in names}


def test_model_generation_configuration_is_applied_at_request_boundary(tmp_path):
    config,story=real_config(tmp_path);data=json.loads(config.read_text());data['generation']={'temperature':.3,'max_tokens':9000};config.write_text(json.dumps(data))
    payloads=[];outputs=[story,{'verdict':'pass','findings':[]}]
    def model(payload):payloads.append(payload);return response(outputs.pop(0))
    module().run_project(config,tmp_path/'out',model,model_identity='test-model')
    assert payloads[0]['temperature']==.3
    assert payloads[0]['max_tokens']==9000


def test_cache_requires_unchanged_inputs_and_intact_output(tmp_path):
    config,story=real_config(tmp_path);out=tmp_path/'out';m=module()
    m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    no_calls=Model([])
    assert m.run_project(config,out,no_calls,model_identity='test-model')['cache']=='hit'
    assert no_calls.calls==0
    (out/'editorial_plan.json').write_text('{}')
    refreshed=m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    assert refreshed['cache']=='miss'
    assert 'narrations' in json.loads((out/'editorial_plan.json').read_text())
    (tmp_path/'understanding/understanding_index.json').write_text('{"plot_points":["changed context"]}')
    assert m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')['cache']=='miss'


def test_semantic_reviewer_failure_is_visible_not_a_pass(tmp_path):
    config,story=real_config(tmp_path);out=tmp_path/'out'
    result=module().run_project(config,out,Model([story,RuntimeError('service unavailable')]),model_identity='test-model')
    assert result['status']=='needs_review'
    assert result['findings'][0]['code']=='review_unavailable'
    assert not (out/'recap_story_plan.json').exists()


def test_prepare_only_needs_no_credentials_or_model(tmp_path):
    config,_=real_config(tmp_path);out=tmp_path/'out'
    result=module().run_project(config,out,Model([]),model_identity='test-model',prepare_only=True)
    assert result['status']=='prepared'
    assert (out/'evidence_bundle.json').exists()
    assert not (out/'recap_story_plan.json').exists()


def test_cache_invalidated_when_model_sampling_configuration_changes(tmp_path):
    config,story=real_config(tmp_path);out=tmp_path/'out';m=module()
    m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    data=json.loads(config.read_text());data['generation']={'temperature':.3,'max_tokens':9000};config.write_text(json.dumps(data))
    assert m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')['cache']=='miss'


def test_malformed_nested_model_data_is_retained_for_diagnosis(tmp_path):
    config,story=real_config(tmp_path);story['paragraphs'][0]['narration']['after_events']=['bad event shape']
    result=module().run_project(config,tmp_path/'out',Model([story,story,story]),model_identity='test-model')
    assert result['status']=='needs_review'
    assert result['attempts'][0]['story']['paragraphs'][0]['narration']['after_events']==['bad event shape']
    assert result['attempts'][0]['findings'][0]['path']!='$', 'nested shape error must be actionable'


def test_two_pass_authoring_carries_continuous_draft_into_planning(tmp_path):
    config,story=real_config(tmp_path);data=json.loads(config.read_text());data['authoring_mode']='write_then_plan';config.write_text(json.dumps(data))
    draft={'viewer_promise':'看清门锁的变化','angle':'门为什么没开','passages':[{'id':'p1','spoken_text':'门锁转了，门却没开。','evidence_ids':['frame:0:55'],'original_audio_job':'随后让门后回答原声接管','return_to_story':'听到回答后作出选择'}]}
    payloads=[];outputs=[draft,story,{'verdict':'pass','findings':[]}]
    def model(payload):payloads.append(payload);return response(outputs.pop(0))
    result=module().run_project(config,tmp_path/'out',model,model_identity='test-model')
    assert result['status']=='ready_for_editorial_review'
    assert len(payloads)==3
    assert '门锁转了，门却没开。' in payloads[1]['messages'][-1]['content']
    assert json.loads((tmp_path/'out/authoring_draft.json').read_text())['passages'][0]['id']=='p1'


def test_invalid_authoring_draft_cannot_proceed_to_timing(tmp_path):
    config,story=real_config(tmp_path);data=json.loads(config.read_text());data['authoring_mode']='write_then_plan';config.write_text(json.dumps(data))
    result=module().run_project(config,tmp_path/'out',Model([{'passages':[]}]),model_identity='test-model')
    assert result['status']=='needs_review'
    assert result['findings'][0]['code']=='authoring_draft_invalid'
    paths=list((tmp_path/'out/attempts').glob('*/authoring_draft.json'))
    assert len(paths)==1
    assert json.loads(paths[0].read_text())=={'passages':[]}
    assert 'missing' in result['findings'][0]['message']


def test_saved_authoring_draft_can_resume_timing_without_regenerating_text(tmp_path):
    config,story=real_config(tmp_path)
    draft={'viewer_promise':'看懂开门','angle':'动作和后果','passages':[{'id':'p1','spoken_text':'门锁转了，门却没开。','evidence_ids':['frame:0:55'],'original_audio_job':None,'return_to_story':'听下一句回答'},{'id':'p2','spoken_text':'','evidence_ids':[],'original_audio_job':{'start':61,'end':65,'purpose':'听回答'},'return_to_story':'人物作决定'}]}
    (tmp_path/'draft.json').write_text(json.dumps(draft,ensure_ascii=False))
    data=json.loads(config.read_text());data.update(authoring_mode='write_then_plan',authoring_draft_path='draft.json');config.write_text(json.dumps(data))
    model=Model([story,{'verdict':'pass','findings':[]}]);result=module().run_project(config,tmp_path/'out',model,model_identity='test-model')
    assert result['status']=='ready_for_editorial_review'
    assert model.calls==2
    assert json.loads((tmp_path/'out/authoring_draft.json').read_text())==draft


def test_agent_candidate_uses_same_contract_and_records_authoring_origin(tmp_path):
    config,story=real_config(tmp_path);candidate=tmp_path/'candidate.json';candidate.write_text(json.dumps(story,ensure_ascii=False))
    reviewer=Model([{'verdict':'pass','findings':[]}])
    result=module().run_project(config,tmp_path/'out',reviewer,model_identity='test-model',candidate_path=candidate)
    assert result['status']=='ready_for_editorial_review'
    assert result['authoring_origin']=='agent_candidate'
    assert reviewer.calls==1
    assert json.loads((tmp_path/'out/recap_story_plan.json').read_text())==story


def test_invalid_agent_candidate_does_not_call_model_reviewer(tmp_path):
    config,story=real_config(tmp_path);story['paragraphs'][0]['claims'][0]['evidence_ids']=['wrong'];candidate=tmp_path/'candidate.json';candidate.write_text(json.dumps(story))
    no_calls=Model([]);result=module().run_project(config,tmp_path/'out',no_calls,model_identity='test-model',candidate_path=candidate)
    assert result['status']=='needs_review'
    assert no_calls.calls==0
