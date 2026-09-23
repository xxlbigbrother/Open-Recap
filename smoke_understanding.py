"""Run the actual understanding stage on a new short clip without touching old film data."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent


def load_env():
    path=Path(os.environ.get('RECAP_ENV_FILE',str(ROOT/'.env'))).expanduser()
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1);os.environ.setdefault(key.strip(),value.strip().strip("\"'"))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--video',type=Path)
    ap.add_argument('--work-dir',type=Path)
    ap.add_argument('--start',type=float,default=0)
    ap.add_argument('--seconds',type=float,default=30)
    ap.add_argument('--skip-asr',action='store_true',help='Visual-only probe; no ASR quality claim')
    ap.add_argument('--check-config',action='store_true',help='Print public settings and credential presence only; no request')
    args=ap.parse_args();load_env()
    from gemini_adapter import settings
    cfg=settings();public={**cfg,'new_key_configured':bool(os.environ.get('AIHUB_API_KEY')),
                          'legacy_asr_configured':bool(os.environ.get('APP_ID') and os.environ.get('APP_KEY'))}
    if args.check_config:
        print(json.dumps(public,ensure_ascii=False,indent=2));return
    if not os.environ.get('AIHUB_API_KEY'):
        raise SystemExit('Set AIHUB_API_KEY in your private .env before Gemini smoke testing')
    if args.video is None or args.work_dir is None or not args.video.is_file():
        ap.error('--video existing-file and --work-dir new-directory are required')
    if not math.isfinite(args.start) or args.start<0 or not math.isfinite(args.seconds) or not 1<=args.seconds<=120:
        ap.error('start must be nonnegative; seconds must be1..120')
    work=args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit('Use a new empty smoke directory; previous understanding is never overwritten')
    env=dict(os.environ);env['PATH']=str(ROOT/'tools')+os.pathsep+env.get('PATH','')
    work.mkdir(parents=True,exist_ok=True);clip=work/'input.mp4';analysis=work/'understanding'
    subprocess.run(['ffmpeg','-v','error','-n','-ss',str(args.start),'-i',str(args.video.resolve()),
                    '-t',str(args.seconds),'-map','0:v:0','-map','0:a:0?','-vf','scale=640:-2,fps=5',
                    '-c:v','libx264','-preset','veryfast','-crf','24','-c:a','aac',str(clip)],env=env,check=True)
    identity={'original_source':str(args.video.resolve()),'source_start':args.start,'requested_seconds':args.seconds,
              'clip_sha256':hashlib.sha256(clip.read_bytes()).hexdigest(),'analysis_clock':'clip_local',
              'original_time_equals':'clip_time + source_start','settings':cfg}
    (work/'source-map.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2)+'\n')
    command=[sys.executable,str(ROOT/'run_skill.py'),'video-understanding','understand.py',str(clip),
             '--work-dir',str(analysis),'--context','仅描述当前片段可见人物、行为与可听对白；未知身份保持未知。']
    if args.skip_asr:command.append('--skip-asr')
    env['UNDERSTANDING_PROVIDER']='aihub-gemini'
    subprocess.run(command,env=env,check=True)
    rows=json.loads((analysis/'vlm_analysis.json').read_text());meta=json.loads((analysis/'vlm_analysis.json.meta.json').read_text())
    if meta['settings']['vlm_model']!=cfg['model'] or not rows or not all(x.get('description') for x in rows):
        raise SystemExit('Understanding did not produce the expected Gemini scene artifacts')
    summary={'status':'passed','model':cfg['model'],'scene_count':len(rows),'asr_enabled':not args.skip_asr,
             'source_map':'source-map.json','scope':'Short-clip protocol/output check; not a full-film quality comparison.'}
    (work/'smoke-result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
