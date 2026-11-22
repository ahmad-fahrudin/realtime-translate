import json
import numpy as np
from fastapi.testclient import TestClient

from app.main import app, engine
from app.pipeline import TranslationOutput
from app.pulse_audio import AudioRingBuffer, resample_linear
from app.vad import StreamingVAD


def test_vad_ptt_collects_and_flushes():
    vad = StreamingVAD(sample_rate=16000, frame_duration_ms=20, min_speech_ms=100)
    vad.set_mode("ptt")
    vad.set_ptt(True)

    t = np.linspace(0, 0.2, 3200, endpoint=False)
    pcm16 = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16).tobytes()
    assert vad.feed(pcm16) == []

    utterance = vad.set_ptt(False)
    assert utterance is not None
    assert utterance.dtype == np.float32
    assert len(utterance) == 3200


def test_vad_ignores_silence_in_auto_mode():
    vad = StreamingVAD(sample_rate=16000, frame_duration_ms=20, energy_threshold=400.0)
    silence = np.zeros(1600, dtype=np.int16).tobytes()
    assert vad.feed(silence) == []


def test_ring_buffer_and_resample():
    buf = AudioRingBuffer(max_samples=100)
    buf.push(np.ones(40, dtype=np.float32))
    out, has_audio = buf.pull(60)
    assert has_audio is True
    assert len(out) == 60
    assert np.allclose(out[:40], 1.0)
    assert np.allclose(out[40:], 0.0)

    up = resample_linear(np.ones(160, dtype=np.float32), 16000, 48000)
    assert len(up) == 480


def test_web_ui_and_api_endpoints():
    client = TestClient(app)
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "Realtime Translate" in res_root.text

    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"

    res_langs = client.get("/languages")
    assert res_langs.status_code == 200
    codes = {item["code"] for item in res_langs.json()["languages"]}
    assert {"id", "en"}.issubset(codes)


def test_websocket_test_text_roundtrip(monkeypatch):
    def fake_process_text(text, channel, src_lang, dst_lang, tts_enabled=True):
        return TranslationOutput(
            utterance_id="abc123",
            channel=channel,
            src_lang=src_lang,
            dst_lang=dst_lang,
            source_text=text,
            translated_text="Hello, how are you?",
            latency_ms=42,
            tts_pcm16=b"\x01\x00\x02\x00",
            tts_sample_rate=22050,
        )

    monkeypatch.setattr(engine, "process_text", fake_process_text)

    client = TestClient(app)
    with client.websocket_connect("/ws/translate?channel=outgoing&src_lang=id&dst_lang=en") as ws:
        ws.send_text(json.dumps({"type": "test_text", "text": "Halo, apa kabar?"}))
        tr_msg = json.loads(ws.receive_text())
        assert tr_msg["type"] == "translation"
        assert tr_msg["translated_text"] == "Hello, how are you?"

        meta_msg = json.loads(ws.receive_text())
        assert meta_msg["type"] == "audio_meta"
        assert meta_msg["sample_rate"] == 22050

        audio_bytes = ws.receive_bytes()
        assert audio_bytes == b"\x01\x00\x02\x00"
