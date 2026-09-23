"""Structural editorial checks. These do not grade artistry or prove source truth."""
import math
import re
from editorial_inputs import index_evidence

GENERIC={'承接前文','承接上一段','见上一事件','见所属段落的前一事件','继续剧情','下一段','因果连接或可见证据解读'}


def shape_findings(story):
    errors=[]
    def bad(path,expected):errors.append({'severity':'error','code':'invalid_shape','path':path,'message':f'expected {expected}'})
    def text(parent,key,path,optional=False):
        if optional and parent.get(key) is None:return
        value=parent.get(key)
        if not isinstance(value,str) or not value.strip():bad(path+'.'+key,'nonempty string')
    def list_of(parent,key,path,item_type,required=True):
        if key not in parent and not required:return []
        value=parent.get(key)
        if not isinstance(value,list):bad(path+'.'+key,'list');return []
        for i,item in enumerate(value):
            if not isinstance(item,item_type):bad(f'{path}.{key}[{i}]',item_type.__name__)
        return [item for item in value if isinstance(item,item_type)]
    if not isinstance(story,dict):bad('$','object');return errors
    for i,c in enumerate(list_of(story,'chapters','$',dict)):text(c,'id',f'chapters[{i}]')
    for i,q in enumerate(list_of(story,'questions','$',dict)):
        for key in ['id','setup_paragraph','status']:text(q,key,f'questions[{i}]')
        text(q,'payoff_paragraph',f'questions[{i}]',True)
    for i,p in enumerate(list_of(story,'paragraphs','$',dict)):
        path=f'paragraphs[{i}]'
        for key in ['id','chapter_id']:text(p,key,path)
        for k in ['audience_knows','questions_opened','questions_answered']:list_of(p,k,path,str)
        for ci,claim in enumerate(list_of(p,'claims',path,dict)):
            cp=f'{path}.claims[{ci}]';list_of(claim,'evidence_ids',cp,str)
            for key in ['id','text','kind','certainty']:text(claim,key,cp)
        for oi,op in enumerate(list_of(p,'presentation',path,dict)):
            for key in ['id','source_id','type','audio']:text(op,key,f'{path}.presentation[{oi}]')
            text(op,'return_to',f'{path}.presentation[{oi}]',True)
        for gi,g in enumerate(list_of(p,'protected_audio',path,dict,False)):text(g,'operation_id',f'{path}.protected_audio[{gi}]')
        if not isinstance(p.get('handoff'),dict):bad(path+'.handoff','object')
        n=p.get('narration')
        if n is not None:
            if not isinstance(n,dict):bad(path+'.narration','object or null');continue
            for key in ['id','text','at_operation']:text(n,key,path+'.narration')
            list_of(n,'allowed_operations',path+'.narration',str)
            for ai,event in enumerate(list_of(n,'after_events',path+'.narration',dict,False)):
                if 'operation_id' in event:text(event,'operation_id',f'{path}.narration.after_events[{ai}]')
            for ci,cue in enumerate(list_of(n,'proof_cues',path+'.narration',dict,False)):
                list_of(cue,'operation_ids',f'{path}.narration.proof_cues[{ci}]',str)
    return errors


def validate_story(story,project,evidence,style):
    shapes=shape_findings(story)
    if shapes:return shapes
    findings=[]
    def fail(code,path,message):findings.append({'severity':'error','code':code,'path':path,'message':message})
    def specific(value,path):
        if not isinstance(value,str) or not value.strip():fail('missing_content',path,'需要具体内容')
        elif value.strip() in GENERIC:fail('generic_continuity',path,'写出实际人物、上一结果与下一期待')
    def numeric(value):return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)
    if not isinstance(story,dict):
        fail('invalid_story','$','story must be an object');return findings
    if story.get('schema_version')!=1:fail('schema_version','schema_version','expected 1')
    if story.get('project_id')!=project['id'] or story.get('source_id')!=project['source']['id']:fail('source_identity','$','project/source identity mismatch')
    profile=style['profile']
    if story.get('style')!={'id':profile['id'],'version':profile['version']}:fail('style_version','style','必须绑定指定风格版本')
    for key in ('viewer_promise','selected_angle'):specific(story.get(key),key)
    paragraphs=story.get('paragraphs')
    if not isinstance(paragraphs,list) or not paragraphs:
        fail('empty_story','paragraphs','需要完整讲述段');return findings
    chapters=story.get('chapters',[])
    if not isinstance(chapters,list):fail('chapter_link','chapters','expected list');chapters=[]
    chapter_ids={c.get('id') for c in chapters if isinstance(c,dict)}
    if not chapter_ids or None in chapter_ids or len(chapter_ids)!=len(chapters):fail('chapter_link','chapters','章节ID需要非空且唯一')
    records=index_evidence(evidence)
    source=project['source'];a,b=source['range'];operations={};paragraph_ids={};narration_ids=set();claim_ids=set()
    for i,p in enumerate(paragraphs):
        path=f'paragraphs[{i}]'
        if not isinstance(p,dict):fail('invalid_paragraph',path,'expected object');continue
        pid=p.get('id')
        if not isinstance(pid,str) or not pid or pid in paragraph_ids:fail('paragraph_id',path,'段落ID需要非空且唯一')
        else:paragraph_ids[pid]=i
        if p.get('chapter_id') not in chapter_ids:fail('chapter_link',path,'未知章节')
        for field in ('incoming_result','audience_question','change','added_value'):specific(p.get(field),path+'.'+field)
        if not isinstance(p.get('audience_knows'),list):fail('audience_state',path,'audience_knows需要列表')
        handoff=p.get('handoff',{})
        if not isinstance(handoff,dict):handoff={}
        for field in ('from_result','through_original','to_next'):specific(handoff.get(field),path+'.handoff.'+field)
        claims=p.get('claims',[])
        if not isinstance(claims,list):fail('invalid_claim',path+'.claims','expected list');claims=[]
        for ci,claim in enumerate(claims):
            cp=f'{path}.claims[{ci}]'
            if not isinstance(claim,dict):fail('invalid_claim',cp,'expected object');continue
            ident=claim.get('id')
            if not ident or ident in claim_ids:fail('claim_id',cp,'claim id必须唯一')
            claim_ids.add(ident)
            kind=claim.get('kind');refs=claim.get('evidence_ids',[])
            if kind not in {'observation','interpretation','research'}:fail('claim_kind',cp,'未知主张类别')
            if not isinstance(refs,list) or not refs:fail('unknown_evidence',cp,'每条主张需要当前影片证据');refs=[]
            matched=[]
            for ref in refs:
                if ref not in records:fail('unknown_evidence',cp,f'未知目标影片证据 {ref}');continue
                record=records[ref];matched.append(record)
                if record.get('source_id')!=source['id']:fail('source_identity',cp,'其他影片证据')
                if record.get('disputed_by'):fail('disputed_evidence',cp,f'{ref}存在已核验冲突，请读校正证据')
                if kind=='observation' and record.get('kind') in {'model_interpretation','context_only','research'}:
                    fail('observation_evidence',cp,'不能把模型解释或背景当画面事实')
            if kind=='research' and not any(r.get('kind')=='research' and r.get('source_url') for r in matched):
                fail('research_source_missing',cp,'外部知识必须有已核验外部来源')
            if kind=='interpretation' and claim.get('certainty') not in {'inference','hypothesis'}:
                fail('inference_certainty',cp,'解释必须标明 inference 或 hypothesis')
        presented=p.get('presentation',[])
        if not isinstance(presented,list) or not presented:fail('empty_presentation',path,'需要实际画面操作');presented=[]
        for oi,op in enumerate(presented):
            opath=f'{path}.presentation[{oi}]'
            if not isinstance(op,dict):fail('invalid_operation',opath,'expected object');continue
            ident=op.get('id')
            if not ident or ident in operations:fail('operation_id',opath,'操作ID必须唯一')
            else:operations[ident]=op
            if op.get('source_id')!=source['id']:fail('source_identity',opath,'操作源不匹配')
            kind=op.get('type')
            if kind not in {'play','replay','freeze'}:fail('unsupported_operation',opath,'仅支持play/replay/freeze')
            allowed_keys={'id','type','source_id','source_start','source_end','duration','audio','purpose','return_to','metadata'}
            if set(op)-allowed_keys:fail('unsupported_operation',opath,'未知执行字段: '+', '.join(sorted(set(op)-allowed_keys)))
            start=op.get('source_start');end=op.get('source_end',start)
            if not numeric(start) or not numeric(end) or not a<=start<=end<=b or start>=b:
                fail('source_range',opath,'操作超出项目源范围')
            elif kind!='freeze' and end<=start:fail('source_range',opath,'空播放区间')
            elif kind!='freeze' and end-start<1/project.get('fps',24):fail('frame_duration',opath,'播放区间短于一帧')
            if kind=='freeze' and (not numeric(op.get('duration')) or op['duration']<=0):fail('freeze_duration',opath,'定格需要有限正时长')
            elif kind=='freeze' and op['duration']<1/project.get('fps',24):fail('frame_duration',opath,'定格短于一帧')
            if op.get('audio') not in {'original','mute'}:fail('audio_policy',opath,'明确音频策略')
            if kind in {'replay','freeze'} and op.get('audio')!='mute':fail('audio_policy',opath,'回放/定格原声必须静音')
            specific(op.get('purpose'),opath+'.purpose')
        n=p.get('narration')
        if n is not None:
            if not isinstance(n,dict):fail('invalid_narration',path,'narration需要对象或null');continue
            allowed_narration={'id','text','at_operation','offset','allowed_operations','after_events','proof_cues','metadata'}
            if set(n)-allowed_narration:fail('unsupported_narration_field',path+'.narration','未知旁白执行字段: '+', '.join(sorted(set(n)-allowed_narration)))
            if not n.get('id') or n['id'] in narration_ids:fail('narration_id',path,'旁白ID必须唯一')
            narration_ids.add(n.get('id'))
            specific(n.get('text'),path+'.narration.text')
            if isinstance(n.get('text'),str) and re.search(r'\[[^\]]+\]',n['text']):fail('stage_direction_in_speech',path,'制作备注不得进入朗读文本')
            if not numeric(n.get('offset',0)) or n.get('offset',0)<0:fail('narration_offset',path,'offset必须非负')
    for i,p in enumerate(paragraphs):
        if not isinstance(p,dict):continue
        n=p.get('narration');path=f'paragraphs[{i}]'
        if isinstance(n,dict):
            allowed=n.get('allowed_operations')
            if not isinstance(allowed,list) or not allowed:
                fail('narration_window',path,'旁白必须指定非空有效画面窗口')
            elif n.get('at_operation') not in allowed:fail('narration_window',path,'开始锚点必须包含在allowed_operations中')
            anchor=operations.get(n.get('at_operation'))
            if anchor and numeric(n.get('offset',0)):
                span=anchor.get('duration') if anchor.get('type')=='freeze' else ((anchor.get('source_end')-anchor.get('source_start')) if numeric(anchor.get('source_end')) and numeric(anchor.get('source_start')) else None)
                if numeric(span) and n.get('offset',0)>=span:fail('narration_window',path,'offset超出开始操作')
            refs=[n.get('at_operation')]+n.get('allowed_operations',[])
            refs += [x.get('operation_id') for x in n.get('after_events',[])]
            refs += [k for x in n.get('proof_cues',[]) for k in x.get('operation_ids',[])]
            for ref in refs:
                if ref not in operations:fail('unknown_operation',path,f'未知旁白锚点 {ref}')
            for event in n.get('after_events',[]):
                if not event.get('operation_id') or not numeric(event.get('source_time')):
                    fail('event_anchor_shape',path+'.narration.after_events','每项必须为 {operation_id: 已存在操作ID, source_time: 源秒数}')
            for cue in n.get('proof_cues',[]):
                if not numeric(cue.get('audio_start')) or not numeric(cue.get('audio_end')) or not 0<=cue['audio_start']<cue['audio_end']:
                    fail('proof_cue_shape',path+'.narration.proof_cues','需要0<=audio_start<audio_end，单位为相对音频秒数')
                if not cue.get('operation_ids'):fail('proof_cue_shape',path+'.narration.proof_cues','举证需要非空operation_ids')
        for op in p.get('presentation',[]):
            if isinstance(op,dict) and op.get('type')=='replay' and op.get('return_to') not in operations:
                fail('unknown_operation',path,'回放必须有有效返回操作')
        for guard in p.get('protected_audio',[]):
            op=operations.get(guard.get('operation_id'))
            if not op or op.get('type')!='play' or op.get('audio')!='original':fail('protected_audio',path,'原声保护必须属于原声play');continue
            if not numeric(op.get('source_start')) or not numeric(op.get('source_end')):continue
            x,y=guard.get('source_start'),guard.get('source_end')
            if not numeric(x) or not numeric(y) or not op['source_start']<=x<y<=op['source_end']:fail('protected_audio',path,'保护区间不在播放区间内')
    ordered=list(operations.values())
    positions={op['id']:i for i,op in enumerate(ordered)}
    for i,op in enumerate(ordered):
        if op.get('type')!='replay':continue
        target=operations.get(op.get('return_to'))
        if not target or target.get('type')!='play' or positions.get(target.get('id'),-1)<=i:
            fail('replay_return',op['id'],'回放返回点必须为之后的play')
        start,end=op.get('source_start'),op.get('source_end')
        if numeric(start) and numeric(end):
            ranges=sorted((p['source_start'],p['source_end']) for p in ordered[:i] if p.get('type')=='play' and numeric(p.get('source_start')) and numeric(p.get('source_end')))
            covered=[]
            for x,y in ranges:
                if covered and x<=covered[-1][1]+1e-6:covered[-1][1]=max(y,covered[-1][1])
                else:covered.append([x,y])
            if not any(x<=start+1e-6 and y>=end-1e-6 for x,y in covered):
                fail('replay_unseen',op['id'],'回放区间必须已经被正常播放过')
    # Pre-TTS estimates catch gross contradictions; real-duration compile remains authoritative.
    starts={};cursor=0;valid_timing=True
    for op in ordered:
        duration=op.get('duration') if op.get('type')=='freeze' else ((op.get('source_end')-op.get('source_start')) if numeric(op.get('source_end')) and numeric(op.get('source_start')) else None)
        if not numeric(duration) or duration<=0:valid_timing=False;break
        fps=project.get('fps',24);duration=max(1,round(duration*fps))/fps
        starts[op['id']]=(cursor,cursor+duration);cursor+=duration
    all_guards=[g for p in paragraphs if isinstance(p,dict) for g in p.get('protected_audio',[])]
    for required in project.get('required_audio_ranges',[]):
        x,y=required['start'],required['end']
        if not any(numeric(g.get('source_start')) and numeric(g.get('source_end')) and g['source_start']<=x and g['source_end']>=y for g in all_guards):
            fail('required_audio_missing','protected_audio',f'必须完整保护源原声{x}–{y}: {required.get("purpose","")}')
    if valid_timing:
        guards=[]
        for g in all_guards:
            op=operations.get(g.get('operation_id'))
            if (op and op.get('type')=='play' and op.get('audio')=='original'
                    and numeric(op.get('source_start')) and numeric(op.get('source_end'))
                    and numeric(g.get('source_start')) and numeric(g.get('source_end'))
                    and op['source_start']<=g['source_start']<g['source_end']<=op['source_end']):
                guards.append((starts[op['id']][0]+g['source_start']-op['source_start'],starts[op['id']][0]+g['source_end']-op['source_start']))
        cps=project.get('estimated_chars_per_second',3.4)
        for i,p in enumerate(paragraphs):
            n=p.get('narration') if isinstance(p,dict) else None
            if not isinstance(n,dict) or n.get('at_operation') not in starts or not numeric(n.get('offset',0)):continue
            start=starts[n['at_operation']][0]+n.get('offset',0)
            estimate=len(re.findall(r'[\u3400-\u9fffA-Za-z0-9]',n.get('text','')))/cps
            end=start+estimate
            for ci,cue in enumerate(n.get('proof_cues',[])):
                ca,cb=cue.get('audio_start'),cue.get('audio_end')
                if not numeric(ca) or not numeric(cb) or not 0<=ca<cb:continue
                windows=sorted(starts[oid] for oid in cue['operation_ids'] if oid in starts)
                merged=[]
                for x,y in windows:
                    if merged and x<=merged[-1][1]+1e-6:merged[-1][1]=max(y,merged[-1][1])
                    else:merged.append([x,y])
                if not any(x<=start+ca+.05 and y>=start+cb-.05 for x,y in merged):
                    fail('proof_visibility',f'paragraphs[{i}].narration.proof_cues[{ci}]','该语音时刻没有同步展示指定证据操作')
            valid_windows=sorted(starts[oid] for oid in n.get('allowed_operations',[]) if oid in starts)
            joined=[]
            for x,y in valid_windows:
                if joined and x<=joined[-1][1]+1e-6:joined[-1][1]=max(y,joined[-1][1])
                else:joined.append([x,y])
            if not any(x<=start+1e-6 and y>=end-.02 for x,y in joined):
                fail('estimated_window_overrun',f'paragraphs[{i}].narration',f'全文预计需{estimate:.2f}秒，输出{start:.2f}–{end:.2f}超出allowed_operations{valid_windows}。精简完整思路或安排有意义的后续画面，不仅把offset挪到末尾。')
            if any(min(end+.2,y)>max(start-.2,x)+1e-6 for x,y in guards):
                collision=[(round(x,2),round(y,2)) for x,y in guards if min(end+.2,y)>max(start-.2,x)+1e-6]
                fail('estimated_audio_overlap',f'paragraphs[{i}].narration',f'旁白预计输出{start:.2f}–{end:.2f}秒覆盖原声保护{collision}。此段设narration=null或另设保护区间之后的旁白，不能删除required_audio_ranges。')
            for event in n.get('after_events',[]):
                op=operations.get(event.get('operation_id'));when=event.get('source_time')
                if op and numeric(when):
                    if op.get('type')!='play' or not op['source_start']<=when<=op['source_end']:
                        fail('event_anchor_range',f'paragraphs[{i}]','事件时刻必须在相应play操作内')
                    elif start<starts[op['id']][0]+when-op['source_start']-1e-6:
                        event_output=starts[op['id']][0]+when-op['source_start']
                        fail('early_event_narration',f'paragraphs[{i}]',f'旁白输出起点{start:.2f}秒早于事件{event_output:.2f}秒（操作{op["id"]}源时刻{when}）。若在同一操作解释已发生结果，offset至少{when-op["source_start"]+.2:.2f}，同时检查能否放下全文；否则改稿、换到后续操作或narration=null。')
    questions=story.get('questions',[]);qids=set()
    if not isinstance(questions,list):fail('question_link','questions','expected list');questions=[]
    for q in questions:
        if not isinstance(q,dict):fail('question_link','questions','expected object');continue
        qid=q.get('id');setup=q.get('setup_paragraph');payoff=q.get('payoff_paragraph')
        if not qid or qid in qids:fail('question_link','questions','问题ID必须唯一')
        qids.add(qid)
        if setup not in paragraph_ids:fail('question_link','questions','找不到问题建立段落');continue
        if qid not in paragraphs[paragraph_ids[setup]].get('questions_opened',[]):fail('question_link','questions','建立段落没有记录此问题')
        if q.get('status')=='resolved':
            if payoff not in paragraph_ids:fail('question_link','questions','找不到回收段落');continue
            if paragraph_ids[payoff]<paragraph_ids[setup]:fail('question_order','questions','回收在建立之前')
            if qid not in paragraphs[paragraph_ids[payoff]].get('questions_answered',[]):fail('question_payoff','questions','回收段落没有回应此问题')
        elif q.get('status')=='deferred':
            if not q.get('next_part'):fail('question_payoff','questions','延后问题需要明确后续归属')
            if payoff or any(qid in p.get('questions_answered',[]) for p in paragraphs):
                fail('question_state','questions','deferred不能同时有本Part的payoff或answered')
        else:fail('question_link','questions','问题必须 resolved/deferred')
    for i,p in enumerate(paragraphs):
        if isinstance(p,dict):
            for qid in p.get('questions_opened',[])+p.get('questions_answered',[]):
                if qid not in qids:fail('question_link',f'paragraphs[{i}]',f'未知问题{qid}')
            by_question={q['id']:q for q in questions}
            for field,target in [('questions_opened','setup_paragraph'),('questions_answered','payoff_paragraph')]:
                for qid in p.get(field,[]):
                    q=by_question.get(qid)
                    if q and q.get(target)!=p['id']:fail('question_state',f'paragraphs[{i}].{field}',f'{qid}的{target}属于另一段落')
    return findings
