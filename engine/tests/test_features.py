import numpy as np
from fastapi.testclient import TestClient

from app.audio.pulse_audio import resample_audio
from app.audio.vad import StreamingVAD
from app.core.history import HistoryStore
from app.main import app


def test_bandlimited_resampler_preserves_dc_and_smooth_sine():
    dc = np.ones(320, dtype=np.float32)
    up_dc = resample_audio(dc, 16000, 22050)
    assert len(up_dc) == 441
    assert np.allclose(up_dc, 1.0, atol=1e-4)

    t = np.linspace(0, 0.1, 2205, endpoint=False)
    sine = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    up_sine = resample_audio(sine, 22050, 48000)
    assert len(up_sine) == 4800
    # Interior peak should stay close to 1.0 without overshoot
    assert 0.95 <= float(np.max(np.abs(up_sine[200:-200]))) <= 1.05


def test_vad_highpass_attenuates_low_hum_and_updates_aggressiveness():
    vad = StreamingVAD(sample_rate=16000, frame_duration_ms=20, aggressiveness=2, enable_highpass=True)
    frame_samples = 320
    t = np.linspace(0, 0.02, frame_samples, endpoint=False)
    hum_20hz = (np.sin(2 * np.pi * 20 * t) * 2000).astype(np.int16).tobytes()
    # Feed a few frames to settle filter
    for _ in range(5):
        vad.feed(hum_20hz)
    assert vad.last_rms < 700.0

    vad.configure(aggressiveness=3)
    assert vad.aggressiveness == 3


def test_history_store_and_api_roundtrip(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "history.db")
    monkeypatch.setattr("app.main.history_store", store)

    store.add(
        {
            "id": "u1",
            "ts_ms": 1000,
            "channel": "outgoing",
            "src_lang": "id",
            "dst_lang": "en",
            "source_text": "Halo Kubernetes",
            "translated_text": "Hello Kubernetes",
            "latency_ms": 120,
        }
    )
    store.add(
        {
            "id": "u2",
            "ts_ms": 2000,
            "channel": "incoming",
            "src_lang": "en",
            "dst_lang": "id",
            "source_text": "Server is ready",
            "translated_text": "Server sudah siap",
            "latency_ms": 95,
        }
    )

    client = TestClient(app)
    res_all = client.get("/history")
    assert res_all.status_code == 200
    items = res_all.json()["items"]
    assert [it["id"] for it in items] == ["u1", "u2"]

    res_search = client.get("/history", params={"q": "Kubernetes"})
    assert res_search.status_code == 200
    assert [it["id"] for it in res_search.json()["items"]] == ["u1"]

    res_export = client.get("/history/export")
    assert res_export.status_code == 200
    assert "Hello Kubernetes" in res_export.text
    assert "Server sudah siap" in res_export.text

    res_del = client.delete("/history")
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] == 2
    assert client.get("/history").json()["items"] == []
