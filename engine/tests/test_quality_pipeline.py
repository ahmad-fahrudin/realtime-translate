import numpy as np

from app.stt import _is_hallucination, _prepare_audio
from app.text_norm import normalize_spoken_text
from app.vad import StreamingVAD


def test_spoken_indonesian_normalizer():
    assert (
        normalize_spoken_text("Bentar ya, aku gak tau gimana caranya nih.", "id")
        == "Sebentar ya, aku tidak tau bagaimana caranya ini."
    )
    assert (
        normalize_spoken_text("Udah ngecek, tapi blm kedengeran sih.", "id")
        == "Sudah mengecek, tetapi belum terdengar."
    )
    assert (
        normalize_spoken_text("Coba lagi, hasilnya masih sama saja.", "id")
        == "Coba lagi, hasilnya masih sama saja."
    )
    # Non-Indonesian text passes through unchanged
    assert normalize_spoken_text("I'm gonna check gak.", "en") == "I'm gonna check gak."


def test_stt_audio_conditioning_and_hallucination_filter():
    t = np.linspace(0, 0.5, 8000, endpoint=False)
    quiet = (0.04 * np.sin(2 * np.pi * 300 * t) + 0.02).astype(np.float32)
    conditioned = _prepare_audio(quiet)
    assert abs(float(np.mean(conditioned))) < 1e-4
    assert float(np.max(np.abs(conditioned))) > float(np.max(np.abs(quiet)))

    assert _is_hallucination("Terima kasih telah menonton.") is True
    assert _is_hallucination("Subtitles by Amara.org") is True
    assert _is_hallucination("ulang ulang ulang ulang ulang") is True
    assert _is_hallucination("Halo, saya sedang mengetes suara.") is False


def test_vad_hysteresis_holds_soft_ending_and_splits_long_speech():
    vad = StreamingVAD(
        sample_rate=16000,
        frame_duration_ms=20,
        energy_threshold=350.0,
        continue_energy_threshold=150.0,
        silence_ms=160,
        min_speech_ms=80,
        max_utterance_ms=600,  # 30 frames
        pre_roll_ms=40,
        post_roll_ms=40,
    )
    frame_samples = 320
    t = np.linspace(0, 0.02, frame_samples, endpoint=False)
    loud = (np.sin(2 * np.pi * 220 * t) * 4000).astype(np.int16).tobytes()
    soft = (np.sin(2 * np.pi * 220 * t) * 320).astype(np.int16).tobytes()  # RMS ~226 (between 150 and 350)
    silent = np.zeros(frame_samples, dtype=np.int16).tobytes()

    # Start with loud frames, continue with soft trailing frames (above continue threshold), then silence
    res = vad.feed(loud * 6 + soft * 4 + silent * 10)
    utterances = [r.utterance for r in res if r.utterance is not None]
    assert len(utterances) == 1
    # Ensure soft trailing frames were preserved before silence trim
    assert len(utterances[0]) >= 9 * frame_samples

    # Test smart split when exceeding max_utterance_ms
    vad.reset()
    # 20 loud frames + 2 soft pause frames + 10 loud frames (> 30 frames max)
    res_split = vad.feed(loud * 20 + soft * 2 + loud * 10)
    split_utts = [r.utterance for r in res_split if r.utterance is not None]
    assert len(split_utts) == 1


def test_tts_voice_resolution_and_pitch_shift():
    from app.tts import PiperTTS, apply_pitch_shift

    tts = PiperTTS()
    en_male = tts.resolve_voice("en", "male")
    en_female = tts.resolve_voice("en", "female")
    assert en_male is not None and en_male["gender"] == "male"
    assert en_female is not None and en_female["gender"] == "female"
    assert en_male["model_id"] != en_female["model_id"]

    id_male = tts.resolve_voice("id", "male")
    assert id_male is not None and id_male["gender"] == "male"
    assert float(id_male["pitch_ratio"]) < 1.0

    # Verify ffmpeg rubberband pitch shift preserves valid PCM16 output
    t = np.linspace(0, 0.25, 5512, endpoint=False)
    pcm = (np.sin(2 * np.pi * 220 * t) * 10000).astype(np.int16).tobytes()
    shifted = apply_pitch_shift(pcm, 22050, 0.78)
    assert len(shifted) > 0
    assert len(shifted) % 2 == 0
