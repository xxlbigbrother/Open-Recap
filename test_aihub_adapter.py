import pytest
import aihub_adapter as adapter


def test_asr_preserves_real_word_times_and_unknown_speaker():
    raw = {"result": {"utterances": [{"start_time": 1234, "end_time": 2400, "text": "你好",
           "words": [{"text": "你", "start_time": 1234, "end_time": 1555}]}]}}
    row = adapter.normalize_asr(raw)[0]
    assert row["start"] == 1.234
    assert row["words"][0] == {"text": "你", "start": 1.234, "end": 1.555}
    assert row["speaker"] is None


def test_asr_rejects_reversed_interval():
    with pytest.raises(ValueError):
        adapter.normalize_asr({"result": {"utterances": [{"text": "错", "start_time": 5, "end_time": 4}]}})


def test_chunk_offset_preserves_word_alignment_and_speaker_scope():
    row = {"start": 1.25, "end": 2.5, "text": "你好", "speaker": "1",
           "words": [{"text": "你", "start": 1.25, "end": 1.5}]}
    shifted = adapter.offset_asr(row, 598.5, 1)
    assert shifted["start"] == 599.75
    assert shifted["words"][0]["start"] == 599.75
    assert shifted["speaker"] == "chunk1:1"
    assert row["words"][0]["start"] == 1.25


def test_chat_uses_aihub_bearer_and_real_model(monkeypatch):
    monkeypatch.setenv("APP_ID", "test-app")
    monkeypatch.setenv("APP_KEY", "test-secret")
    calls = []
    def fake(url, body, headers, timeout=180):
        calls.append((url, body, headers))
        return None, {"choices": [{"message": {"content": "ok"}}]}
    monkeypatch.setattr(adapter, "post", fake)
    monkeypatch.setattr(adapter, "record", lambda *a: None)
    adapter.chat({"model": "mimo-v2.5", "messages": [{"role": "user", "content": "x"}], "max_tokens": 99})
    url, body, headers = calls[0]
    assert url == adapter.LLM_URL
    assert body["model"] == adapter.LLM_MODEL
    assert body["max_tokens"] == 99
    assert headers["Authorization"] == "Bearer test-app:test-secret"


def test_completed_asr_cache_makes_no_network_call(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ID", "test-app")
    monkeypatch.setenv("APP_KEY", "test-secret")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"same-audio")
    raw_path = tmp_path / "asr.json"
    raw = {"result": {"utterances": []}}
    adapter.atomic_json(raw_path, raw)
    identity = {"audio_sha256": adapter.hashlib.sha256(wav.read_bytes()).hexdigest(), **adapter.asr_settings()}
    adapter.atomic_json(raw_path.with_suffix(".task.json"), {"identity": identity, "complete": True, "request_id": "task1"})
    monkeypatch.setattr(adapter, "post", lambda *a, **k: pytest.fail("cache must avoid network"))
    assert adapter.transcribe_wav(wav, raw_path) == raw
