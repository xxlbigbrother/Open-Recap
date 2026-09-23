"""Gemini understanding through AIHub's standard Chat Completions protocol.

This module does not route editorial writing, speech recognition, or speech synthesis.
"""
from copy import deepcopy
import datetime
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parent
_LOCK = threading.Lock()
DEFAULT_URL = 'http://api.aihub.woa.com/standard/v1/chat/completions'
DEFAULT_MODEL = 'gemini-3.8-flash'


def settings():
    url = os.environ.get('GEMINI_API_URL', DEFAULT_URL).strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('GEMINI_API_URL must be an HTTP(S) endpoint without embedded credentials')
    model = os.environ.get('GEMINI_MODEL', DEFAULT_MODEL).strip()
    if not model:
        raise ValueError('GEMINI_MODEL must be nonempty')
    reasoning = os.environ.get('GEMINI_REASONING_EFFORT', 'low').strip()
    if reasoning not in {'low', 'medium', 'high'}:
        raise ValueError('Gemini reasoning must be low, medium, or high')
    minimum = int(os.environ.get('GEMINI_MIN_OUTPUT_TOKENS', '4096'))
    timeout = int(os.environ.get('GEMINI_TIMEOUT_SECONDS', '120'))
    if not 1 <= minimum <= 65536 or not 1 <= timeout <= 3600:
        raise ValueError('Gemini output budget or timeout outside documented range')
    return {'provider': 'aihub-gemini', 'model': model, 'api_url': url,
            'reasoning_effort': reasoning, 'min_output_tokens': minimum,
            'timeout_seconds': timeout, 'adapter_version': 1,
            'media_transport': 'image_url accepts image/video data URIs; source fps is encoded before upload'}


def prepare_payload(payload):
    cfg = settings()
    # Explicit allowlist keeps unsupported vendor parameters out of this route.
    body = {k: deepcopy(v) for k, v in payload.items() if k in {
        'messages', 'response_format', 'stop', 'seed', 'tools', 'tool_choice',
        'web_search_options', 'prompt_cache_key'}}
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages:
        raise ValueError('Gemini messages must be a nonempty list')
    for message in messages:
        content = message.get('content')
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise ValueError('Gemini content must be text or media parts')
        for index, part in enumerate(content):
            kind = part.get('type')
            if kind == 'video_url':
                media = part.get('video_url', {})
                url = media.get('url') if isinstance(media, dict) else media
                if not isinstance(url, str) or not url:
                    raise ValueError('video_url requires a nonempty url')
                content[index] = {'type': 'image_url', 'image_url': {'url': url}}
            elif kind not in {'text', 'image_url', 'input_audio', 'file'}:
                raise ValueError(f'Unsupported Gemini content type: {kind}')
    budget = payload.get('max_completion_tokens', payload.get('max_tokens', cfg['min_output_tokens']))
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 65536:
        raise ValueError('Gemini output token budget must be in1..65536')
    body.update(model=cfg['model'], reasoning_effort=cfg['reasoning_effort'],
                max_tokens=max(budget, cfg['min_output_tokens']), stream=False)
    return body


def _record(cfg, result, elapsed):
    folder = ROOT / 'reports'
    folder.mkdir(parents=True, exist_ok=True)
    row = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'capability': 'understanding', 'model': cfg['model'],
           'provider': cfg['provider'], 'elapsed_seconds': round(elapsed, 3),
           'usage': result.get('usage')}
    with _LOCK, (folder / 'api-usage.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def chat(payload, **kwargs):
    cfg = settings()
    key = os.environ.get('AIHUB_API_KEY', '').strip()
    if not key:
        raise RuntimeError('Missing AIHUB_API_KEY for Gemini; legacy APP_ID/APP_KEY are not used')
    body = prepare_payload(payload)
    headers = {'Authorization': f'Bearer {key}?timeout={cfg["timeout_seconds"]}',
               'Content-Type': 'application/json'}
    started = time.monotonic()
    for attempt in range(3):
        try:
            response = requests.post(cfg['api_url'], json=body, headers=headers,
                                     timeout=(10, cfg['timeout_seconds'] + 15))
        except requests.RequestException:
            if attempt == 2:
                raise RuntimeError('Gemini transport failed after3 attempts') from None
            time.sleep(2 ** attempt)
            continue
        if response.status_code in {429, 502, 503, 504} and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        if response.status_code >= 400:
            raise RuntimeError(f'Gemini AIHub HTTP {response.status_code}; check API key and model access')
        try:
            result = response.json()
        except ValueError:
            raise RuntimeError('Gemini returned invalid JSON') from None
        if not isinstance(result, dict) or not result.get('choices'):
            raise RuntimeError('Gemini response missing choices')
        choices = result['choices']
        if any(choice.get('finish_reason') == 'length' for choice in choices):
            raise RuntimeError('Gemini output truncated; increase output budget before retrying')
        cleaned = []
        for choice in choices:
            message = choice.get('message') or {}
            content = message.get('content')
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError('Gemini returned no final text; reasoning is not scene evidence')
            cleaned.append({'index': choice.get('index', 0), 'finish_reason': choice.get('finish_reason'),
                            'message': {'role': 'assistant', 'content': content}})
        _record(cfg, result, time.monotonic() - started)
        return {'model': result.get('model', cfg['model']), 'choices': cleaned,
                'usage': result.get('usage', {})}
    raise RuntimeError('Gemini request did not complete')


def configure(config):
    cfg = settings()
    key = os.environ.get('AIHUB_API_KEY', '').strip()
    config.update(api_provider='aihub-gemini', api_url=cfg['api_url'], api_key=key,
                  api_key_source='AIHUB_API_KEY', api_url_source='Gemini adapter',
                  vlm_model=cfg['model'], vlm_model_source='Gemini adapter',
                  mimo_model=cfg['model'], mimo_video_model=cfg['model'],
                  mimo_api_url=cfg['api_url'], mimo_video_api_url=cfg['api_url'],
                  mimo_api_key=key, mimo_video_api_key=key,
                  mimo_disable_thinking=False,
                  understanding_provider_settings=cfg)
    # ASR continues through its own adapter; do not hand the Gemini key to it.
    from aihub_adapter import ASR_MODEL, BASE
    legacy = ':'.join([os.environ.get('APP_ID', ''), os.environ.get('APP_KEY', '')])
    config.update(mimo_asr_model=ASR_MODEL, mimo_asr_api_url=BASE + '/api/v3/auc/bigmodel/submit')
    if not config.get('mimo_asr_api_key'):
        config['mimo_asr_api_key'] = legacy if all(legacy.split(':', 1)) else ''
    config['mimo_asr_api_key_source'] = 'APP_ID/APP_KEY'
