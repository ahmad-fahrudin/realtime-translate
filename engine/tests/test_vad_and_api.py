import json
import numpy as np
from fastapi.testclient import TestClient

from app.ai.pipeline import TranslationOutput
from app.audio.pulse_audio import AudioRingBuffer, resample_linear
from app.audio.vad import StreamingVAD
from app.main import app, engine


def test_vad_ptt_collects_and_flushes():
    vad = StreamingVAD(sample_rate=16000, frame_duration_ms=20, min_speech_ms=100, max_utterance_ms=200)
    vad.set_mode("ptt")
    vad.set_ptt(True)

    # Feed 400ms (> max_utterance_ms=200ms) while still holding PTT: must NOT emit early
    t = np.linspace(0, 0.4, 6400, endpoint=False)
    pcm16 = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16).tobytes()
    assert vad.feed(pcm16) == []

    utterance = vad.set_ptt(False)
    assert utterance is not None
    assert utterance.dtype == np.float32
    assert len(utterance) == 6400


def test_vad_ignores_silence_in_auto_mode():
    vad = StreamingVAD(sample_rate=16000, frame_duration_ms=20, energy_threshold=400.0)
    silence = np.zeros(1600, dtype=np.int16).tobytes()
    assert vad.feed(silence) == []


def test_vad_rejects_short_noise_spike_and_emits_real_speech():
    vad = StreamingVAD(
        sample_rate=16000,
        frame_duration_ms=20,
        energy_threshold=350.0,
        silence_ms=200,
        min_speech_ms=160,
        pre_roll_ms=100,
        post_roll_ms=60,
    )
    frame_samples = 320
    t = np.linspace(0, 0.02, frame_samples, endpoint=False)
    voiced = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16).tobytes()
    silent = np.zeros(frame_samples, dtype=np.int16).tobytes()

    # A single 20ms click followed by silence must NOT emit an utterance
    res_spike = vad.feed(voiced + silent * 15)
    utterances_spike = [r.utterance for r in res_spike if r.utterance is not None]
    assert utterances_spike == []

    # Real speech (10 voiced frames = 200ms >= 160ms) followed by 200ms silence MUST emit
    res_real = vad.feed(voiced * 10 + silent * 12)
    utterances_real = [r.utterance for r in res_real if r.utterance is not None]
    assert len(utterances_real) == 1
    assert utterances_real[0].dtype == np.float32


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

    res_whisper = client.get("/models/whisper")
    assert res_whisper.status_code == 200
    whisper_ids = {item["id"] for item in res_whisper.json()["models"]}
    assert {"tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"}.issubset(whisper_ids)

    res_voices = client.get("/voices")
    assert res_voices.status_code == 200
    voices_by_lang = res_voices.json()["voices"]
    assert len(voices_by_lang["en"]) >= 2
    assert {v["gender"] for v in voices_by_lang["en"]} == {"male", "female"}
    assert {v["gender"] for v in voices_by_lang["id"]} == {"male", "female"}


def test_switch_whisper_model_endpoint(monkeypatch):
    def fake_switch(model_name: str, cancel_event=None, on_progress=None):
        if on_progress:
            on_progress(50, 100, "model.bin")
        return {
            "active_model": model_name,
            "active_device": "cuda",
            "installed_models": ["tiny", "base", "small"],
        }

    monkeypatch.setattr(engine.stt, "switch_model", fake_switch)

    client = TestClient(app)
    res = client.post("/models/whisper", json={"model": "base"})
    assert res.status_code == 200
    assert res.json()["active_model"] == "base"
    assert res.json()["active_device"] == "cuda"

    res_cancel_w = client.post("/models/whisper/cancel")
    assert res_cancel_w.status_code == 200
    assert res_cancel_w.json()["ok"] is True

    res_cancel_l = client.post("/models/prepare/cancel")
    assert res_cancel_l.status_code == 200
    assert res_cancel_l.json()["ok"] is True


def test_download_cancellation_cleans_temp_file(tmp_path):
    import threading
    import httpx
    from app.core.downloader import DownloadCancelled, stream_download_file

    cancel_ev = threading.Event()
    cancel_ev.set()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1024, headers={"content-length": "1024"})

    transport = httpx.MockTransport(handler)
    dest = tmp_path / "model.bin"
    with httpx.Client(transport=transport) as client:
        try:
            stream_download_file(client, "https://example.com/model.bin", dest, cancel_event=cancel_ev)
            assert False, "Expected DownloadCancelled"
        except DownloadCancelled:
            pass

    assert not dest.exists()
    assert not (tmp_path / "model.bin.tmp").exists()


def test_whisper_model_selection_persists(tmp_path, monkeypatch):
    from app.ai.stt import FasterWhisperSTT
    from app.core.config import settings

    monkeypatch.setattr(settings, "models_dir", str(tmp_path))
    monkeypatch.setattr(settings, "whisper_model", "small")

    stt_1 = FasterWhisperSTT()
    assert stt_1.active_model_name == "small"
    stt_1._save_active_model_name("base")

    stt_2 = FasterWhisperSTT()
    assert stt_2.active_model_name == "base"


def test_ui_prefs_roundtrip_and_vad_configure(tmp_path, monkeypatch):
    from app.audio.vad import StreamingVAD
    from app.core.config import settings

    monkeypatch.setattr(settings, "models_dir", str(tmp_path))

    client = TestClient(app)
    res_post = client.post(
        "/prefs",
        json={
            "my_lang": "en",
            "peer_lang": "id",
            "outgoing_tts_enabled": False,
            "incoming_tts_enabled": True,
            "tts_speed": 1.2,
            "vad_energy_threshold": 500,
            "vad_silence_ms": 900,
            "peer_auto_detect": True,
            "translator_backend": "argos",
        },
    )
    assert res_post.status_code == 200
    assert res_post.json()["translator_backend"] == "argos"

    res_get = client.get("/prefs")
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["my_lang"] == "en"
    assert data["outgoing_tts_enabled"] is False
    assert data["incoming_tts_enabled"] is True
    assert data["tts_speed"] == 1.2
    assert data["peer_auto_detect"] is True

    # Restore backend to nllb so singleton isn't left on argos
    client.post("/prefs", json={"translator_backend": "nllb"})

    vad = StreamingVAD()
    vad.configure(energy_threshold=500.0, silence_ms=900)
    assert vad.energy_threshold == 500.0
    assert vad.silence_frames_limit == 45


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
