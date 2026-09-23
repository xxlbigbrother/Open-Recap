"""Explicit editorial review decisions never masquerade as machine approval."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from test_editorial_runner import real_config,Model,module as runner
from editorial_inputs import load_project,build_evidence
from editorial_development import evidence_digest
from editorial_projection import story_fingerprint

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/video-script/scripts'

def module():
    path=SCRIPTS/'editorial_adjudication.py';assert path.exists(),'explicit adjudication is not implemented'
    spec=importlib.util.spec_from_file_location('editorial_adjudication',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def setup(tmp_path):
    config,story=real_config(tmp_path);project=load_project(config);evidence=build_evidence(project)
    review={'story':story,'record_type':'editorial_semantic_review','review_stage':'semantic','review_completed':True,
            'evidence_sha256':evidence_digest(evidence),'source_id':evidence['source_id'],
            'findings':[{'severity':'error','code':'evidence','path':'paragraphs[0]','message':'reviewed assertion'}]}
    source=tmp_path/'machine-review.json';source.write_text(json.dumps(review))
    decision={'schema_version':1,'reviewer':{'kind':'agent','name':'editorial agent'},'verdict':'accept',
              'story_sha256':story_fingerprint(story),'evidence_sha256':evidence_digest(evidence),
              'source_review':{'path':'machine-review.json','sha256':hashlib.sha256(source.read_bytes()).hexdigest()},
              'decisions':[{'finding_index':0,'disposition':'dismiss','reason':'实际图证与稿件声明一致，机器读错该段','evidence_ids':['frame:0:55']}]}
    path=tmp_path/'decision.json';path.write_text(json.dumps(decision))
    return config,story,project,evidence,decision,path


def test_verified_decision_retains_original_review_and_reason(tmp_path):
    _,story,project,evidence,decision,path=setup(tmp_path)
    loaded=module().load_adjudication(path,story,evidence)
    assert loaded['source_review']['findings'][0]['severity']=='error'
    assert loaded['decision']['decisions'][0]['reason']=='实际图证与稿件声明一致，机器读错该段'
    assert loaded['origin']=='agent_adjudication'


@pytest.mark.parametrize('mutate',[
 lambda d:d.update(story_sha256='old'),
 lambda d:d.update(evidence_sha256='old'),
 lambda d:d.update(decisions=[]),
 lambda d:d['source_review'].update(sha256='invented'),
 lambda d:d['decisions'][0].update(evidence_ids=['missing']),
 lambda d:d['decisions'][0].update(reason=''),
 lambda d:d['decisions'].append(copy.deepcopy(d['decisions'][0])),
])
def test_invalid_decisions_cannot_accept_candidate(tmp_path,mutate):
    _,story,project,evidence,decision,path=setup(tmp_path);mutate(decision);path.write_text(json.dumps(decision))
    with pytest.raises(ValueError):module().load_adjudication(path,story,evidence)


def test_adjudication_must_bind_the_actual_reviewed_story(tmp_path):
    _,story,project,evidence,decision,path=setup(tmp_path)
    source=tmp_path/'machine-review.json';data=json.loads(source.read_text());data['story']['viewer_promise']='different story';source.write_text(json.dumps(data))
    decision['source_review']['sha256']=hashlib.sha256(source.read_bytes()).hexdigest();path.write_text(json.dumps(decision))
    with pytest.raises(ValueError):module().load_adjudication(path,story,evidence)


def test_runner_publishes_adjudicated_status_without_model_call(tmp_path):
    config,story,project,evidence,decision,path=setup(tmp_path);candidate=tmp_path/'candidate.json';candidate.write_text(json.dumps(story))
    cfg=json.loads(config.read_text());cfg['review_decision_path']='decision.json';config.write_text(json.dumps(cfg));out=tmp_path/'out'
    result=runner().run_project(config,out,Model([]),model_identity='test-model',candidate_path=candidate)
    assert result['status']=='ready_for_editorial_review'
    review=json.loads((out/'editorial_review.json').read_text())
    assert review['status']=='agent_adjudicated_user_review_pending'
    assert result['review_origin']=='agent_adjudication'
    assert 'editorial_adjudication.json' in result['output_hashes']
    assert json.loads((out/'editorial_adjudication.json').read_text())['source_review']['findings'][0]['severity']=='error'
    hit=runner().run_project(config,out,Model([]),model_identity='test-model',candidate_path=candidate)
    assert hit['cache']=='hit'


def test_adjudication_cannot_bypass_structural_invalidity(tmp_path):
    config,story,project,evidence,decision,path=setup(tmp_path);story['paragraphs'][0]['claims'][0]['evidence_ids']=['missing']
    candidate=tmp_path/'candidate.json';candidate.write_text(json.dumps(story));cfg=json.loads(config.read_text());cfg['review_decision_path']='decision.json';config.write_text(json.dumps(cfg))
    source=tmp_path/'machine-review.json';data=json.loads(source.read_text());data['story']=story;source.write_text(json.dumps(data))
    decision['story_sha256']=story_fingerprint(story);decision['source_review']['sha256']=hashlib.sha256(source.read_bytes()).hexdigest();path.write_text(json.dumps(decision))
    module().load_adjudication(path,story,evidence)
    result=runner().run_project(config,tmp_path/'out',Model([]),model_identity='test-model',candidate_path=candidate)
    assert result['status']=='needs_review'
    assert result['review_origin']=='agent_adjudication'
    assert result['findings'][0]['code']=='unknown_evidence'
    assert not (tmp_path/'out/editorial_plan.json').exists()


def test_timing_artifacts_cited_in_decision_are_bound_and_verified(tmp_path):
    _,story,project,evidence,decision,path=setup(tmp_path);audio=tmp_path/'audio_durations.json';audio.write_text('{"n1":3}')
    decision['supporting_artifacts']={'audio_durations.json':hashlib.sha256(audio.read_bytes()).hexdigest()};path.write_text(json.dumps(decision))
    loaded=module().load_adjudication(path,story,evidence)
    assert str(audio) in loaded['fingerprints']
    audio.write_text('{"n1":30}')
    with pytest.raises(ValueError):module().load_adjudication(path,story,evidence)


@pytest.mark.parametrize('stage,completed,code',[
 ('structural',False,'estimated_window_overrun'),
 ('semantic',False,'review_unavailable'),
 ('semantic',True,'review_unavailable'),
])
def test_only_completed_semantic_findings_can_be_adjudicated(tmp_path,stage,completed,code):
    _,story,project,evidence,decision,path=setup(tmp_path);source=tmp_path/'machine-review.json';review=json.loads(source.read_text())
    review.update(review_stage=stage,review_completed=completed);review['findings'][0]['code']=code;source.write_text(json.dumps(review));decision['source_review']['sha256']=hashlib.sha256(source.read_bytes()).hexdigest();path.write_text(json.dumps(decision))
    with pytest.raises(ValueError):module().load_adjudication(path,story,evidence)


def test_foreign_source_dismissal_evidence_is_rejected(tmp_path):
    _,story,project,evidence,decision,path=setup(tmp_path)
    evidence['records'].append({'id':'foreign','source_id':'other','kind':'dialogue','text':'other film'})
    decision['evidence_sha256']=evidence_digest(evidence);decision['decisions'][0]['evidence_ids']=['foreign']
    source=tmp_path/'machine-review.json';review=json.loads(source.read_text());review['evidence_sha256']=evidence_digest(evidence);source.write_text(json.dumps(review));decision['source_review']['sha256']=hashlib.sha256(source.read_bytes()).hexdigest();path.write_text(json.dumps(decision))
    with pytest.raises(ValueError):module().load_adjudication(path,story,evidence)


def test_adjudicated_validator_exception_is_saved_and_never_published(tmp_path):
    config,story,project,evidence,decision,path=setup(tmp_path);candidate=tmp_path/'candidate.json';candidate.write_text(json.dumps(story))
    cfg=json.loads(config.read_text());cfg.update(review_decision_path='decision.json',estimated_chars_per_second=0);config.write_text(json.dumps(cfg));out=tmp_path/'out'
    result=runner().run_project(config,out,Model([]),model_identity='test-model',candidate_path=candidate)
    assert result['status']=='needs_review'
    assert result['findings'][0]['code']=='validator_internal_error'
    assert (out/'attempts'/result['run_id']/'attempt-1.json').exists()
    assert not (out/'editorial_plan.json').exists()


def test_real_runner_semantic_record_is_eligible_and_transport_failure_is_not(tmp_path):
    config,story,project,evidence,decision,path=setup(tmp_path);candidate=tmp_path/'candidate.json';candidate.write_text(json.dumps(story));m=runner()
    results=[({'verdict':'revise','findings':[{'severity':'error','code':'evidence','path':'paragraphs[0]','message':'reviewed observation'}]},True), (RuntimeError('offline'),False)]
    for index,(response,eligible) in enumerate(results):
        result=m.run_project(config,tmp_path/f'out-{index}',Model([response]),model_identity='test-model',candidate_path=candidate)
        source=tmp_path/f'out-{index}'/'attempts'/result['run_id']/'attempt-1.json'
        decision['source_review']={'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()};path.write_text(json.dumps(decision))
        if eligible:
            loaded=module().load_adjudication(path,story,evidence)
            assert loaded['source_review']['review_completed'] is True
        else:
            with pytest.raises(ValueError):module().load_adjudication(path,story,evidence)
