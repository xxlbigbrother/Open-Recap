"""Load a local, versioned style profile and complete positive/counterexamples."""
import hashlib
import json
import math
from pathlib import Path


def load_style(path):
    path=Path(path).resolve();profile=json.loads(path.read_text());hashes={str(path):hashlib.sha256(path.read_bytes()).hexdigest()}
    for key in ['id','version','title','audience','viewer_promise','voice_principles','knowledge_preferences','original_audio_principles','technique_selection','case_files','limits']:
        if not profile.get(key):raise ValueError(f'style requires {key}')
    if profile.get('schema_version')!=1 or type(profile['version'])!=int or profile['version']<1:raise ValueError('invalid style version')
    cases=[];ids=set()
    for name in profile['case_files']:
        p=(path.parent/name).resolve();bundle=json.loads(p.read_text());hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
        if bundle.get('schema_version')!=1 or type(bundle.get('version'))!=int or bundle['version']<1:raise ValueError('invalid case bundle version')
        evidence_index=bundle.get('evidence_index',{})
        for case in bundle['cases']:
            for key in ['id','title','status','source','when_use','added_value','evidence_requirements','sequence','return_to_story','when_not_use','transfer_rule']:
                if not case.get(key):raise ValueError(f'case requires {key}')
            if case['id'] in ids:raise ValueError(f'duplicate case id {case["id"]}')
            ids.add(case['id'])
            if case['status'] not in {'candidate','user_approved_local','needs_revision','retired'}:raise ValueError('unknown case status')
            if not case['source'].get('reference_id') or not case['source'].get('source_clock'):raise ValueError('case source provenance required')
            origin=case['source']
            if origin['source_clock'] not in {'reference_video','movie_source','local_output','local_v2_output'}:raise ValueError('invalid case source clock')
            interval=origin.get('reference_range')
            if not isinstance(interval,list) or len(interval)!=2 or not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in interval) or not 0<=interval[0]<interval[1]:raise ValueError('invalid case source range')
            refs=origin.get('source_evidence_ids')
            if not isinstance(refs,list) or not refs:raise ValueError('case evidence refs required')
            for ref in refs:
                if ref not in evidence_index:raise ValueError('unresolved case evidence '+str(ref))
                entry=evidence_index[ref]
                if entry.get('reference_id')!=origin['reference_id'] or entry.get('clock')!=origin['source_clock']:raise ValueError('case evidence identity mismatch')
                if entry.get('range')!=interval:raise ValueError('case evidence range mismatch')
            feedback=case.get('feedback')
            if not isinstance(feedback,list):raise ValueError('case feedback must be list')
            if case['status']=='user_approved_local' and not any(isinstance(f,dict) and f.get('origin') in {'user','user_feedback'} and isinstance(f.get('scope'),dict) and f['scope'].get('version',f['scope'].get('artifact')) and f['scope'].get('range',f['scope'].get('range_seconds')) and f.get('judgment') for f in feedback):
                raise ValueError('local user approval requires version/range feedback')
            if case['status']!='retired':cases.append(case)
    return {'profile':profile,'cases':cases,'fingerprints':hashes}
