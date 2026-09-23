"""Synthesize editorial narration and measure the final PCM audio for timing."""
import argparse
import base64
import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import subprocess
import uuid
import wave

ROOT = Path(__file__).resolve().parents[3]


def read_json(path):
    with Path(path).open(encoding='utf-8') as handle:
        return json.load(handle)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def load_environment():
    """Load local configuration without replacing inherited environment values."""
    env_path = Path(os.environ.get('RECAP_ENV_FILE', str(ROOT / '.env'))).expanduser()
    if os.environ.get('RECAP_ENV_FILE') and not env_path.is_file():
        raise FileNotFoundError('RECAP_ENV_FILE does not point to a file')
    for line in env_path.read_text().splitlines() if env_path.exists() else []:
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_project(path):
    """Resolve project paths from its JSON; voice needs no source-media access."""
    path = Path(path).expanduser().resolve()
    project = read_json(path)
    if not isinstance(project, dict):
        raise ValueError('project must be a JSON object')
    source = project.get('source', {})
    if source.get('path'):
        source['path'] = str((path.parent / source['path']).resolve())
    for key in ('understanding_dir', 'style_path', 'verified_notes', 'research_path',
                'authoring_draft_path', 'development_path', 'review_decision_path'):
        if project.get(key):
            project[key] = str((path.parent / project[key]).resolve())
    for key, value in project.get('source_binding', {}).items():
        if key in {'proxy_meta', 'render_manifest', 'analysis_source_map'}:
            project['source_binding'][key] = str((path.parent / value).resolve())
    project['project_path'] = str(path)
    return project


def pcm_duration(path):
    with wave.open(str(path),'rb') as f:return f.getnframes()/f.getframerate()


def run(cmd,env=None):
    result=subprocess.run(cmd,env=env,capture_output=True,text=True)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    return result.stdout


def synthesize_mimo_preset(text,output_wav,*,speaker):
    """The verified custom gateway path accepts spoken assistant text only."""
    from aihub_adapter import credentials,post,BASE
    app_id,key=credentials();source='video-recap-mimo'
    date=datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    sign=base64.b64encode(hmac.new(key.encode(),f'date: {date}\nsource: {source}'.encode(),hashlib.sha1).digest()).decode()
    request_id=str(uuid.uuid4());model='api_xiaomi_mimo-v2.5-tts'
    headers={'Apiversion':'v2.03','Date':date,'Source':source,'X-Request-ID':request_id,
             'Authorization':f'hmac id="{app_id}", algorithm="hmac-sha1", headers="date source", signature="{sign}"'}
    payload={'request_id':request_id,'model_marker':model,
             'messages':[{'content':[{'type':'text','role':'assistant','value':text}]}],
             'params':{'audio':{'format':'wav','voice':speaker},'stream':False},'timeout':3600}
    _,result=post(BASE+'/api/v1/data_eval',payload,headers,timeout=185)
    if result.get('code')!=0:raise RuntimeError('MiMo TTS request rejected')
    raw=b''.join(base64.b64decode(row['value'],validate=True) for row in result.get('answer',[]) if row.get('type')=='audio_base64')
    if not raw:raise RuntimeError('MiMo TTS returned no audio')
    output_wav=Path(output_wav);output_wav.write_bytes(raw)
    write_json(output_wav.with_suffix('.provider.json'),{'provider':'aihub-mimo','model':model,'voice':speaker,
               'format':'wav','source_text':text,'instruction':'','usage':result.get('usage'),'cost_info':result.get('cost_info')})


def prepare_audio(project,plan,work,synthesizer=None):
    work=Path(work);directory=work/'editorial_tts';directory.mkdir(parents=True,exist_ok=True)
    voice=project.get('voice',{});provider=voice.get('provider','aihub-doubao')
    if provider not in {'aihub-doubao','aihub-mimo'}:raise ValueError('unsupported voice provider')
    speaker=voice.get('speaker','茉莉' if provider=='aihub-mimo' else 'zh_male_cixingjieshuonan_uranus_bigtts')
    if not isinstance(speaker,str) or not speaker.strip():raise ValueError('voice speaker must be nonempty')
    tempo=voice.get('tempo',1 if provider=='aihub-mimo' else 1.1)
    model='api_xiaomi_mimo-v2.5-tts' if provider=='aihub-mimo' else 'api_doubao_doubao-tts-2.0'
    if isinstance(tempo,bool) or not isinstance(tempo,(int,float)) or not math.isfinite(tempo) or not .5<=tempo<=2:raise ValueError('tempo must be finite in .5..2')
    if synthesizer is None:
        load_environment()
        if provider=='aihub-mimo':
            synthesizer=lambda text,path,rate: synthesize_mimo_preset(text,path,speaker=speaker)
        else:
            os.environ['DOUBAO_TTS_VOICE']=speaker
            from aihub_adapter import synthesize
            synthesizer=synthesize
    catalog={}
    for n in plan['narrations']:
        identity={'text':n['text'],'speaker':speaker,'tempo':tempo,'native_rate':0,'provider':provider,'model':model,'version':1}
        key=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
        raw=directory/f'{key}-raw.wav';ready=directory/f'{key}-ready.wav';meta=directory/f'{key}.json'
        valid=False
        if meta.exists() and ready.exists():
            old=read_json(meta);valid=old.get('identity')==identity and old.get('ready_sha256')==fingerprint(ready)
        if not valid:
            print(f"Voice {n['id']}",flush=True)
            synthesizer(n['text'],raw,rate='+0%')
            run(['ffmpeg','-v','error','-y','-i',str(raw),'-af',f'atempo={tempo},loudnorm=I=-20:TP=-2:LRA=11','-ar','44100','-ac','1','-c:a','pcm_s16le',str(ready)])
            write_json(meta,{'identity':identity,'ready_sha256':fingerprint(ready),'duration':pcm_duration(ready)})
        catalog[n['id']]={'id':n['id'],'path':str(ready.resolve()),'duration':pcm_duration(ready),'text':n['text'],'post_tempo':tempo,'speaker':speaker,'provider':provider,'model':model}
    write_json(work/'audio_catalog.json',catalog)
    write_json(work/'audio_durations.json',{key:value['duration'] for key,value in catalog.items()})
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, help='Project JSON; embedded paths are relative to this file')
    parser.add_argument('--plan', required=True, help='Editorial plan JSON containing narrations')
    parser.add_argument('--work-dir', required=True, help='Output directory for audio and measured durations')
    args = parser.parse_args(argv)
    os.environ['PATH'] = str(ROOT / 'tools') + os.pathsep + os.environ.get('PATH', '')
    project = load_project(args.project)
    plan = read_json(Path(args.plan).expanduser().resolve())
    work = Path(args.work_dir).expanduser().resolve()
    return prepare_audio(project, plan, work)


if __name__ == '__main__':
    main()
