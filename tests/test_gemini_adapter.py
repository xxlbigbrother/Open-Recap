"""The configured understanding route preserves media and never borrows old credentials."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def module():
    path=ROOT/'gemini_adapter.py'
    assert path.exists(),'Gemini understanding adapter is not implemented'
    spec=importlib.util.spec_from_file_location('gemini_adapter_test',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_video_url_is_converted_and_unsupported_sampling_removed():
    m=module();payload={'model':'old','messages':[{'role':'user','content':[
      {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,aA=='}},
      {'type':'video_url','video_url':{'url':'data:video/mp4;base64,aA==','fps':2}},
      {'type':'text','text':'描述画面'}]}],'temperature':0,'top_p':.5,'thinking':{'type':'disabled'},
      'max_tokens':5,'max_completion_tokens':6000,'logprobs':True,'generationConfig':{'bad':True}}
    old=deepcopy(payload);out=m.prepare_payload(payload)
    assert payload==old
    assert out['model']=='gemini-3.8-flash'
    assert out['reasoning_effort']=='low'
    assert out['max_tokens']==6000 and 'max_completion_tokens' not in out
    assert not any(k in out for k in ['temperature','top_p','thinking','logprobs','generationConfig'])
    assert out['messages'][0]['content'][1]=={'type':'image_url','image_url':{'url':'data:video/mp4;base64,aA=='}}
    assert out['messages'][0]['content'][0]==old['messages'][0]['content'][0]

def test_default_minimum_output_budget_accounts_for_thinking():
    assert module().prepare_payload({'messages':[{'role':'user','content':'hi'}],'max_tokens':5})['max_tokens']==4096

def test_unsupported_media_is_rejected_instead_of_dropped():
    with pytest.raises(ValueError,match='content type'):
        module().prepare_payload({'messages':[{'role':'user','content':[{'type':'audio_url','url':'https://example.org/a.mp3'}]}]})

def test_missing_new_key_does_not_fall_back_to_legacy(monkeypatch):
    monkeypatch.delenv('AIHUB_API_KEY',raising=False);monkeypatch.setenv('APP_ID','old-id');monkeypatch.setenv('APP_KEY','old-key')
    with pytest.raises(RuntimeError,match='AIHUB_API_KEY'):module().chat({'messages':[{'role':'user','content':'hi'}]})

def test_chat_uses_only_new_key_and_omits_gateway_diagnostics(monkeypatch,tmp_path):
    m=module();monkeypatch.setenv('AIHUB_API_KEY','test-new-key');monkeypatch.setattr(m,'ROOT',tmp_path);calls=[]
    class Response:
        status_code=200
        def json(self):return {'model':'gemini-3.8-flash','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'{"ok":true}'}}],'usage':{'prompt_tokens':12},'request_detail':{'Authorization':'test-new-key'}}
    def request(url,**kwargs):calls.append((url,kwargs));return Response()
    monkeypatch.setattr(m.requests,'post',request)
    out=m.chat({'messages':[{'role':'user','content':'hi'}]})
    assert calls[0][0]=='http://api.aihub.woa.com/standard/v1/chat/completions'
    assert calls[0][1]['headers']['Authorization']=='Bearer test-new-key?timeout=120'
    assert out['choices'][0]['message']['content']=='{"ok":true}'
    assert 'request_detail' not in out
    assert 'test-new-key' not in (tmp_path/'reports/api-usage.jsonl').read_text()

def test_auth_failure_never_echoes_response_secret(monkeypatch):
    m=module();monkeypatch.setenv('AIHUB_API_KEY','top-secret')
    class Response:
        status_code=401
        text='top-secret'
    monkeypatch.setattr(m.requests,'post',lambda *a,**k:Response())
    with pytest.raises(RuntimeError,match='401') as error:m.chat({'messages':[{'role':'user','content':'hi'}]})
    assert 'top-secret' not in str(error.value)

def test_reasoning_and_configuration_are_separate_from_asr(monkeypatch):
    m=module();monkeypatch.setenv('AIHUB_API_KEY','new-key');monkeypatch.setenv('GEMINI_REASONING_EFFORT','high')
    config={'mimo_asr_model':'doubao-seed-asr-2.0','mimo_tts_model':'doubao-tts','mimo_asr_api_key':'legacy'}
    m.configure(config)
    assert config['vlm_model']=='gemini-3.8-flash'
    assert config['api_provider']=='aihub-gemini'
    assert config['mimo_asr_api_key']=='legacy' and config['mimo_asr_model']=='doubao-seed-asr-2.0'
    assert config['understanding_provider_settings']['reasoning_effort']=='high'
    monkeypatch.setenv('GEMINI_REASONING_EFFORT','none')
    with pytest.raises(ValueError,match='reasoning'):m.configure({})

def test_truncated_reasoning_only_response_does_not_become_scene_text(monkeypatch,tmp_path):
    m=module();monkeypatch.setenv('AIHUB_API_KEY','new');monkeypatch.setattr(m,'ROOT',tmp_path)
    class Response:
        status_code=200
        def json(self):return {'choices':[{'finish_reason':'length','message':{'content':'','reasoning_content':'internal analysis'}}]}
    monkeypatch.setattr(m.requests,'post',lambda *a,**k:Response())
    with pytest.raises(RuntimeError,match='truncated'):m.chat({'messages':[{'role':'user','content':'hi'}]})
