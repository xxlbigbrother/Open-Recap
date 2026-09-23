"""Development packets bind chosen editorial observations to current evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import pytest
from test_editorial_runner import real_config,Model,module as runner_module
from editorial_inputs import load_project,build_evidence

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/video-script/scripts'


def module():
    path=SCRIPTS/'editorial_development.py'
    assert path.exists(),'development packet behavior is not implemented'
    spec=importlib.util.spec_from_file_location('editorial_development',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def idea():
    return {'id':'door','observation':'门锁转动，人物仍留在门外','evidence_ids':['frame:0:55'],
            'interpretation':'声音与门的位置让开门期待暂时落空','viewer_gain':'理解动作完成与目标完成的区别',
            'missing_premises':[],'editing_checks':[{'action':'编排时核对拉门动作完整结束'}],
            'status':'ready','return_to_story':'回到他再次拉门的动作','presentation_intent':'正常播放门锁及反应'}


def packet(tmp_path):
    config,story=real_config(tmp_path);project=load_project(config);evidence=build_evidence(project)
    result=module().bind_development({'candidates':[idea()], 'selected_id':'door'},project,evidence)
    return config,story,project,evidence,result


def test_selected_fact_can_be_ready_while_editing_checks_remain(tmp_path):
    _,_,project,evidence,data=packet(tmp_path);m=module()
    assert m.validate_development(data,project,evidence)==[]
    assert m.selected_context(data)['editing_checks']==[{'action':'编排时核对拉门动作完整结束'}]


@pytest.mark.parametrize('mutation,code',[
 (lambda d:d['candidates'][0].update(evidence_ids=['reference:other-film']),'unknown_evidence'),
 (lambda d:d['candidates'][0].update(missing_premises=[{'claim':'门是谁锁上的','action':'核对前场'}]),'unresolved_premise'),
 (lambda d:d.update(selected_id='missing'),'selected_angle'),
 (lambda d:d.update(source_id='different-film'),'source_identity'),
 (lambda d:d['candidates'].append(copy.deepcopy(d['candidates'][0])),'duplicate_candidate'),
])
def test_invalid_selected_packet_returns_actionable_failure(tmp_path,mutation,code):
    _,_,project,evidence,data=packet(tmp_path);mutation(data)
    assert code in [x['code'] for x in module().validate_development(data,project,evidence)]


def test_stale_evidence_or_new_dispute_invalidates_development(tmp_path):
    _,_,project,evidence,data=packet(tmp_path)
    evidence['records'][0]['text']='corrected observation'
    assert 'stale_development' in [x['code'] for x in module().validate_development(data,project,evidence)]
    row=next(x for x in evidence['records'] if x['id']=='frame:0:55');row['disputed_by']=['note:correction']
    assert 'disputed_evidence' in [x['code'] for x in module().validate_development(data,project,evidence)]


def test_unselected_research_angle_is_kept_for_work_but_not_in_author_context(tmp_path):
    _,_,project,evidence,data=packet(tmp_path)
    other=idea();other.update(id='history',status='needs_research',missing_premises=[{'claim':'导演的经历','action':'查访谈'}])
    data['candidates'].append(other);m=module()
    assert m.validate_development(data,project,evidence)==[]
    selected=m.selected_context(data)
    assert selected['id']=='door'
    assert 'history' not in json.dumps(selected)
    assert len(data['candidates'])==2


def test_no_selected_angle_remains_a_research_result(tmp_path):
    _,_,project,evidence,data=packet(tmp_path);data['selected_id']=None;data['candidates'][0]['status']='needs_research'
    data['candidates'][0]['missing_premises']=[{'claim':'门是否上锁','action':'回查镜头'}]
    m=module();assert m.validate_development(data,project,evidence)==[]
    assert m.selected_context(data) is None


def test_runner_uses_selected_packet_and_caches_it_as_required_output(tmp_path):
    config,story,project,evidence,data=packet(tmp_path);path=tmp_path/'development.json';path.write_text(json.dumps(data))
    cfg=json.loads(config.read_text());cfg.update(development_path='development.json');config.write_text(json.dumps(cfg))
    calls=[];outputs=[story,{'verdict':'pass','findings':[]}]
    def provider(payload):
        calls.append(payload)
        return {'choices':[{'message':{'content':json.dumps(outputs.pop(0),ensure_ascii=False)}}]}
    out=tmp_path/'out';m=runner_module();result=m.run_project(config,out,provider,model_identity='test-model')
    assert result['status']=='ready_for_editorial_review'
    assert (out/'editorial_development.json').exists()
    assert '理解动作完成与目标完成的区别' in json.dumps(calls[0]['messages'],ensure_ascii=False)
    (out/'editorial_development.json').unlink()
    result=m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    assert result['cache']=='miss'
    assert (out/'editorial_development.json').exists()


def test_unresolved_packet_does_not_overwrite_accepted_story(tmp_path):
    config,story,project,evidence,data=packet(tmp_path);out=tmp_path/'out';m=runner_module()
    m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    before=(out/'recap_story_plan.json').read_bytes()
    data['candidates'][0]['missing_premises']=[{'claim':'unverified premise','action':'look up source'}]
    (tmp_path/'development.json').write_text(json.dumps(data));cfg=json.loads(config.read_text());cfg['development_path']='development.json';config.write_text(json.dumps(cfg))
    result=m.run_project(config,out,Model([]),model_identity='test-model')
    assert result['status']=='needs_review'
    assert result['findings'][0]['code']=='unresolved_premise'
    assert (out/'recap_story_plan.json').read_bytes()==before


def test_packet_replacement_after_read_cannot_bind_new_hash_to_old_content(tmp_path,monkeypatch):
    config,story,project,evidence,data=packet(tmp_path);path=tmp_path/'development.json'
    path.write_text(json.dumps(data));cfg=json.loads(config.read_text());cfg['development_path']='development.json';config.write_text(json.dumps(cfg))
    replacement=copy.deepcopy(data);replacement['candidates'][0]['viewer_gain']='新观察收获'
    m=runner_module();validate=m.validate_development
    def replace_after_read(*args):
        findings=validate(*args);path.write_text(json.dumps(replacement));return findings
    with monkeypatch.context() as patch:
        patch.setattr(m,'validate_development',replace_after_read)
        first=m.run_project(config,tmp_path/'out',Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    second=m.run_project(config,tmp_path/'out',Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    assert second['cache']=='miss'
    assert first['cache_key']!=second['cache_key']
    assert json.loads((tmp_path/'out/editorial_development.json').read_text())==replacement


def test_opt_out_with_same_workdir_retires_only_prior_published_packet(tmp_path):
    config,story,project,evidence,data=packet(tmp_path);path=tmp_path/'development.json';path.write_text(json.dumps(data))
    cfg=json.loads(config.read_text());cfg['development_path']='development.json';config.write_text(json.dumps(cfg));out=tmp_path/'out';m=runner_module()
    first=m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    cfg.pop('development_path');config.write_text(json.dumps(cfg))
    m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    assert not (out/'editorial_development.json').exists()
    assert (out/'attempts'/first['run_id']/'accepted/editorial_development.json').exists()
    assert path.exists()


def test_opt_out_archives_edited_prior_packet_without_losing_user_changes(tmp_path):
    config,story,project,evidence,data=packet(tmp_path);path=tmp_path/'development.json';path.write_text(json.dumps(data))
    cfg=json.loads(config.read_text());cfg['development_path']='development.json';config.write_text(json.dumps(cfg));out=tmp_path/'out';m=runner_module()
    m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    edited=copy.deepcopy(data);edited['review_note']='local user annotation'
    current=out/'editorial_development.json';current.write_text(json.dumps(edited));before=current.read_bytes()
    cfg.pop('development_path');config.write_text(json.dumps(cfg))
    result=m.run_project(config,out,Model([story,{'verdict':'pass','findings':[]}]),model_identity='test-model')
    assert not current.exists()
    assert (out/'attempts'/result['run_id']/'superseded/editorial_development.json').read_bytes()==before
