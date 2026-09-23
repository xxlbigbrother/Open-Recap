"""Run explicit reference-guided authoring; inputs immutable, failed drafts quarantined."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from editorial_inputs import load_project,build_evidence,fingerprint,index_evidence
from editorial_catalog import load_style
from editorial_contract import validate_story
from editorial_projection import project_editorial,project_board,draft_markdown,story_fingerprint
from editorial_prompts import generate_messages,review_messages,authoring_messages,PROMPT_VERSION
from editorial_development import validate_development,selected_context,evidence_digest
from editorial_adjudication import load_adjudication


def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def parse_object(response):
    if not isinstance(response,dict):raise ValueError('response must be object')
    text=response['choices'][0]['message']['content']
    if not isinstance(text,str):raise ValueError('model content must be text')
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
    result=json.loads(text)
    if not isinstance(result,dict):raise ValueError('model output must be JSON object')
    return result


def effective_config(project,model_identity,provider_config=None):
    generation={'temperature':.65,'max_tokens':14000}
    requested=project.get('generation',{})
    if not isinstance(requested,dict) or set(requested)-set(generation):raise ValueError('unsupported generation configuration')
    generation.update(requested)
    if type(generation['max_tokens'])!=int or not 1000<=generation['max_tokens']<=30000:raise ValueError('generation max_tokens must be 1000..30000')
    if isinstance(generation['temperature'],bool) or not isinstance(generation['temperature'],(int,float)) or not 0<=generation['temperature']<=2:raise ValueError('invalid temperature')
    provider=provider_config or {}
    return {'model':model_identity,'generation':generation,'review':{'temperature':.2,'max_tokens':4000},
            'provider':{k:provider[k] for k in ['api_provider','api_url','mimo_disable_thinking','adapter_version'] if k in provider}}


def generate_story(project,evidence,style,call_model,max_revisions=2,model_identity='configured-chat',on_attempt=None,settings=None,authoring_draft=None,initial_candidate=None,development=None):
    if type(max_revisions)!=int or not 0<=max_revisions<=2:raise ValueError('max_revisions must be 0..2')
    attempts=[];previous=None;findings=[]
    settings=settings or effective_config(project,model_identity)
    for attempt in range(max_revisions+1):
        story=None
        messages=generate_messages(project,evidence,style,previous,findings)
        if development is not None:
            messages.append(development_message(development))
        if authoring_draft is not None:
            messages.append({'role':'user','content':'已有连续讲述草稿。现在将它与原声安排共同编排为执行story，保留新增价值，可以精简、拆分或改变画面呈现，但不得仅把长稿挪到短窗口的末尾。原声先后和证据约束优先。\nAUTHORING_DRAFT:\n'+json.dumps(authoring_draft,ensure_ascii=False)})
        payload={'model':model_identity,'messages':messages,**settings['generation']}
        print(f'Editorial generation {attempt+1}/{max_revisions+1}',flush=True)
        try:
            if initial_candidate is not None and attempt==0:story=initial_candidate
            else:story=parse_object(call_model(payload))
        except Exception as exc:
            findings=[{'severity':'error','code':'generation_invalid','path':'$','message':f'生成/结构解析失败：{type(exc).__name__}'}]
            record={'attempt':attempt+1,'findings':findings};attempts.append(record)
            if on_attempt:on_attempt(record)
            continue
        try:
            findings=validate_story(story,project,evidence,style)
        except Exception as exc:
            import traceback
            findings=[{'severity':'error','code':'validator_internal_error','path':'$','message':f'本地验证器异常：{type(exc).__name__}'}]
            record={'attempt':attempt+1,'story':story,'findings':findings,
                    'diagnostic_frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(exc.__traceback__)]}
            attempts.append(record)
            if on_attempt:on_attempt(record)
            return {'status':'needs_review','attempts':attempts,'findings':findings}
        previous=story
        if findings:
            record={'attempt':attempt+1,'story':story,'findings':findings};attempts.append(record)
            if on_attempt:on_attempt(record)
            print('Structural findings: '+', '.join(x['code'] for x in findings),flush=True)
            continue
        print('Editorial semantic review',flush=True)
        try:
            review_context=review_messages(story,project,evidence,style)
            if development is not None:review_context.append(development_message(development))
            review=parse_object(call_model({'model':model_identity,'messages':review_context,**settings['review']}))
            if review.get('verdict') not in {'pass','revise'} or not isinstance(review.get('findings'),list):raise ValueError('invalid review')
            for item in review['findings']:
                if not isinstance(item,dict) or item.get('severity') not in {'error','warning'} or not all(item.get(k) for k in ['code','path','message']):raise ValueError('invalid finding')
            findings=review['findings']
            if review['verdict']=='revise' and not any(x['severity']=='error' for x in findings):
                findings=[*findings,{'severity':'error','code':'review_inconsistent','path':'$','message':'review要求修改却未给出具体error'}]
        except Exception as exc:
            findings=[{'severity':'error','code':'review_unavailable','path':'$','message':f'语义评审不可用：{type(exc).__name__}'}]
            attempts.append({'attempt':attempt+1,'story':story,'findings':findings})
            if on_attempt:on_attempt(attempts[-1])
            return {'status':'needs_review','attempts':attempts,'findings':findings}
        attempts.append({'attempt':attempt+1,'story':story,'findings':findings,
                         'record_type':'editorial_semantic_review','review_stage':'semantic','review_completed':True,
                         'evidence_sha256':evidence_digest(evidence),'source_id':evidence.get('source_id')})
        if on_attempt:on_attempt(attempts[-1])
        if not any(x['severity']=='error' for x in findings):
            return {'status':'ready_for_editorial_review','story':story,'attempts':attempts,'findings':findings}
    return {'status':'needs_review','attempts':attempts,'findings':findings}


def development_message(development):
    return {'role':'user','content':'已选观察计划。围绕其具体证据、解释与观看收获写作或审稿；它不是新增事实证据，仍以TARGET_EVIDENCE为准。剪辑复核项在实际编排时解决。\n'+json.dumps(development,ensure_ascii=False)}


def run_project(project_path,output_dir,call_model=None,*,model_identity=None,prepare_only=False,force=False,candidate_path=None):
    project=load_project(project_path);evidence=build_evidence(project);style=load_style(project['style_path'])
    work=Path(output_dir).resolve();work.mkdir(parents=True,exist_ok=True)
    candidate=None;candidate_hash=None;adjudication=None
    if candidate_path:
        candidate_bytes=Path(candidate_path).read_bytes()
        candidate=json.loads(candidate_bytes)
        candidate_hash=hashlib.sha256(candidate_bytes).hexdigest()
    if project.get('review_decision_path'):
        try:
            if candidate is None:raise ValueError('review decisions require an Agent candidate')
            adjudication=load_adjudication(project['review_decision_path'],candidate,evidence)
        except (ValueError,OSError) as exc:
            result={'status':'needs_review','findings':[{'severity':'error','code':'adjudication_invalid','path':'review_decision_path','message':str(exc)}],'attempts':[],'cache':'miss'}
            write_json(work/'last_failed_run.json',result)
            return result
    development=None;chosen=None;development_hash=None
    if project.get('development_path'):
        try:
            development_bytes=Path(project['development_path']).read_bytes()
            development_hash=hashlib.sha256(development_bytes).hexdigest()
            development=json.loads(development_bytes)
            findings=validate_development(development,project,evidence)
            if not findings:
                chosen=selected_context(development)
                if chosen is None and not prepare_only:findings=[{'severity':'error','code':'selected_angle','path':'selected_id','message':'观察计划仍需研究，尚未选择可写角度'}]
        except (ValueError,OSError):
            findings=[{'severity':'error','code':'development_unavailable','path':'development_path','message':'观察计划缺失或不可解析'}]
        if findings:
            run_id=uuid.uuid4().hex[:12]
            result={'status':'needs_review','findings':findings,'attempts':[],'cache':'miss','run_id':run_id}
            write_json(work/'attempts'/run_id/'development.json',development)
            write_json(work/'attempts'/run_id/'result.json',result)
            write_json(work/'last_failed_run.json',result)
            return result
    provider_config={}
    if call_model is None and not prepare_only:
        from lib import CONFIG,api_call
        provider_config={k:CONFIG[k] for k in ['api_provider','api_url','mimo_disable_thinking'] if k in CONFIG}
        if CONFIG.get('api_provider')=='aihub-doubao':
            from aihub_adapter import VERSION
            provider_config['adapter_version']=VERSION
        call_model=api_call
    if model_identity is None:
        if prepare_only:model_identity='unresolved-until-generation'
        else:
            from lib import CONFIG
            model_identity=CONFIG.get('vlm_model','configured-chat')
    settings=effective_config(project,model_identity,provider_config)
    code_hashes={p.name:fingerprint(p) for p in Path(__file__).parent.glob('editorial*.py')}
    identity={'project':project,'input_hashes':evidence['fingerprints'],'source':evidence['source_fingerprint'],
              'style_hashes':style['fingerprints'],'prompt_version':PROMPT_VERSION,'model':model_identity,'effective_config':settings,'code_hashes':code_hashes}
    if project.get('authoring_draft_path'):identity['authoring_draft_sha256']=fingerprint(project['authoring_draft_path'])
    if candidate_path:identity['agent_candidate_sha256']=candidate_hash
    if project.get('development_path'):identity['development_sha256']=development_hash
    if adjudication is not None:identity['adjudication_hashes']=adjudication['fingerprints']
    cache_key=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    manifest_path=work/'editorial_run.json'
    try:prior=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    except (ValueError,OSError):prior={}
    if not force and not prepare_only and manifest_path.exists():
        try:old=json.loads(manifest_path.read_text())
        except (ValueError,OSError):old={}
        if not isinstance(old,dict):old={}
        required_outputs={'input_manifest.json','evidence_bundle.json','style_snapshot.json','recap_story_plan.json',
                          'visual_audio_board.json','editorial_plan.json','editorial_review.json','narration_draft.md'}
        if project.get('authoring_mode')=='write_then_plan' and candidate_path is None:required_outputs.add('authoring_draft.json')
        if development is not None:required_outputs.add('editorial_development.json')
        if adjudication is not None:required_outputs.add('editorial_adjudication.json')
        hashes=old.get('output_hashes',{})
        if old.get('cache_key')==cache_key and old.get('status')=='ready_for_editorial_review' and isinstance(hashes,dict) and set(hashes)==required_outputs and all((work/name).is_file() and fingerprint(work/name)==h for name,h in hashes.items()):
            return {**old,'cache':'hit'}
    run_id=uuid.uuid4().hex[:12];attempt_dir=work/'attempts'/run_id
    input_artifacts={'input_manifest.json':identity,'evidence_bundle.json':evidence,'style_snapshot.json':style}
    if development is not None:input_artifacts['editorial_development.json']=development
    if adjudication is not None:input_artifacts['editorial_adjudication.json']=adjudication
    for name,data in input_artifacts.items():write_json(attempt_dir/name,data)
    if prepare_only:
        if not manifest_path.exists():
            for name,data in input_artifacts.items():write_json(work/name,data)
        return {'status':'prepared','cache':'miss','cache_key':cache_key,'work_dir':str(work),'prepared_dir':str(attempt_dir)}
    authoring_draft=None
    if project.get('authoring_mode')=='write_then_plan' and candidate_path is None:
        print('Continuous authoring before timing',flush=True)
        try:
            if project.get('authoring_draft_path'):authoring_draft=json.loads(Path(project['authoring_draft_path']).read_text())
            else:
                author_context=authoring_messages(project,evidence,style)
                if chosen is not None:author_context.append(development_message(chosen))
                authoring_draft=parse_object(call_model({'model':model_identity,'messages':author_context,'max_tokens':4000,'temperature':settings['generation']['temperature']}))
            write_json(attempt_dir/'authoring_draft.json',authoring_draft)
            if not authoring_draft.get('viewer_promise') or not authoring_draft.get('angle') or not isinstance(authoring_draft.get('passages'),list) or not authoring_draft['passages']:raise ValueError('missing continuous draft')
            ids=set();valid_evidence={key for key,r in index_evidence(evidence).items() if not r.get('disputed_by')}
            for p in authoring_draft['passages']:
                if not isinstance(p,dict) or not isinstance(p.get('id'),str) or p['id'] in ids or not isinstance(p.get('spoken_text'),str) or not p.get('return_to_story'):raise ValueError('invalid passage')
                audio_job=p.get('original_audio_job')
                if not p['spoken_text'].strip() and not audio_job:raise ValueError('invalid passage')
                if audio_job is not None and not isinstance(audio_job,(str,dict)):raise ValueError('invalid passage')
                if isinstance(audio_job,dict) and (not isinstance(audio_job.get('start'),(int,float)) or not isinstance(audio_job.get('end'),(int,float)) or not project['source']['range'][0]<=audio_job['start']<audio_job['end']<=project['source']['range'][1] or not audio_job.get('purpose')):raise ValueError('invalid passage')
                ids.add(p['id'])
                if not isinstance(p.get('evidence_ids'),list) or any(k not in valid_evidence for k in p['evidence_ids']):raise ValueError('invalid draft evidence')
        except Exception as exc:
            detail=str(exc) if str(exc) in {'missing continuous draft','invalid passage','invalid draft evidence'} else type(exc).__name__
            result={'status':'needs_review','findings':[{'severity':'error','code':'authoring_draft_invalid','path':'authoring_draft','message':detail}],'attempts':[],'cache':'miss','run_id':run_id}
            write_json(attempt_dir/'result.json',result);write_json(work/'last_failed_run.json',result);return result
    if adjudication is not None:
        try:findings=validate_story(candidate,project,evidence,style)
        except Exception as exc:
            findings=[{'severity':'error','code':'validator_internal_error','path':'$','message':f'本地验证器异常：{type(exc).__name__}'}]
        record={'attempt':1,'story':candidate,'findings':findings,'review_origin':adjudication['origin']}
        write_json(attempt_dir/'attempt-1.json',record)
        result={'status':'needs_review' if findings else 'ready_for_editorial_review','story':candidate,'attempts':[record],'findings':findings,'review_origin':adjudication['origin']}
    else:
        result=generate_story(project,evidence,style,call_model,model_identity=model_identity,
                          on_attempt=lambda record:write_json(attempt_dir/f"attempt-{record['attempt']}.json",record),settings=settings,authoring_draft=authoring_draft,
                          initial_candidate=candidate,max_revisions=0 if candidate_path else 2,development=chosen)
    write_json(attempt_dir/'result.json',result)
    output={k:v for k,v in result.items() if k!='story'}
    output.update(cache='miss',cache_key=cache_key,run_id=run_id,work_dir=str(work),model=model_identity)
    output['authoring_origin']='agent_candidate' if candidate_path else ('llm_two_pass' if authoring_draft else 'llm_direct')
    if result['status']!='ready_for_editorial_review':
        write_json(work/'last_failed_run.json',output);return output
    story=result['story'];projection=project_editorial(story,project)
    artifacts={**input_artifacts,'recap_story_plan.json':story,'visual_audio_board.json':project_board(story),
               'editorial_plan.json':projection,'editorial_review.json':{'findings':result['findings'],'status':('agent_adjudicated_user_review_pending' if adjudication['origin']=='agent_adjudication' else 'human_adjudicated') if adjudication else 'machine_review_passed_user_review_pending'}}
    if authoring_draft is not None:artifacts['authoring_draft.json']=authoring_draft
    for name,data in artifacts.items():write_json(attempt_dir/'accepted'/name,data)
    (attempt_dir/'accepted/narration_draft.md').write_text(draft_markdown(story))
    for name,data in artifacts.items():write_json(work/name,data)
    (work/'narration_draft.md').write_text(draft_markdown(story))
    if development is None and isinstance(prior,dict):
        old_hashes=prior.get('output_hashes',{})
        old_path=work/'editorial_development.json'
        if isinstance(old_hashes,dict) and 'editorial_development.json' in old_hashes and old_path.exists():
            # Preserve even local annotations, but remove the old current-artifact name.
            archived=attempt_dir/'superseded'/'editorial_development.json'
            archived.parent.mkdir(parents=True,exist_ok=True)
            old_path.replace(archived)
    if adjudication is None and isinstance(prior,dict):
        old_hashes=prior.get('output_hashes',{})
        old_path=work/'editorial_adjudication.json'
        if isinstance(old_hashes,dict) and 'editorial_adjudication.json' in old_hashes and old_path.exists():
            archived=attempt_dir/'superseded'/'editorial_adjudication.json'
            archived.parent.mkdir(parents=True,exist_ok=True)
            old_path.replace(archived)
    output['output_hashes']={name:fingerprint(work/name) for name in [*artifacts,'narration_draft.md']}
    output['parent_story_sha256']=story_fingerprint(story)
    write_json(manifest_path,output)
    return output
