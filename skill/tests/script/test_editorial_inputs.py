"""Input leakage/clock regressions: consume raw evidence without changing it."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/video-script/scripts'
sys.path.insert(0,str(SCRIPTS))


def module():
    path=SCRIPTS/'editorial_inputs.py'
    assert path.exists(), 'read-only editorial input capability not implemented'
    spec=importlib.util.spec_from_file_location('editorial_inputs',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def fixture(tmp_path, source_id='synthetic-film'):
    source=tmp_path/'source.mp4';source.write_bytes(b'local fixture media')
    understanding=tmp_path/'understanding';understanding.mkdir()
    (understanding/'vlm_analysis.json').write_text(json.dumps([{'scene_id':0,'start':50,'end':70,'description':'人物离开房间','depth_analysis':'他可能感到害怕','frame_facts':{'55':['手握门把'],'65':['关上房门']}}],ensure_ascii=False))
    (understanding/'asr_result.json').write_text(json.dumps([{'start':55,'end':66,'text':'走吧','speaker':'speaker1','words':[{'start':55,'end':56,'text':'走'},{'start':65,'end':66,'text':'吧'}]}],ensure_ascii=False))
    (understanding/'understanding_index.json').write_text('{"plot_points":[],"relationships":[]}')
    for name in ['vlm_analysis.json','asr_result.json']:
        artifact=understanding/name
        (understanding/(name+'.meta.json')).write_text(json.dumps({'source_video_fingerprint':hashlib.sha256(source.read_bytes()).hexdigest(),'source_id':source_id,'clock':'movie_source','artifact_fingerprint':hashlib.sha256(artifact.read_bytes()).hexdigest()}))
    config={'schema_version':1,'id':'test','source':{'id':source_id,'path':'source.mp4','media_origin':45,'media_duration':100,'range':[54,60]},'understanding_dir':'understanding','style_path':'style.json','audience':'new_viewer','reveal_policy':'progressive'}
    path=tmp_path/'project.json';path.write_text(json.dumps(config));return path,config


@pytest.mark.parametrize('source_id',['synthetic-film','other-film'])
def test_words_and_frames_are_scoped_without_requiring_plot_index(tmp_path,source_id):
    path,_=fixture(tmp_path,source_id)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'understanding').iterdir()}
    m=module();bundle=m.build_evidence(m.load_project(path))
    assert bundle['source_id']==source_id
    assert [r['text'] for r in bundle['records'] if r['kind']=='dialogue']==['走']
    assert [r['text'] for r in bundle['records'] if r['kind']=='frame_observation']==['手握门把']
    assert all(54<=r['start']<=r['end']<=60 for r in bundle['records'])
    assert any(r['kind']=='scene_observation' for r in bundle['records'])
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'understanding').iterdir()}


@pytest.mark.parametrize('source_range',[[60,54],[0,60],[54,float('nan')],[54,146]])
def test_invalid_or_incompatible_source_range_rejected(tmp_path,source_range):
    path,config=fixture(tmp_path);config['source']['range']=source_range;path.write_text(json.dumps(config))
    with pytest.raises(ValueError):module().load_project(path)


def test_verified_note_can_flag_conflict_without_erasing_original(tmp_path):
    path,config=fixture(tmp_path);config['verified_notes']='notes.json';path.write_text(json.dumps(config))
    note={'schema_version':1,'source_id':'synthetic-film','clock':'movie_source','notes':[{'id':'door','start':55,'end':56,'text':'他握门把，并未关门','evidence':'人工查看55秒画面','supersedes':['scene:0:observation']} ]}
    (tmp_path/'notes.json').write_text(json.dumps(note,ensure_ascii=False))
    m=module();bundle=m.build_evidence(m.load_project(path));by={r['id']:r for r in bundle['records']}
    assert by['scene:0:observation']['disputed_by']==['note:door']
    assert by['note:door']['kind']=='verified_note'
    assert by['scene:0:observation']['text']=='人物离开房间'


def test_wrong_source_notes_cannot_contaminate_current_movie(tmp_path):
    path,config=fixture(tmp_path);config['verified_notes']='notes.json';path.write_text(json.dumps(config))
    (tmp_path/'notes.json').write_text(json.dumps({'schema_version':1,'source_id':'different-film','clock':'movie_source','notes':[]}))
    m=module()
    with pytest.raises(ValueError,match='source'):m.build_evidence(m.load_project(path))


def test_wrong_understanding_media_fingerprint_is_rejected(tmp_path):
    path,_=fixture(tmp_path);p=tmp_path/'understanding/vlm_analysis.json.meta.json';d=json.loads(p.read_text());d['source_video_fingerprint']='other-film';p.write_text(json.dumps(d))
    m=module()
    with pytest.raises(ValueError,match='identity'):m.build_evidence(m.load_project(path))


def test_understanding_clock_must_match_movie_source(tmp_path):
    path,_=fixture(tmp_path);p=tmp_path/'understanding/asr_result.json.meta.json';d=json.loads(p.read_text());d['clock']='reference_video';p.write_text(json.dumps(d))
    m=module()
    with pytest.raises(ValueError,match='clock'):m.build_evidence(m.load_project(path))


def test_valid_explicit_proxy_mapping_can_bind_distinct_source_bytes(tmp_path):
    path,cfg=fixture(tmp_path);actual=hashlib.sha256((tmp_path/'source.mp4').read_bytes()).hexdigest()
    metadata={'source_fingerprints':{'movie-analysis.mp4':'analysis-hash'},'edited_source_fingerprint':actual}
    (tmp_path/'proxy.meta.json').write_text(json.dumps(metadata));(tmp_path/'proxy.render.json').write_text(json.dumps({'clips':[{'source_path':'movie-analysis.mp4','source_start':45,'source_end':145,'output_start':0,'output_end':100}]}))
    for name in ['vlm_analysis.json','asr_result.json']:
        p=tmp_path/'understanding'/(name+'.meta.json');d=json.loads(p.read_text());d['source_video_fingerprint']='analysis-hash';p.write_text(json.dumps(d))
    cfg['source_binding']={'proxy_meta':'proxy.meta.json','render_manifest':'proxy.render.json'};path.write_text(json.dumps(cfg))
    m=module();e=m.build_evidence(m.load_project(path));assert e['source_identity']['status']=='verified_proxy_mapping'


def test_proxy_cannot_bind_wrong_clip_by_merely_listing_understanding_hash(tmp_path):
    path,cfg=fixture(tmp_path);actual=hashlib.sha256((tmp_path/'source.mp4').read_bytes()).hexdigest()
    (tmp_path/'proxy.meta.json').write_text(json.dumps({'source_fingerprints':{'other.mp4':'other-hash','analysis.mp4':'analysis-hash'},'edited_source_fingerprint':actual}))
    (tmp_path/'proxy.render.json').write_text(json.dumps({'clips':[{'source_path':'other.mp4','source_start':45,'source_end':145,'output_start':0,'output_end':100}]}))
    for name in ['vlm_analysis.json','asr_result.json']:
        p=tmp_path/'understanding'/(name+'.meta.json');d=json.loads(p.read_text());d['source_video_fingerprint']='analysis-hash';p.write_text(json.dumps(d))
    cfg['source_binding']={'proxy_meta':'proxy.meta.json','render_manifest':'proxy.render.json'};path.write_text(json.dumps(cfg))
    m=module()
    with pytest.raises(ValueError,match='clip source'):m.build_evidence(m.load_project(path))


def test_english_dialogue_preserves_lexical_word_boundaries(tmp_path):
    path,cfg=fixture(tmp_path);cfg['source']['range']=[54,60];path.write_text(json.dumps(cfg))
    p=tmp_path/'understanding/asr_result.json'
    words=[{'start':55,'end':55.2,'text':'Stay'},{'start':-.001,'end':-.001,'text':' '},{'start':55.3,'end':55.6,'text':'here'},{'start':-.001,'end':-.001,'text':' '}]
    p.write_text(json.dumps([{'start':55,'end':55.6,'text':'Stay here.','words':words}]))
    meta=p.with_name(p.name+'.meta.json');d=json.loads(meta.read_text());d['artifact_fingerprint']=hashlib.sha256(p.read_bytes()).hexdigest();meta.write_text(json.dumps(d))
    m=module();e=m.build_evidence(m.load_project(path))
    assert [r['text'] for r in e['records'] if r['kind']=='dialogue']==['Stay here']
    assert [w['text'] for r in e['records'] if r['kind']=='dialogue' for w in r['words']]==['Stay','here']
