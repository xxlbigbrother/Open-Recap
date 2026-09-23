"""Derive execution and reading artifacts from one authoritative story object."""
from copy import deepcopy
import hashlib
import json


def story_fingerprint(story):
    return hashlib.sha256(json.dumps(story,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def project_editorial(story,project):
    source=project['source'];operations=[];narrations=[];guards=[]
    for p in story['paragraphs']:
        operations.extend(deepcopy(p['presentation']))
        guards.extend(deepcopy(p.get('protected_audio',[])))
        if p.get('narration'):
            narrations.append({**deepcopy(p['narration']),'paragraph_id':p['id'],'incoming_result':p['incoming_result'],'added_value':p['added_value']})
    return {'schema_version':1,'project_id':project['id'],'parent_story_sha256':story_fingerprint(story),
            'fps':project.get('fps',24),'handoff_guard':project.get('handoff_guard',.2),
            'sources':{source['id']:{k:source[k] for k in ('path','media_origin','media_duration')}},
            'operations':operations,'narrations':narrations,'protected_audio':guards}


def project_board(story):
    return {'schema_version':1,'parent_story_sha256':story_fingerprint(story),'items':[
        {'paragraph_id':p['id'],'chapter_id':p['chapter_id'],'incoming_result':p['incoming_result'],
         'change':p['change'],'added_value':p['added_value'],'handoff':deepcopy(p['handoff']),
         'operations':[x['id'] for x in p['presentation']],
         'claims':deepcopy(p['claims']),'audio_owner':'narration_and_original' if p.get('narration') else 'original'} for p in story['paragraphs']]}


def draft_markdown(story):
    lines=[f"# {story['viewer_promise']}",'',f"讲述角度：{story['selected_angle']}",'']
    for p in story['paragraphs']:
        lines += [f"## {p['id']} · {p['change']}",'']
        if p.get('narration'):lines += [p['narration']['text'],'']
        else:lines += ['（本段由电影原声与表演完成）','']
        lines += [f"视听交接：{p['handoff']['through_original']} → {p['handoff']['to_next']}",'']
    return '\n'.join(lines)
