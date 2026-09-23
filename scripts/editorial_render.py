"""Bridge editorial plans to explicit AIHub voice providers and presentation/assembly."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
# Load the stage by its exact file, independent of sibling skills' module names.
_voice_spec=importlib.util.spec_from_file_location('openrecap_voiceover',ROOT/'skills/video-voiceover/scripts/voiceover.py')
voiceover=importlib.util.module_from_spec(_voice_spec)
_voice_spec.loader.exec_module(voiceover)

SCRIPT=ROOT/'skills/video-script/scripts'


def prepare_outputs(project,compiled,catalog,work):
    from editorial_inputs import read_json
    from editorial_runner import write_json
    from editorial_timing import build_tts_meta,map_source_evidence

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
    args=parser.parse_args()
    sys.path.insert(0,str(SCRIPT))
    from editorial_inputs import load_project,read_json
    from editorial_runner import write_json

    os.environ['PATH']=str(ROOT/'tools')+os.pathsep+os.environ.get('PATH','')
    work=Path(args.work_dir).resolve();project=load_project(args.project)
    plan=read_json(work/'editorial_plan.json');story=read_json(work/'recap_story_plan.json')
    from editorial_projection import story_fingerprint
    if plan.get('parent_story_sha256')!=story_fingerprint(story):raise ValueError('stale editorial plan parent')
    voiceover.load_environment()
    default_font='PingFang SC' if sys.platform=='darwin' else 'Noto Sans CJK SC'
    os.environ.setdefault('SUBTITLE_FONT_NAME',default_font)
    catalog=voiceover.prepare_audio(project,plan,work)
    if args.audio_only:return
    geometry=json.loads(voiceover.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',project['source']['path']]))['streams'][0]
    delivery=delivery_settings(project,geometry)
    write_json(work/'delivery_settings.json',delivery)
    env=dict(os.environ);env['PYTHONPATH']=str(ROOT/'scripts')
    cut=ROOT/'skills/video-cut/scripts/presentation.py'
    print(voiceover.run([sys.executable,str(cut),'--plan',str(work/'editorial_plan.json'),'--audio-durations',str(work/'audio_durations.json'),'--work-dir',str(work),'--width',str(delivery['width']),'--height',str(delivery['height'])],env),flush=True)
    compiled=read_json(work/'presentation_compiled.json');prepare_outputs(project,compiled,catalog,work)
    if delivery['subtitle_band']:
        write_json(work/'source_subtitle_mask_windows.json',{'schema_version':1,'clock':'output','windows':[
            {'start':op['output_start'],'end':op['output_end'],'reason':'source_muted'}
            for op in compiled['operations'] if op['audio']=='mute']})
    env.update({'NARRATION_SPEED':'1','NARRATION_TIGHTEN':'false','TTS_SEGMENT_TEMPO_MAX':'1','NARRATION_CUMULATIVE_TEMPO_MAX':'1','NARRATION_CUMULATIVE_TEMPO_HARD_MAX':'1',
                'SUBTITLE_FONT_NAME':os.environ.get('SUBTITLE_FONT_NAME',default_font),'SUBTITLE_FONT_SIZE':'76','SUBTITLE_MAX_CHARS':'22','SUBTITLE_MARGIN_V':'12','SUBTITLE_ORIGINAL_IN_GAPS':'false',
                'SUBTITLE_FONTS_DIR':os.environ.get('SUBTITLE_FONTS_DIR',str(ROOT/'tools/fonts')),
                'SUBTITLE_MASK_BRIDGE_SECONDS':'.8','DUCK_FADE_SECONDS':'.2','DUCK_BRIDGE_SECONDS':'.25','SPEECH_DUCKING_VOLUME':'.1','ZONE_DUCKING_VOLUME':'.12'})
    assembly=ROOT/'skills/video-assemble/scripts/assemble.py'
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
