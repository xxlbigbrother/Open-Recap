import importlib.util
import json
from pathlib import Path
import pytest

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/video-script/scripts'


def module():
    path=SCRIPTS/'editorial_catalog.py';assert path.exists(),'versioned reference catalog not implemented'
    spec=importlib.util.spec_from_file_location('editorial_catalog',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def bundle(tmp_path):
    cases=[]
    for ident,status in [('positive','user_approved_local'),('negative','needs_revision'),('old','retired')]:
        feedback=[{'origin':'user','scope':{'version':'v1','range':[1,8]},'judgment':'liked this passage'}] if status=='user_approved_local' else []
        cases.append({'id':ident,'title':ident,'status':status,'source':{'reference_id':'reference-A','reference_range':[1,8],'source_clock':'reference_video','source_evidence_ids':['E1']},'when_use':['出现需要比较的变化'],'added_value':'让观众看清差异','evidence_requirements':['前后可见证据'],'sequence':['前提','证据','返回'],'return_to_story':'人物接着做决定','when_not_use':['重复已知信息'],'feedback':feedback,'transfer_rule':'依据问题选择呈现'})
    (tmp_path/'cases.json').write_text(json.dumps({'schema_version':1,'id':'cases','version':1,'evidence_index':{'E1':{'reference_id':'reference-A','clock':'reference_video','range':[1,8]}},'cases':cases}))
    p={'schema_version':1,'id':'style','version':1,'title':'重看','audience':'新观众','viewer_promise':'看懂故事并有发现','voice_principles':['自然'],'knowledge_preferences':['当下需要'],'original_audio_principles':['保护表演'],'technique_selection':['比较价值'],'case_files':['cases.json'],'limits':['单片观察']}
    (tmp_path/'style.json').write_text(json.dumps(p));return tmp_path/'style.json'


def test_negative_examples_retained_but_retired_cases_not_active(tmp_path):
    path=bundle(tmp_path);data=module().load_style(path)
    assert [c['id'] for c in data['cases']]==['positive','negative']
    assert data['cases'][1]['status']=='needs_revision'
    assert len(data['fingerprints'])==2


def test_duplicate_case_identity_is_rejected(tmp_path):
    path=bundle(tmp_path);p=tmp_path/'cases.json';data=json.loads(p.read_text());data['cases'][1]['id']='positive';p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='duplicate'):module().load_style(path)


def test_missing_context_is_not_silently_adopted(tmp_path):
    path=bundle(tmp_path);p=tmp_path/'cases.json';data=json.loads(p.read_text());del data['cases'][0]['when_not_use'];p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='when_not_use'):module().load_style(path)


@pytest.mark.parametrize('change',[
 lambda d:d.update(version=-1),
 lambda d:d['cases'][0]['source'].update(reference_range=[10,1]),
 lambda d:d['cases'][0]['source'].update(source_clock='invalid'),
 lambda d:d['cases'][0]['source'].update(source_evidence_ids=['MISSING']),
 lambda d:d['cases'][0].update(feedback=[]),
])
def test_invalid_provenance_or_unbacked_user_approval_rejected(tmp_path,change):
    path=bundle(tmp_path);p=tmp_path/'cases.json';d=json.loads(p.read_text());change(d);p.write_text(json.dumps(d))
    with pytest.raises(ValueError):module().load_style(path)
