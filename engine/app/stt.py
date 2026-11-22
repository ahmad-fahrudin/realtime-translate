from dataclasses import dataclass
import logging
import re
import threading
import time
import ctranslate2
import numpy as np
from faster_whisper import WhisperModel

from .config import settings

logger = logging.getLogger("rt-stt")

_BASE_PROMPTS: dict[str, str] = {
    "id": "Percakapan bahasa Indonesia sehari-hari yang jelas, termasuk istilah kerja dan teknologi.",
    "en": "Clear everyday English conversation, including work and technology terms.",
}

_HALLUCINATION_PHRASES: set[str] = {
    "terima kasih telah menonton",
    "terima kasih sudah menonton",
    "jangan lupa subscribe",
    "selamat menonton",
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "subtitles by",
    "amara org",
    "mbc 뉴스",
    "ご視聴ありがとうございました",
}

_PUNCT_ONLY_RE = re.compile(r"[^\w\s]+", flags=re.UNICODE)


def _is_hallucination(text: str) -> bool:
    normalized = _PUNCT_ONLY_RE.sub(" ", text.lower())
    normalized = " ".join(normalized.split()).strip()
    if not normalized:
        return True
    for phrase in _HALLUCINATION_PHRASES:
        if phrase in normalized and len(normalized) <= len(phrase) + 12:
            return True
    # Reject repetitive loop hallucinations (same token >= 5 times in a row)
    words = normalized.split()
    if len(words) >= 5:
        streak = 1
        for i in range(1, len(words)):
            streak = streak + 1 if words[i] == words[i - 1] else 1
            if streak >= 5:
                return True
    return False


def _prepare_audio(audio_f32: np.ndarray) -> np.ndarray:
    """Remove DC offset and gently normalize quiet speech without boosting silence."""
    if audio_f32.size == 0:
        return audio_f32
    clean = (audio_f32 - float(np.mean(audio_f32))).astype(np.float32)
    rms = float(np.sqrt(np.mean(clean ** 2)))
    peak = float(np.max(np.abs(clean)))
    if rms > 0.008 and 1e-4 < peak < 0.35:
        gain = min(4.0, 0.5 / peak)
        clean = np.clip(clean * gain, -1.0, 1.0).astype(np.float32)
    return clean


@dataclass
class STTResult:
    text: str
    language: str
    probability: float


class FasterWhisperSTT:
    """Offline Speech-to-Text backed by faster-whisper (CUDA auto-detect with CPU fallback)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._model: WhisperModel | None = None
        self._context: dict[str, tuple[str, float]] = {}
        self.active_device: str = "unloaded"

    def _resolve_device(self) -> str:
        req = (settings.whisper_device or "auto").lower()
        if req == "auto":
            try:
                return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                return "cpu"
        return req

    def load(self) -> WhisperModel:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    device = self._resolve_device()
                    try:
                        self._model = WhisperModel(
                            settings.whisper_model,
                            device=device,
                            compute_type=settings.whisper_compute_type,
                            cpu_threads=settings.whisper_cpu_threads,
                        )
                        self.active_device = device
                        logger.info(
                            "Loaded Whisper '%s' on %s (%s)",
                            settings.whisper_model,
                            device,
                            settings.whisper_compute_type,
                        )
                    except Exception as exc:
                        if device != "cpu":
                            logger.warning(
                                "Whisper CUDA load failed (%s); falling back to CPU int8", exc
                            )
                            self._model = WhisperModel(
                                settings.whisper_model,
                                device="cpu",
                                compute_type="int8",
                                cpu_threads=settings.whisper_cpu_threads,
                            )
                            self.active_device = "cpu"
                        else:
                            raise
        return self._model

    def _build_prompt(self, src_lang: str, channel: str) -> str | None:
        base = _BASE_PROMPTS.get(src_lang, "")
        prev_entry = self._context.get(channel)
        if prev_entry:
            prev_text, ts = prev_entry
            if time.monotonic() - ts <= settings.stt_context_ttl_sec and prev_text:
                return f"{base} {prev_text}".strip()
        return base or None

    def transcribe(
        self,
        audio_f32: np.ndarray,
        src_lang: str = "auto",
        channel: str = "default",
    ) -> STTResult | None:
        if audio_f32.size < int(settings.sample_rate * 0.25):
            return None

        prepared = _prepare_audio(audio_f32)
        model = self.load()
        language = None if src_lang in ("auto", "", None) else src_lang
        prompt = self._build_prompt(src_lang, channel)

        with self._infer_lock:
            segments, info = model.transcribe(
                prepared,
                language=language,
                beam_size=settings.stt_beam_size,
                best_of=1,
                temperature=0.0,
                initial_prompt=prompt,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
            )

            texts: list[str] = []
            for seg in segments:
                if seg.no_speech_prob > 0.6:
                    continue
                if getattr(seg, "compression_ratio", 1.0) > 2.4:
                    continue
                if getattr(seg, "avg_logprob", 0.0) < -1.15 and seg.no_speech_prob > 0.35:
                    continue
                chunk = seg.text.strip()
                if chunk and any(ch.isalnum() for ch in chunk):
                    texts.append(chunk)

        full_text = " ".join(texts).strip()
        if not full_text or _is_hallucination(full_text):
            return None

        self._context[channel] = (full_text[-180:], time.monotonic())
        detected_lang = info.language or (src_lang if src_lang != "auto" else "en")
        return STTResult(
            text=full_text,
            language=detected_lang,
            probability=float(getattr(info, "language_probability", 1.0)),
        )

