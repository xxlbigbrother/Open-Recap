"""ElevenLabs native timestamp TTS through the documented AIHub passthrough.

No direct ElevenLabs key, data_eval envelope, or automatic POST retries. The
caller loads its environment; credentials and the gateway come from AIHub.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
import wave

import requests

MODELS = ('eleven_v3', 'eleven_multilingual_v2', 'eleven_flash_v2_5')
TEXT_LIMITS = {'eleven_v3': 5000, 'eleven_multilingual_v2': 10000, 'eleven_flash_v2_5': 40000}
DEFAULT_MODEL = 'eleven_v3'
OUTPUT_FORMAT = 'mp3_44100_128'
VERSION = 1
DEFAULT_VOICE_SETTINGS = {
    'stability': .5, 'similarity_boost': .75, 'style': 0.0,
    'speed': 1.0, 'use_speaker_boost': True,
}
ALIGNMENT_TOLERANCE_SECONDS = .15
USAGE_FIELDS = {
    'characters', 'character_count', 'input_characters', 'output_characters',
    'input_tokens', 'output_tokens', 'prompt_tokens', 'completion_tokens',
    'total_tokens', 'credits', 'cost', 'total_cost',
}
USAGE_HEADERS = {
    'x-usage-cost', 'x-usage-prompt-tokens', 'x-usage-completion-tokens',
    'x-usage-total-tokens', 'x-usage-cache-hit-tokens', 'x-usage-cache-write-tokens',
    'x-usage-created-unix', 'x-usage-first-token-unix', 'x-usage-finished-unix',
}


def _finite_number(value):
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value))
    except OverflowError:
        return False


def tts_settings(*, speaker, model=DEFAULT_MODEL, voice_settings=None, language_code=None):
    """Validate and return all effective settings used for request/cache identity."""
    from aihub_adapter import BASE

    if not isinstance(speaker, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', speaker):
        raise ValueError('ElevenLabs speaker must be an explicit native voice ID')
    if model not in MODELS:
        raise ValueError('unsupported ElevenLabs native model')
    if language_code is not None:
        if not isinstance(language_code, str) or not re.fullmatch(r'[a-z]{2}', language_code):
            raise ValueError('ElevenLabs language_code must be a two-letter lowercase ISO 639-1 code')
        if model == 'eleven_multilingual_v2':
            raise ValueError('ElevenLabs multilingual v2 does not support language_code')
    if voice_settings is None:
        voice_settings = {}
    if not isinstance(voice_settings, dict) or voice_settings.keys() - DEFAULT_VOICE_SETTINGS.keys():
        raise ValueError('unsupported ElevenLabs voice_settings')
    effective = {**DEFAULT_VOICE_SETTINGS, **voice_settings}
    for name in ('stability', 'similarity_boost', 'style', 'speed'):
        low, high = (.7, 1.2) if name == 'speed' else (0, 1)
        value = effective[name]
        if not _finite_number(value) or not low <= value <= high:
            raise ValueError(f'ElevenLabs {name} must be finite in {low}..{high}')
        effective[name] = float(value)
    if model == 'eleven_v3' and effective['stability'] not in (0, .5, 1):
        raise ValueError('ElevenLabs v3 stability must be 0, .5 or 1')
    if not isinstance(effective['use_speaker_boost'], bool):
        raise ValueError('ElevenLabs use_speaker_boost must be boolean')
    # The verified v3 request omits this optional field. Preserve an explicit
    # boolean override; it is distinct from omission in the request and cache.
    if model == 'eleven_v3' and 'use_speaker_boost' not in voice_settings:
        del effective['use_speaker_boost']
    return {
        'provider': 'aihub-elevenlabs',
        'endpoint': BASE.rstrip('/') + f'/v1/text-to-speech/{speaker}/with-timestamps',
        'model': model, 'voice': speaker, 'voice_settings': effective, 'language_code': language_code,
        'output_format': OUTPUT_FORMAT, 'sample_rate': 44100,
        'channels': 1, 'pcm_format': 'pcm_s16le', 'adapter_version': VERSION,
    }


def _alignment(value, duration):
    """Keep provider character times in seconds on the unmodified audio clock."""
    if value is None:
        return None
    fields = ('characters', 'character_start_times_seconds', 'character_end_times_seconds')
    if not isinstance(value, dict) or any(not isinstance(value.get(k), list) for k in fields):
        raise RuntimeError('ElevenLabs returned malformed alignment')
    chars, starts, ends = (value[k] for k in fields)
    if len(chars) != len(starts) or len(chars) != len(ends):
        raise RuntimeError('ElevenLabs returned mismatched alignment arrays')
    previous_start = previous_end = 0.0
    for char, start, end in zip(chars, starts, ends):
        if (not isinstance(char, str) or not _finite_number(start) or not _finite_number(end)
                or not 0 <= start <= end <= duration + ALIGNMENT_TOLERANCE_SECONDS
                or start < previous_start or end < previous_end):
            raise RuntimeError('ElevenLabs returned invalid alignment times or characters')
        previous_start, previous_end = start, end
    return {key: value[key] for key in fields}


def _usage(value):
    """Only numeric billing counters; gateway request details never reach disk."""
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if key in USAGE_FIELDS and _finite_number(item)}


def _usage_headers(headers):
    """Persist only documented finite numeric billing headers, never raw headers."""
    result = {}
    for name, value in headers.items():
        name = name.lower()
        if name not in USAGE_HEADERS or not isinstance(value, str) or len(value) > 128:
            continue
        value = value.strip()
        if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
            continue
        try:
            number = float(value) if any(char in value for char in '.eE') else int(value)
        except (ValueError, OverflowError):
            continue
        if _finite_number(number):
            result[name] = number
    return result


def _request_id(value, secrets):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', value)
            or any(secret and secret in value for secret in secrets)):
        return None
    return value


def synthesize(text, output_wav, *, speaker, model=DEFAULT_MODEL, voice_settings=None,
               language_code=None) -> Path:
    """Write mono PCM WAV (44.1 kHz), return its allowlisted .provider.json path.

    Alignment may be absent/null. Present arrays must be valid, ordered, and
    within the decoded duration plus 150 ms MP3/provider timing tolerance.
    An ambiguous transport error is reported without retrying a billed POST.
    """
    from aihub_adapter import credentials

    settings = tts_settings(speaker=speaker, model=model, voice_settings=voice_settings,
                            language_code=language_code)
    if not isinstance(text, str) or not text.strip():
        raise ValueError('ElevenLabs text must be nonempty')
    if len(text) > TEXT_LIMITS[model]:
        raise ValueError(f'ElevenLabs {model} text exceeds {TEXT_LIMITS[model]} characters')
    app_id, key = credentials()
    cache_task_id = uuid.uuid4().hex
    token = (f'Bearer {app_id}:{key}?provider=elevenlabs&model={model}'
             f'&cache_task_id={cache_task_id}&timeout=120&usage=1')
    body = {'text': text, 'model_id': model, 'voice_settings': settings['voice_settings']}
    if language_code is not None:
        body['language_code'] = language_code
    try:
        response = requests.post(
            settings['endpoint'], params={'output_format': OUTPUT_FORMAT},
            headers={'Authorization': token},
            json=body,
            timeout=(10, 120), allow_redirects=False,
        )
    except requests.RequestException:
        raise RuntimeError('ElevenLabs AIHub transport failed; POST was not retried') from None
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f'ElevenLabs AIHub HTTP {response.status_code}; POST was not retried')
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError('ElevenLabs AIHub returned invalid JSON') from None
    if not isinstance(data, dict):
        raise RuntimeError('ElevenLabs AIHub returned a non-object response')
    encoded = data.get('audio_base64')
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError('ElevenLabs returned no audio_base64')
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise RuntimeError('ElevenLabs returned malformed audio_base64') from None
    if not audio:
        raise RuntimeError('ElevenLabs returned empty audio')

    output_wav = Path(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    sidecar = output_wav.with_suffix('.provider.json')
    with tempfile.TemporaryDirectory(prefix='.elevenlabs-', dir=output_wav.parent) as folder:
        temporary = Path(folder)
        mp3, wav = temporary / 'speech.mp3', temporary / 'speech.wav'
        mp3.write_bytes(audio)
        try:
            result = subprocess.run([
                'ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'mp3', '-i', str(mp3),
                '-map', '0:a:0', '-vn', '-ar', '44100', '-ac', '1', '-c:a', 'pcm_s16le', str(wav),
            ], capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('ElevenLabs MP3 to PCM conversion failed') from None
        if result.returncode:
            raise RuntimeError('ElevenLabs returned invalid MP3 audio')
        try:
            with wave.open(str(wav), 'rb') as decoded:
                if (decoded.getnchannels(), decoded.getsampwidth(), decoded.getframerate(),
                        decoded.getcomptype()) != (1, 2, 44100, 'NONE') or decoded.getnframes() <= 0:
                    raise ValueError('invalid PCM')
                duration = decoded.getnframes() / 44100
        except (OSError, EOFError, wave.Error, ValueError):
            raise RuntimeError('ElevenLabs conversion produced invalid PCM audio') from None
        aligned = _alignment(data.get('alignment'), duration)
        normalized = _alignment(data.get('normalized_alignment'), duration)
        # An upstream echo of authentication must never be saved as character data.
        for value in (aligned, normalized):
            if value and any(secret and secret in ''.join(value['characters']) for secret in (app_id, key)):
                raise RuntimeError('ElevenLabs returned unsafe alignment metadata')
        record = {
            'provider': settings['provider'], 'model': model, 'voice': speaker,
            'voice_settings': settings['voice_settings'], 'language_code': language_code, 'source_text': text,
            'output_format': OUTPUT_FORMAT, 'format': 'wav', 'sample_rate': 44100,
            'channels': 1, 'pcm_format': 'pcm_s16le', 'adapter_version': VERSION,
            'duration': duration, 'clock': 'raw_audio',
            'audio_sha256': hashlib.sha256(wav.read_bytes()).hexdigest(),
            'alignment': aligned, 'normalized_alignment': normalized,
            'usage': _usage(data.get('usage')),
            'usage_headers': _usage_headers(response.headers),
            'requestid': _request_id(data.get('requestid') or response.headers.get('request-id')
                                     or response.headers.get('x-request-id'), (app_id, key)),
            'cache_task_id': cache_task_id,
        }
        metadata = temporary / 'speech.provider.json'
        metadata.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                            encoding='utf-8')
        # Keep a previous complete pair recoverable if publication fails. The
        # audio hash also lets the consumer reject a process-interrupted pair.
        prior_wav, prior_metadata = temporary / 'previous.wav', temporary / 'previous.json'
        if output_wav.is_file():
            shutil.copy2(output_wav, prior_wav)
        if sidecar.is_file():
            shutil.copy2(sidecar, prior_metadata)
        try:
            wav.replace(output_wav)
            metadata.replace(sidecar)
        except OSError:
            if prior_wav.is_file():
                prior_wav.replace(output_wav)
            else:
                output_wav.unlink(missing_ok=True)
            if prior_metadata.is_file():
                prior_metadata.replace(sidecar)
            else:
                sidecar.unlink(missing_ok=True)
            raise
    return sidecar
