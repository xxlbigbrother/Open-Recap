"""Rhythm diagnostics expose edit facts without treating a narration ratio as a grade."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/video-script/scripts'
sys.path.insert(0,str(SCRIPTS))


def module():
    path=SCRIPTS/'editorial_rhythm.py'
    assert path.is_file(), 'rhythm diagnostics are missing'
    spec=importlib.util.spec_from_file_location('editorial_rhythm_test',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def timeline():
    return {'duration':100, 'sources':{'film':{'path':'movie.mp4'}}, 'operations':[
        {'id':'a','type':'play','source_id':'film','source_start':0,'source_end':40,'output_start':0,'output_end':40,'audio':'original','purpose':'表演'},
        {'id':'b','type':'replay','source_id':'film','source_start':20,'source_end':30,'output_start':40,'output_end':50,'audio':'mute','purpose':'证据'},
        {'id':'c','type':'play','source_id':'film','source_start':80,'source_end':130,'output_start':50,'output_end':100,'audio':'original','purpose':'后果'}],
        'narrations':[{'id':'n1','start':10,'end':20},{'id':'n2','start':15,'end':25},{'id':'n3','start':70,'end':75}],
        'protected_audio':[{'output_start':26,'output_end':39,'purpose':'一组完整表演'}]}


def test_actual_rhythm_unions_narration_and_distinguishes_audio_from_picture():
    c=timeline();before=deepcopy(c)
    result=module().summarize_compiled(c)
    assert result['narration_seconds']==20
    assert result['narration_occupancy']==.2
    assert result['without_narration_seconds']==80
    assert result['source_audio_enabled_seconds']==90  # May overlap narration; not a dialogue ratio.
    assert result['source_play_seconds']==90
    assert result['source_unique_play_seconds']==90
    assert result['replay_seconds']==10
    assert result['longest_without_narration_seconds']==45
    gap=result['without_narration_runs'][1]
    assert (gap['start'],gap['end'])==(25,70)
    assert gap['operation_ids']==['a','b','c']
    assert gap['protected_seconds']==13
    assert result['assessment']=='descriptive_only'
    assert c==before


def test_subdividing_one_long_performance_does_not_hide_narration_absence():
    c={'duration':80,'operations':[
        {'id':f'o{i}','type':'play','source_id':'f','source_start':i*20,'source_end':(i+1)*20,
         'output_start':i*20,'output_end':(i+1)*20,'audio':'original'} for i in range(4)],
        'narrations':[], 'protected_audio':[{'output_start':0,'output_end':80,'purpose':'完整笑点'}]}
    report=module().summarize_compiled(c)
    assert report['longest_without_narration_seconds']==80
    assert len(report['without_narration_runs'])==1
    assert report['without_narration_runs'][0]['protected_seconds']==80
    assert 'failed' not in report and 'passed' not in report


def test_review_outline_uses_array_order_estimates_and_real_chapter_edges():
    story={'chapters':[{'id':'s1','title':'院子'},{'id':'s2','title':'帮派据点'}], 'paragraphs':[
        {'id':'p02','chapter_id':'s1','change':'街坊击退来人','handoff':{'from_result':'来人退走','through_original':'收势','to_next':'帮派追究责任'},
         'presentation':[{'id':'a','type':'play','source_id':'f','source_start':0,'source_end':10,'audio':'original','purpose':'战斗结果'}],
         'narration':{'id':'n','text':'人退了。','at_operation':'a','offset':1}},
        {'id':'p19','chapter_id':'s2','incoming_result':'帮派把失利归到冒名者身上','change':'追责',
         'presentation':[{'id':'b','type':'play','source_id':'f','source_start':40,'source_end':60,'audio':'original','purpose':'追责现场'}],
         'narration':None, 'handoff':{'through_original':'处理命令'}}]}
    before=deepcopy(story)
    report=module().summarize_story(story,{'source':{'range':[0,100]},'fps':24,'estimated_chars_per_second':3})
    assert report['duration_basis']=='estimated_from_text_before_tts'
    assert report['duration_seconds']==30
    assert report['narration_seconds']==1
    assert report['source_unique_play_seconds']==30
    assert report['source_scope_seconds']==100
    assert report['chapters'][1]['chapter_id']=='s2'
    bridge=report['chapter_handoffs'][0]
    assert bridge['from_paragraph']=='p02' and bridge['to_paragraph']=='p19'
    assert bridge['output_time']==10
    assert bridge['next_narration'] is None
    assert bridge['outgoing_handoff']['to_next']=='帮派追究责任'
    assert bridge['next_incoming_result']=='帮派把失利归到冒名者身上'
    assert story==before


def test_semantic_review_gets_computed_context_not_caller_reported_metrics():
    from editorial_prompts import review_messages
    story={'chapters':[{'id':'c'}], 'paragraphs':[
        {'id':'p1','chapter_id':'c','claims':[], 'presentation':[{'id':'o','type':'play','source_id':'f','source_start':0,'source_end':60,'audio':'original'}], 'narration':None}]}
    project={'fps':24,'creative_brief':{'rhythm_report':{'narration_seconds':999}}}
    messages=review_messages(story,project,{'source_id':'f','records':[]},{'profile':{}})
    context=json.loads(messages[1]['content'])
    assert context['rhythm_overview']['narration_seconds']==0
    assert context['rhythm_overview']['longest_without_narration_seconds']==60


@pytest.mark.parametrize('bad',[float('nan'),-1,True])
def test_invalid_actual_duration_is_not_reported_as_a_valid_ratio(bad):
    c=timeline();c['duration']=bad
    with pytest.raises(ValueError):module().summarize_compiled(c)
