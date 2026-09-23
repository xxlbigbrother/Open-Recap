"""Story integrity checks exercise evidence, continuity and executable projection."""
import copy
import importlib.util
from pathlib import Path
import sys
import pytest

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/video-script/scripts';sys.path.insert(0,str(SCRIPTS))


def modules():
    path=SCRIPTS/'editorial_contract.py';assert path.exists(),'editorial story contract missing'
    spec=importlib.util.spec_from_file_location('editorial_contract',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    path2=SCRIPTS/'editorial_projection.py';assert path2.exists(),'editorial projection missing'
    spec2=importlib.util.spec_from_file_location('editorial_projection',path2);p=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(p)
    return m,p


def fixture():
    project={'id':'film-B','source':{'id':'film-B','range':[50,75],'path':'source.mp4','media_origin':45,'media_duration':100},'fps':24}
    evidence={'records':[{'id':'frame:0:55','source_id':'film-B','kind':'frame_observation','start':55,'end':55,'text':'门锁转动'}]}
    style={'profile':{'id':'discovery','version':1},'cases':[{'id':'case1','status':'candidate'}]}
    story={'schema_version':1,'project_id':'film-B','source_id':'film-B','style':{'id':'discovery','version':1},'viewer_promise':'看懂门为什么打不开','selected_angle':'门锁的变化',
      'chapters':[{'id':'c1','title':'门打不开了','promise':'找出原因'}],
      'questions':[{'id':'q1','setup_paragraph':'p1','payoff_paragraph':'p2','status':'resolved'}],
      'paragraphs':[
      {'id':'p1','chapter_id':'c1','incoming_result':'他拿着钥匙走到门前','audience_knows':['他想进门'],'audience_question':'门锁有什么变化','change':'转动钥匙后发现异常','added_value':'引导注意门锁的方向',
       'claims':[{'id':'cl1','text':'门锁转动','kind':'observation','evidence_ids':['frame:0:55'],'certainty':'visible'}],
       'presentation':[{'id':'o1','type':'play','source_id':'film-B','source_start':50,'source_end':60,'audio':'original','purpose':'展示转动的门锁'}],
       'narration':{'id':'n1','text':'门锁转了，门却没开。','at_operation':'o1','offset':6,'allowed_operations':['o1'],'after_events':[{'operation_id':'o1','source_time':55}]},
       'protected_audio':[], 'handoff':{'from_result':'门没有打开','through_original':'他再拉一次门','to_next':'镜头给出门后的原因'},'questions_opened':['q1'],'questions_answered':[]},
      {'id':'p2','chapter_id':'c1','incoming_result':'钥匙转了门仍未打开','audience_knows':['门有阻碍'],'audience_question':'门后有什么','change':'镜头揭示阻碍','added_value':'让原片直接回答问题','claims':[],
       'presentation':[{'id':'o2','type':'play','source_id':'film-B','source_start':60,'source_end':70,'audio':'original','purpose':'展示门后及台词'}],
       'narration':None,'protected_audio':[{'operation_id':'o2','source_start':61,'source_end':65,'purpose':'完整原声回答'}],
       'handoff':{'from_result':'看清门后','through_original':'完整回答后停顿','to_next':'本章到此结束'},'questions_opened':[],'questions_answered':['q1']} ]}
    return project,evidence,style,story


def codes(story,project,evidence,style):return [x['code'] for x in modules()[0].validate_story(story,project,evidence,style)]


def test_valid_story_projects_one_authoritative_text_and_original_audio():
    project,evidence,style,story=fixture();m,p=modules();assert m.validate_story(story,project,evidence,style)==[]
    out=p.project_editorial(story,project)
    assert [o['id'] for o in out['operations']]==['o1','o2']
    assert out['narrations'][0]['text']=='门锁转了，门却没开。'
    assert out['narrations'][0]['offset']==6
    assert out['sources']['film-B']['media_origin']==45
    assert out['protected_audio'][0]['source_start']==61
    changed=copy.deepcopy(story);changed['paragraphs'][0]['narration']['text']='门为什么没开？'
    assert p.project_editorial(changed,project)['parent_story_sha256']!=out['parent_story_sha256']


@pytest.mark.parametrize('mutation,expected',[
 (lambda s:s['paragraphs'][0]['claims'][0].update(evidence_ids=['reference:case1']),'unknown_evidence'),
 (lambda s:s['paragraphs'][0]['claims'][0].update(kind='research'),'research_source_missing'),
 (lambda s:s['paragraphs'][0].update(incoming_result='见所属段落的前一事件'),'generic_continuity'),
 (lambda s:s['paragraphs'][0]['handoff'].update(to_next='承接前文'),'generic_continuity'),
 (lambda s:s['paragraphs'][0]['presentation'][0].update(type='slow_motion'),'unsupported_operation'),
 (lambda s:s['paragraphs'][0]['presentation'][0].update(source_end=90),'source_range'),
 (lambda s:s['paragraphs'][0]['narration'].update(at_operation='deleted'),'unknown_operation'),
 (lambda s:s['questions'][0].update(setup_paragraph='deleted'),'question_link'),
 (lambda s:s['paragraphs'][1].update(questions_answered=[]),'question_payoff'),
])
def test_invalid_story_returns_actionable_findings(mutation,expected):
    project,evidence,style,story=fixture();mutation(story)
    assert expected in codes(story,project,evidence,style)


def test_disputed_model_fact_cannot_be_used_as_unqualified_observation():
    project,evidence,style,story=fixture();evidence['records'][0]['disputed_by']=['note:correction']
    assert 'disputed_evidence' in codes(story,project,evidence,style)


def test_declared_question_cannot_payoff_before_setup():
    project,evidence,style,story=fixture();story['questions'][0].update(setup_paragraph='p2',payoff_paragraph='p1')
    assert 'question_order' in codes(story,project,evidence,style)


def test_original_audio_only_is_valid_but_empty_whole_story_is_not():
    project,evidence,style,story=fixture();story['paragraphs']=[]
    assert 'empty_story' in codes(story,project,evidence,style)
