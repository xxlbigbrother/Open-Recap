"""Entrypoint routing stays stage-specific; no real service is contacted."""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]

def config(stage,provider=None):
    env=dict(os.environ,VIDEO_RECAP_PROVIDER='aihub-doubao',APP_ID='legacy',APP_KEY='legacy-secret',AIHUB_API_KEY='new-key')
    env.pop('UNDERSTANDING_PROVIDER',None)
    if provider:env['UNDERSTANDING_PROVIDER']=provider
    code="import json,lib; out={k:lib.CONFIG.get(k) for k in ['vlm_model','mimo_asr_model','api_provider']}; out['asr_key_present']=bool(lib.CONFIG.get('mimo_asr_api_key')); print(json.dumps(out)); print(lib.api_call.__module__)"
    env['PYTHONPATH']=str(ROOT/'skills'/stage/'scripts')+os.pathsep+str(ROOT/"scripts")
    p=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,check=True)
    rows=p.stdout.strip().splitlines();return json.loads(rows[-2]),rows[-1]

def test_understanding_defaults_to_gemini_without_changing_editorial():
    first,client=config('video-understanding')
    assert first['vlm_model']=='gemini-3.8-flash'
    assert first['mimo_asr_model']=='doubao-seed-asr-2.0'
    assert first['asr_key_present'] is True
    assert client=='gemini_adapter'
    second,client=config('video-script')
    assert second['vlm_model']=='api_doubao_doubao-seed-2-1-pro-260628'
    assert client=='aihub_adapter'

def test_previous_doubao_understanding_remains_explicitly_selectable():
    first,client=config('video-understanding','aihub-doubao')
    assert first['vlm_model']=='api_doubao_doubao-seed-2-1-pro-260628' and client=='aihub_adapter'


def test_explicit_missing_env_path_fails_before_running_stage(tmp_path):
    env=dict(os.environ,RECAP_ENV_FILE=str(tmp_path/'missing.env'))
    p=subprocess.run([sys.executable,str(ROOT/'scripts/run_skill.py'),'video-understanding','understand.py','--help'],
        env=env,capture_output=True,text=True)
    assert p.returncode!=0
    assert 'RECAP_ENV_FILE' in p.stderr
