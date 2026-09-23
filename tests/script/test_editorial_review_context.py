"""Review receives cited evidence without flooding a long chapter with unused ASR words."""
import json
from copy import deepcopy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"skills/video-script/scripts"))
from editorial_prompts import review_messages


def test_review_keeps_cited_word_correction_and_context_without_uncited_word_expansion():
    story={'paragraphs':[{'claims':[{'evidence_ids':['asr:1:word:0']}]}]}
    word={'text':'stop','start':5,'end':6,'evidence_id':'asr:1:word:0'}
    evidence={'source_id':'film','range':[0,1000],'clock':'movie_source','records':[
      {'id':'asr:1','kind':'dialogue','source_id':'film','start':5,'end':6,'text':'stop','words':[word], 'provenance':'/input/asr_result.json','speaker':'chunk0:1'},
      {'id':'asr:2','kind':'dialogue','source_id':'film','start':10,'end':1000,'text':'later talk','words':[{'text':'irrelevant','start':10+i*.1,'end':10.05+i*.1,'evidence_id':f'asr:2:word:{i}'} for i in range(1000)]},
      {'id':'note:correction','kind':'verified_note','source_id':'film','start':5,'end':6,'text':'speaker corrected','supersedes':['asr:1:word:0'], 'provenance':'/work/verified_notes.json'},
      {'id':'scene:1:observation','kind':'scene_observation','source_id':'film','start':0,'end':1000,'text':'story context'}],
      'fingerprints':{'huge metadata':'not needed'},'glossary_context_only':['unused lore'*1000]}
    before=deepcopy(evidence)
    context=json.loads(review_messages(story,{},evidence,{'profile':{}})[1]['content'])
    result=context['target_evidence'];index={x['id']:x for x in result['records']}
    assert 'asr:1:word:0' in index
    assert index['asr:1:word:0']['disputed_by']==['note:correction']
    assert index['note:correction']['text']=='speaker corrected'
    assert index['asr:1:word:0']['provenance']=='/input/asr_result.json'
    assert index['asr:1']['provenance']=='/input/asr_result.json'
    assert index['note:correction']['provenance']=='/work/verified_notes.json'
    assert index['asr:1:word:0']['speaker']=='chunk0:1'
    assert 'scene:1:observation' in index
    assert not any(x.get('words') for x in index.values())
    assert 'glossary_context_only' not in result
    assert len(json.dumps(result))<5000
    assert context['story']==story
    assert evidence==before


def test_cited_research_retains_source_and_observation_boundary():
    story={'paragraphs':[{'claims':[{'evidence_ids':['research:fact']}]}]}
    row={'id':'research:fact','kind':'research','source_id':'film','text':'basic identity','source_url':'https://example.org/film','start':0,'end':100,'confidence':'verified_external_fact'}
    context=json.loads(review_messages(story,{}, {'source_id':'film','records':[row]}, {'profile':{}})[1]['content'])
    assert context['target_evidence']['records'][0]['source_url']=='https://example.org/film'
    assert context['target_evidence']['records'][0]['kind']=='research'
