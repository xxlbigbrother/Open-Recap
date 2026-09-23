"""Read existing film evidence without modifying or regenerating understanding."""
from copy import deepcopy
import hashlib
import json
import math
import re
from pathlib import Path


def fingerprint(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for data in iter(lambda:handle.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def finite(value,label):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):
        raise ValueError(f'{label}: expected finite number')
    return float(value)


def read_json(path):
    with Path(path).open(encoding='utf-8') as f:return json.load(f)


def index_evidence(bundle):
    """One citation namespace for observations, utterances, words and corrections."""
    indexed={r['id']:deepcopy(r) for r in bundle['records']}
    for parent in bundle['records']:
        for word in parent.get('words',[]):
            if word.get('evidence_id'):
                indexed[word['evidence_id']]={**deepcopy(parent),**deepcopy(word),
                    'id':word['evidence_id'],'parent_id':parent['id'],'kind':'dialogue'}
    for row in bundle['records']:
        for target_id in row.get('supersedes',[]):
            target=indexed.get(target_id)
            if target is None:continue
            targets=[target]
            if target.get('parent_id'):
                targets.append(indexed[target['parent_id']])
            else:
                targets.extend(r for r in indexed.values() if r.get('parent_id')==target_id)
            for item in targets:
                notes=item.setdefault('disputed_by',[])
                if row['id'] not in notes:notes.append(row['id'])
    return indexed


def load_project(path):
    path=Path(path).resolve();project=deepcopy(read_json(path))
    if project.get('schema_version')!=1:raise ValueError('project schema_version must be 1')
    if not isinstance(project.get('id'),str) or not project['id'].strip():raise ValueError('project id required')
    source=project['source']
    if not isinstance(source.get('id'),str) or not source['id']:raise ValueError('source id required')
    origin=finite(source.get('media_origin',0),'media_origin');duration=finite(source['media_duration'],'media_duration')
    a,b=[finite(x,'source range') for x in source['range']]
    if origin<0 or duration<=0 or not origin<=a<b<=origin+duration:raise ValueError('source range outside media clock')
    source.update(media_origin=origin,media_duration=duration,range=[a,b])
    source['path']=str((path.parent/source['path']).resolve())
    if not Path(source['path']).is_file():raise ValueError('source media missing')
    for key in ('understanding_dir','style_path','verified_notes','research_path','authoring_draft_path','development_path','review_decision_path'):
        if project.get(key):project[key]=str((path.parent/project[key]).resolve())
    for key,value in project.get('source_binding',{}).items():
        if key in {'proxy_meta','render_manifest','analysis_source_map'}:project['source_binding'][key]=str((path.parent/value).resolve())
    if not Path(project['understanding_dir']).is_dir():raise ValueError('understanding_dir missing')
    if project.get('understanding_clock','movie_source')!='movie_source':raise ValueError('understanding clock must be movie_source')
    policy=project.setdefault('reveal_policy','progressive')
    if policy not in {'progressive','preview_allowed'}:raise ValueError('unknown reveal_policy')
    project.setdefault('audience','没看过原片也能跟上，并获得额外理解')
    project['project_path']=str(path)
    return project


def verify_source_identity(project):
    source=project['source'];work=Path(project['understanding_dir']);actual=fingerprint(source['path']);hashes={};metadata=[]
    for name in ['vlm_analysis.json','asr_result.json']:
        p=work/(name+'.meta.json')
        if not p.exists():raise ValueError(f'source identity metadata missing: {p.name}')
        data=read_json(p);hashes[str(p)]=fingerprint(p)
        if data.get('clock',data.get('timeline','movie_source'))!='movie_source':raise ValueError('understanding metadata clock mismatch')
        if data.get('source_id',source['id'])!=source['id']:raise ValueError('understanding source identity mismatch')
        if not data.get('source_video_fingerprint'):raise ValueError('understanding source identity fingerprint missing')
        artifact=work/name
        if data.get('artifact_fingerprint') and data['artifact_fingerprint']!=fingerprint(artifact):raise ValueError('understanding artifact identity changed')
        metadata.append(data)
    fingerprints={d['source_video_fingerprint'] for d in metadata}
    if len(fingerprints)!=1:raise ValueError('ASR/VLM source identity differs')
    origin_fingerprint=next(iter(fingerprints))
    if origin_fingerprint==actual:return {'status':'verified_direct','source_sha256':actual,'understanding_source_sha256':origin_fingerprint,'metadata_fingerprints':hashes}
    binding=project.get('source_binding',{})
    if not binding.get('proxy_meta') or not binding.get('render_manifest'):raise ValueError('source identity mismatch: explicit proxy mapping required')
    p=Path(binding['proxy_meta']);proxy=read_json(p);hashes[str(p)]=fingerprint(p)
    p=Path(binding['render_manifest']);render=read_json(p);hashes[str(p)]=fingerprint(p)
    if proxy.get('edited_source_fingerprint')!=actual or origin_fingerprint not in proxy.get('source_fingerprints',{}).values():
        raise ValueError('proxy source identity mismatch')
    clips=render.get('clips',[])
    if len(clips)!=1:raise ValueError('proxy mapping must be one continuous source interval')
    c=clips[0];origin=source['media_origin'];duration=source['media_duration']
    clip_source=c.get('source_path') or render.get('source_map',{}).get('original_source')
    if not isinstance(clip_source,str) or clip_source not in proxy['source_fingerprints']:raise ValueError('proxy clip source identity missing')
    if proxy['source_fingerprints'][clip_source]!=origin_fingerprint:
        if not binding.get('analysis_source_map'):raise ValueError('proxy clip source differs from understanding')
        p=Path(binding['analysis_source_map']);source_map=read_json(p);hashes[str(p)]=fingerprint(p)
        analysis=Path(source_map['analysis_input'])
        if source_map.get('original_source')!=clip_source or not analysis.is_file() or fingerprint(analysis)!=origin_fingerprint:
            raise ValueError('proxy clip source analysis mapping mismatch')
        if finite(source_map.get('time_offset'),'analysis time_offset')!=0:raise ValueError('nonzero analysis clock unsupported')
        render_map=render.get('source_map',{})
        if render_map.get('original_audio_stream')!=source_map.get('original_audio_stream'):raise ValueError('proxy clip source audio mismatch')
        hashes[str(analysis)]=origin_fingerprint
    if any(abs(finite(value,'proxy mapping time')-expected)>.01 for value,expected in [(c['source_start'],origin),(c['source_end'],origin+duration),(c['output_start'],0),(c['output_end'],duration)]):
        raise ValueError('proxy source clock mapping mismatch')
    return {'status':'verified_proxy_mapping','source_sha256':actual,'understanding_source_sha256':origin_fingerprint,'metadata_fingerprints':hashes}


def build_evidence(project):
    source=project['source'];left,right=source['range'];work=Path(project['understanding_dir'])
    identity=verify_source_identity(project)
    records=[];hashes=dict(identity['metadata_fingerprints']);paths={}
    def load(name,required=False):
        p=work/name
        if not p.exists():
            if required:raise ValueError(f'missing understanding artifact {name}')
            return None
        hashes[str(p)]=fingerprint(p);paths[name]=str(p);return read_json(p)
    def add(ident,start,end,kind,text,path,**extra):
        start,end=finite(start,'evidence start'),finite(end,'evidence end')
        if end<start:raise ValueError(f'{ident}: invalid evidence interval')
        if start>=right or end<left or (end==left and start!=end):return
        if not str(text).strip():return
        records.append({'id':ident,'source_id':source['id'],'clock':'movie_source',
            'start':max(start,left),'end':min(end,right),'kind':kind,'text':str(text),
            'provenance':path,**extra})
    scenes=load('vlm_analysis.json',required=True)
    if not isinstance(scenes,list):raise ValueError('vlm_analysis.json must be a scene list')
    for index,scene in enumerate(scenes):
        a,b=scene['start'],scene['end']
        if b<=left or a>=right:continue
        add(f'scene:{index}:observation',a,b,'scene_observation',scene.get('description',''),paths['vlm_analysis.json'],
            source_interval=[a,b],confidence='model_observation_not_verified')
        add(f'scene:{index}:interpretation',a,b,'model_interpretation',scene.get('depth_analysis',''),paths['vlm_analysis.json'],
            source_interval=[a,b],confidence='hypothesis_only')
        for time,facts in scene.get('frame_facts',{}).items():
            when=float(time)
            if left<=when<right:
                add(f'frame:{index}:{time}',when,when,'frame_observation','；'.join(facts),paths['vlm_analysis.json'],confidence='model_observation_not_verified')
    speech=load('asr_result.json',required=True)
    if not isinstance(speech,list):raise ValueError('asr_result.json must be an utterance list')
    for index,row in enumerate(speech):
        if row['end']<=left or row['start']>=right:continue
        words=[]
        for word_index,w in enumerate(row.get('words',[])):
            if not str(w.get('text','')).strip():continue
            a,b=finite(w['start'],'word start'),finite(w['end'],'word end')
            if b<a:raise ValueError('invalid ASR word timestamp')
            if left<=a and b<=right:words.append({**w,'evidence_id':f'asr:{index}:word:{word_index}'})
        if row.get('words'):
            text=''
            for word in words:
                token=word['text'].strip()
                separator=' ' if text and re.search(r'[A-Za-z0-9]$',text) and re.match(r'[A-Za-z0-9]',token) else ''
                text+=separator+token
            if words:add(f'asr:{index}',words[0]['start'],words[-1]['end'],'dialogue',text,paths['asr_result.json'],words=words,speaker=row.get('speaker'),confidence='asr_not_identity_verified')
        elif left<=row['start'] and row['end']<=right:
            add(f'asr:{index}',row['start'],row['end'],'dialogue',row['text'],paths['asr_result.json'],words=[],speaker=row.get('speaker'),confidence='utterance_only')
    index=load('understanding_index.json') or {}
    glossary=index.get('research_glossary',[])
    if project.get('verified_notes'):
        p=Path(project['verified_notes']);notes=read_json(p);hashes[str(p)]=fingerprint(p)
        if notes.get('source_id')!=source['id'] or notes.get('clock')!='movie_source':raise ValueError('verified notes source/clock mismatch')
        seen=set()
        for note in notes.get('notes',[]):
            if note['id'] in seen:raise ValueError('duplicate verified note id')
            seen.add(note['id']);a,b=finite(note['start'],'note.start'),finite(note['end'],'note.end')
            if not source['media_origin']<=a<=b<=source['media_origin']+source['media_duration']:raise ValueError('verified note outside source')
            if not note.get('evidence','').strip():raise ValueError('verified note requires actual observation provenance')
            add('note:'+note['id'],a,b,'verified_note',note['text'],str(p),evidence_note=note['evidence'],supersedes=note.get('supersedes',[]),confidence='human_or_agent_reviewed')
    if project.get('research_path'):
        p=Path(project['research_path']);data=read_json(p);hashes[str(p)]=fingerprint(p)
        if data.get('source_id')!=source['id']:raise ValueError('research source mismatch')
        for fact in data.get('facts',[]):
            if fact.get('verified') is True and fact.get('source_url'):
                add('research:'+fact['id'],left,right,'research',fact['claim'],str(p),source_url=fact['source_url'],confidence='verified_external_fact')
    by_id=index_evidence({'records':records})
    for row in records:
        if by_id[row['id']].get('disputed_by'):row['disputed_by']=by_id[row['id']]['disputed_by']
        for word in row.get('words',[]):
            entry=by_id[word['evidence_id']]
            if entry.get('disputed_by'):word['disputed_by']=entry['disputed_by']
    return {'schema_version':1,'source_id':source['id'],'clock':'movie_source','range':[left,right],
            'records':records,'glossary_context_only':glossary,'fingerprints':hashes,
            'source_fingerprint':identity['source_sha256'],'source_identity':identity,
            'warnings':['Scene summaries may cover more than the requested interval; exact statements require frame/dialogue/verified-note anchors.',
                        'Model interpretations and speaker IDs are not verified facts. Reference cases are not target-film evidence.']}
