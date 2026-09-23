"""Bridge editorial plans to explicit AIHub voice providers and presentation/assembly."""
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
import sys
import uuid
import wave

ROOT=Path(__file__).resolve().parent
SCRIPT=ROOT/'skill/skills/video-script/scripts'
sys.path.insert(0,str(SCRIPT))
from editorial_inputs import load_project,fingerprint,read_json
from editorial_runner import write_json
from editorial_timing import build_tts_meta,map_source_evidence


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
        env_path=Path(os.environ.get('RECAP_ENV_FILE',str(ROOT/'.env'))).expanduser()
        if os.environ.get('RECAP_ENV_FILE') and not env_path.is_file():
            raise FileNotFoundError('RECAP_ENV_FILE does not point to a file')
        for line in env_path.read_text().splitlines() if env_path.exists() else []:
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key,value=line.split('=',1);os.environ.setdefault(key.strip(),value.strip().strip("\"'"))
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


def prepare_outputs(project,compiled,catalog,work):
    meta=build_tts_meta(compiled,catalog);write_json(work/'tts_meta.json',meta);write_json(work/'narration.json',meta['segments'])
    base=Path(project['understanding_dir'])
    acoustic=read_json(base/'speech_boundary_anchors.json') if (base/'speech_boundary_anchors.json').exists() else {'sentence_anchors':[]}
    silence=read_json(base/'silence_periods.json') if (base/'silence_periods.json').exists() else []
    mapped=map_source_evidence(compiled,read_json(base/'asr_result.json'),acoustic,silence)
    write_json(work/'speech_boundary_anchors.json',mapped)
    return meta


def delivery_settings(project,source_geometry):
    requested=project.get('delivery',{})
    if 'width' in requested or 'height' in requested:
        width,height=requested.get('width'),requested.get('height')
        if type(width)!=int or type(height)!=int or min(width,height)<2 or width%2 or height%2:raise ValueError('delivery dimensions must be even positive integers')
    else:
        width=min(1280,source_geometry['width']);width=2*round(width/2)
        height=2*round((source_geometry['height']*width/source_geometry['width'])/2)
    band=requested.get('subtitle_band')
    if band is not None and (not isinstance(band,list) or len(band)!=2 or not all(type(x)==int for x in band) or not 0<=band[0]<band[1]<=height):
        raise ValueError('subtitle band must be inside output canvas')
    return {'width':width,'height':height,'subtitle_band':band}


def verify_assembly_timing(compiled,assembly):
    actual=assembly['audio_segments']
    if len(actual)!=len(compiled['narrations']):raise ValueError('placement count mismatch')
    for planned,placed in zip(compiled['narrations'],actual):
        start,end=placed['actual_place_start'],placed['actual_place_end']
        if abs(start-planned['start'])>.002 or abs(end-planned['end'])>.02:raise ValueError('placement differs from evidence plan')
        if abs(placed.get('segment_tempo_factor',1)-1)>.001:raise ValueError('additional tempo fit was not authorized')
        restore=placed.get('source_restore_at') or end+.2
        for guard in compiled['protected_audio']:
            if min(restore,guard['output_end'])>max(start-.2,guard['output_start'])+1e-6:
                raise ValueError(f'protected original audio overlap: {planned["id"]}')
    return {'passed':True,'checked_segments':len(actual),'checks':['actual placement','actual source restore','no extra tempo fit']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True);parser.add_argument('--work-dir',required=True)
    parser.add_argument('--audio-only',action='store_true');parser.add_argument('--output-dir')
    args=parser.parse_args();work=Path(args.work_dir).resolve();project=load_project(args.project)
    plan=read_json(work/'editorial_plan.json');story=read_json(work/'recap_story_plan.json')
    from editorial_projection import story_fingerprint
    if plan.get('parent_story_sha256')!=story_fingerprint(story):raise ValueError('stale editorial plan parent')
    catalog=prepare_audio(project,plan,work)
    if args.audio_only:return
    geometry=json.loads(run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',project['source']['path']]))['streams'][0]
    delivery=delivery_settings(project,geometry)
    write_json(work/'delivery_settings.json',delivery)
    env=dict(os.environ);env['PYTHONPATH']=str(ROOT);env['PATH']=str(ROOT/'tools')+os.pathsep+env.get('PATH','')
    cut=ROOT/'skill/skills/video-cut/scripts/presentation.py'
    print(run([sys.executable,str(cut),'--plan',str(work/'editorial_plan.json'),'--audio-durations',str(work/'audio_durations.json'),'--work-dir',str(work),'--width',str(delivery['width']),'--height',str(delivery['height'])],env),flush=True)
    compiled=read_json(work/'presentation_compiled.json');prepare_outputs(project,compiled,catalog,work)
    if delivery['subtitle_band']:
        write_json(work/'source_subtitle_mask_windows.json',{'schema_version':1,'clock':'output','windows':[
            {'start':op['output_start'],'end':op['output_end'],'reason':'source_muted'}
            for op in compiled['operations'] if op['audio']=='mute']})
    env.update({'NARRATION_SPEED':'1','NARRATION_TIGHTEN':'false','TTS_SEGMENT_TEMPO_MAX':'1','NARRATION_CUMULATIVE_TEMPO_MAX':'1','NARRATION_CUMULATIVE_TEMPO_HARD_MAX':'1',
                'SUBTITLE_FONT_NAME':os.environ.get('SUBTITLE_FONT_NAME','PingFang SC'),'SUBTITLE_FONT_SIZE':'76','SUBTITLE_MAX_CHARS':'22','SUBTITLE_MARGIN_V':'12','SUBTITLE_ORIGINAL_IN_GAPS':'false',
                'SUBTITLE_FONTS_DIR':os.environ.get('SUBTITLE_FONTS_DIR',str(ROOT/'tools/fonts')),
                'SUBTITLE_MASK_BRIDGE_SECONDS':'.8','DUCK_FADE_SECONDS':'.2','DUCK_BRIDGE_SECONDS':'.25','SPEECH_DUCKING_VOLUME':'.1','ZONE_DUCKING_VOLUME':'.12'})
    assembly=ROOT/'skill/skills/video-assemble/scripts/assemble.py'
    cmd=[sys.executable,str(assembly),str(work/'presentation_source.mp4'),'--work-dir',str(work),'--recap-stem',project['id'],'--output-dir',str(Path(args.output_dir).resolve() if args.output_dir else work/'delivery')]
    if delivery['subtitle_band']:
        cmd += ['--subtitle-y-top',str(delivery['subtitle_band'][0]),'--subtitle-y-bot',str(delivery['subtitle_band'][1])]
    else:
        env.update(MASK_SOURCE_SUBTITLES='false',SOURCE_SUBTITLE_MASK_POLICY='off')
    result=subprocess.run(cmd,env=env,capture_output=True,text=True);(work/'assembly.log').write_text(result.stdout+'\n'+result.stderr)
    if result.returncode:raise RuntimeError('assembly failed; inspect assembly.log')
    qc=verify_assembly_timing(compiled,read_json(work/'assembly_manifest.json'))
    write_json(work/'editorial_delivery_qc.json',qc)
    print(result.stdout[-1200:])


if __name__=='__main__':main()
