"""AIHub Doubao adapters for editorial review, ASR and speech synthesis."""
from __future__ import annotations
import base64
import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = Path(__file__).resolve().parents[1]
LLM_URL = "http://llm-api.model-eval.woa.com/v1/chat/completions"
LLM_MODEL = "api_doubao_doubao-seed-2-1-pro-260628"
BASE = "http://trpc-gpt-eval.production.polaris:8080"
ASR_MODEL = "doubao-seed-asr-2.0"
TTS_MODEL = "api_doubao_doubao-tts-2.0"
VOICE = "zh_female_vv_uranus_bigtts"
VERSION = 1
_LOCK = threading.Lock()


def credentials():
    app_id, key = os.environ.get("APP_ID"), os.environ.get("APP_KEY")
    if not app_id or not key:
        raise RuntimeError("Missing APP_ID / APP_KEY in the private local configuration")
    return app_id, key


def safe(value):
    text = str(value)
    for name in ("APP_ID", "APP_KEY"):
        if os.environ.get(name):
            text = text.replace(os.environ[name], "[REDACTED]")
    return text[:700]


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def record(capability, model, duration, response):
    # Deliberate allowlist: never store request_detail, media, authentication or headers.
    item = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "capability": capability, "model": model, "elapsed_seconds": round(duration, 3),
            "usage": response.get("usage"), "cost_info": response.get("cost_info")}
    with _LOCK:
        (ROOT / "reports").mkdir(parents=True, exist_ok=True)
        with (ROOT / "reports/api-usage.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _post_once(url, body, headers, timeout=180):
    try:
        response = requests.post(url, json=body, headers=headers, timeout=(10, timeout))
    except requests.RequestException as exc:
        raise RuntimeError(f"AIHub transport error: {type(exc).__name__}") from None
    if response.status_code >= 400:
        raise RuntimeError(f"AIHub HTTP {response.status_code}: {safe(response.text)}")
    try:
        return response, response.json()
    except ValueError:
        raise RuntimeError("AIHub returned a non-JSON response") from None


def post(url, body, headers, timeout=180):
    for attempt in range(4):
        try:
            return _post_once(url, body, headers, timeout)
        except RuntimeError as exc:
            message = str(exc)
            if not any(code in message for code in ("HTTP 502", "HTTP 503", "HTTP 504", "transport error")) or attempt == 3:
                raise
            print(f"[aihub] transient error; retry {attempt+1}/3", flush=True)
            time.sleep(3 * (attempt + 1))


def chat(payload, **kwargs):
    app_id, key = credentials()
    body = dict(payload)
    body["model"] = LLM_MODEL
    body.pop("max_completion_tokens", None)
    body.setdefault("max_tokens", 4096)
    body["thinking"] = {"type": "disabled"}
    start = time.monotonic()
    for attempt in range(3):
        try:
            _, result = post(LLM_URL, body, {"Authorization": f"Bearer {app_id}:{key}"})
            break
        except RuntimeError as exc:
            if "HTTP 40" in str(exc) or attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    record("llm_vision", LLM_MODEL, time.monotonic() - start, result)
    if not result.get("choices"):
        raise RuntimeError("AIHub chat response is missing choices")
    return result


def asr_settings():
    return {"provider": "aihub-doubao", "model": ASR_MODEL, "model_version": "400",
            "endpoint": BASE, "adapter_version": VERSION, "audio_stream": "0:a:0"}


def normalize_asr(raw):
    utterances = raw.get("result", {}).get("utterances", [])
    rows = []
    for u in utterances:
        text = str(u.get("text") or "").strip()
        if not text:
            continue
        start, end = float(u["start_time"]) / 1000, float(u["end_time"]) / 1000
        if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
            raise ValueError("Invalid ASR utterance time")
        words = []
        for w in u.get("words", []):
            a, b = float(w["start_time"]) / 1000, float(w["end_time"]) / 1000
            if not all(math.isfinite(v) for v in (a, b)) or b < a:
                raise ValueError("Invalid ASR word time")
            words.append({"text": w.get("text", ""), "start": a, "end": b})
        rows.append({"start": start, "end": end, "text": text, "words": words,
                     "speaker": (u.get("additions") or {}).get("speaker")})
    return rows


def transcribe_wav(wav, raw_path):
    app_id, key = credentials()
    wav, raw_path = Path(wav), Path(raw_path)
    fingerprint = hashlib.sha256(wav.read_bytes()).hexdigest()
    identity = {"audio_sha256": fingerprint, **asr_settings()}
    state_path = raw_path.with_suffix(".task.json")
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get("identity") == identity and state.get("complete") and raw_path.exists():
        return json.loads(raw_path.read_text())
    token = f"{app_id}:{key}?provider=doubao&model={ASR_MODEL}&api=openspeech_api&timeout=300"
    start = time.monotonic()
    if state.get("identity") != identity or not state.get("request_id"):
        body = {"user": {"uid": "openrecap"},
                "audio": {"data": base64.b64encode(wav.read_bytes()).decode(), "format": "wav"},
                "request": {"model_name": "bigmodel", "model_version": "400",
                            "enable_speaker_info": True, "show_utterances": True,
                            "enable_punc": True, "enable_itn": True}}
        res, data = post(BASE + "/api/v3/auc/bigmodel/submit", body,
                         {"Authorization": "Bearer " + token, "X-Api-Resource-Id": "volc.seedasr.auc"})
        if res.headers.get("X-Api-Status-Code") != "20000000":
            raise RuntimeError("ASR submit failed: " + safe(res.headers.get("X-Api-Message")))
        state = {"identity": identity, "request_id": res.headers["X-Request-Id"],
                 "account_id": res.headers.get("X-Account-Id"), "complete": False}
        atomic_json(state_path, state)
    query_token = token + "?&request_id=" + state["request_id"]
    if state.get("account_id"):
        query_token += "&account_id=" + state["account_id"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        res, data = post(BASE + "/api/v3/auc/bigmodel/query", {},
                         {"Authorization": "Bearer " + query_token, "X-Api-Resource-Id": "volc.seedasr.auc"})
        code = res.headers.get("X-Api-Status-Code")
        if code == "20000000":
            # ASR response allowlist retains words without gateway request metadata.
            raw = {k: data[k] for k in ("audio_info", "result") if k in data}
            normalize_asr(raw)
            atomic_json(raw_path, raw)
            state["complete"] = True
            atomic_json(state_path, state)
            record("asr", ASR_MODEL, time.monotonic() - start, data)
            return raw
        if code not in ("20000001", "20000002"):
            raise RuntimeError(f"ASR query failed: {code}; {safe(res.headers.get('X-Api-Message'))}")
        time.sleep(3)
    raise RuntimeError("ASR query timed out; task ID saved for resume")


def transcribe_video(video, work_dir):
    work_dir = Path(work_dir)
    wav = work_dir / "audio.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-map", "0:a:0",
                    "-vn", "-ar", "16000", "-ac", "1", str(wav)], check=True)
    # Reuse upstream source fingerprint contract for sentence-boundary detection.
    from asr import _write_audio_meta
    _write_audio_meta(work_dir, video)
    duration = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                     "format=duration", "-of", "csv=p=0", str(wav)]))
    if duration <= 600:
        raw = transcribe_wav(wav, work_dir / "doubao_asr_raw.json")
        rows = normalize_asr(raw)
    else:
        folder = work_dir / "asr_chunks"
        folder.mkdir(exist_ok=True)
        ranges = [(i, float(a), min(float(a + 600), duration)) for i, a in enumerate(range(0, math.ceil(duration), 600))]
        def one(item):
            i, a, b = item
            start, end = max(0, a - 1.5), min(duration, b + 1.5)
            part = folder / f"chunk_{i:03d}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(wav),
                            "-t", str(end - start), "-c:a", "pcm_s16le", str(part)], check=True)
            raw = transcribe_wav(part, folder / f"chunk_{i:03d}.json")
            normalized = []
            for row in normalize_asr(raw):
                row = offset_asr(row, start, i)
                midpoint = (row["start"] + row["end"]) / 2
                if a <= midpoint < b:
                    normalized.append(row)
            print(f"[aihub-doubao] ASR chunk {i+1}/{len(ranges)} ready ({len(normalized)} utterances)", flush=True)
            return i, normalized
        results = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            for fut in as_completed([pool.submit(one, item) for item in ranges]):
                i, values = fut.result()
                results[i] = values
        rows = [row for i in sorted(results) for row in results[i]]
        atomic_json(work_dir / "doubao_asr_chunks.json", {"duration": duration, "chunk_seconds": 600,
                    "overlap_context_seconds": 1.5, "speaker_scope": "per_chunk", "chunks": len(ranges)})
    atomic_json(work_dir / "asr_result.json", rows)
    print(f"[aihub-doubao] ASR: {len(rows)} utterances; {sum(len(x['words']) for x in rows)} word timestamps", flush=True)
    return rows


def offset_asr(row, start, chunk_index):
    """Move utterance and word anchors together; do not merge anonymous chunk speakers."""
    return {**row, "start": round(row["start"] + start, 3), "end": round(row["end"] + start, 3),
            "speaker": f"chunk{chunk_index}:{row['speaker']}" if row.get("speaker") is not None else None,
            "words": [{**w, "start": round(w["start"] + start, 3), "end": round(w["end"] + start, 3)} for w in row["words"]]}


def tts_settings():
    return {"provider": "aihub-doubao", "endpoint": BASE + "/api/v1/data_eval",
            "model": TTS_MODEL, "voice": os.environ.get("DOUBAO_TTS_VOICE", VOICE),
            "format": "mp3", "sample_rate": 24000, "bit_rate": 128000,
            "adapter_version": VERSION}


def synthesize(text, output_wav, rate="+0%", **kwargs):
    app_id, key = credentials()
    settings = tts_settings()
    source = "video-recap-doubao"
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    sign = base64.b64encode(hmac.new(key.encode(), f"date: {date}\nsource: {source}".encode(), hashlib.sha1).digest()).decode()
    headers = {"Apiversion": "v2.03", "Date": date, "Source": source,
               "Authorization": f'hmac id="{app_id}", algorithm="hmac-sha1", headers="date source", signature="{sign}"'}
    speed = round(float(rate.rstrip("%")))
    body = {"request_id": str(uuid.uuid4()), "model_marker": TTS_MODEL,
            "messages": [{"content": [{"type": "text", "value": text}]}], "timeout": 180000,
            "params": {"speaker": settings["voice"], "resource_id": "seed-tts-2.0",
                       "format": "mp3", "sample_rate": 24000, "bit_rate": 128000,
                       "speech_rate": speed, "loudness_rate": 0}}
    start = time.monotonic()
    _, data = post(settings["endpoint"], body, headers)
    record("tts", TTS_MODEL, time.monotonic() - start, data)
    if data.get("code") != 0:
        raise RuntimeError("Doubao TTS failed: " + safe(data.get("msg")))
    audio = b"".join(base64.b64decode(a["value"], validate=True) for a in data.get("answer", []) if a.get("type") == "audio_base64")
    if not audio:
        raise RuntimeError("Doubao TTS returned no audio")
    output_wav = Path(output_wav)
    mp3 = output_wav.with_suffix(".source.mp3")
    mp3.write_bytes(audio)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", "44100", "-ac", "1",
                    "-c:a", "pcm_s16le", str(output_wav)], check=True)
    atomic_json(output_wav.with_suffix(".provider.json"), {**settings, "speech_rate": speed,
                "source_text": text, "timestamp_status": "not_returned_by_gateway",
                "usage": data.get("usage"), "cost_info": data.get("cost_info")})


def configure(config):
    app_id, key = os.environ.get('APP_ID',''), os.environ.get('APP_KEY','')
    token = app_id + ":" + key if app_id and key else ''
    config.update({"api_provider": "aihub-doubao", "api_key": token, "api_key_source": "APP_ID/APP_KEY",
                   "api_url": LLM_URL, "api_url_source": "AIHub adapter", "vlm_model": LLM_MODEL,
                   "mimo_model": LLM_MODEL, "mimo_video_model": LLM_MODEL,
                   "mimo_api_url": LLM_URL, "mimo_video_api_url": LLM_URL,
                   "mimo_api_key": token, "mimo_video_api_key": token,
                   "mimo_asr_api_key": token, "mimo_asr_api_key_source": "APP_ID/APP_KEY",
                   "mimo_asr_api_url": BASE + "/api/v3/auc/bigmodel/submit", "mimo_asr_model": ASR_MODEL,
                   "mimo_tts_api_key": token, "mimo_tts_api_key_source": "APP_ID/APP_KEY",
                   "mimo_tts_api_url": BASE + "/api/v1/data_eval", "mimo_tts_model": TTS_MODEL,
                   "mimo_tts_voice": os.environ.get("DOUBAO_TTS_VOICE", VOICE),
                   "tts_dynamic_params": False, "vlm_model_source": "AIHub adapter",
                   "mimo_tts_model_source": "AIHub adapter", "mimo_tts_voice_source": "AIHub adapter"})
