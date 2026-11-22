from dataclasses import dataclass
import threading
import numpy as np
from faster_whisper import WhisperModel

from .config import settings


@dataclass
class STTResult:
    text: str
    language: str
    probability: float


class FasterWhisperSTT:
    """Offline CPU INT8 Speech-to-Text backed by faster-whisper."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: WhisperModel | None = None

    def load(self) -> WhisperModel:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = WhisperModel(
                        settings.whisper_model,
                        device="cpu",
                        compute_type=settings.whisper_compute_type,
                        cpu_threads=settings.whisper_cpu_threads,
                    )
        return self._model

    def transcribe(self, audio_f32: np.ndarray, src_lang: str = "auto") -> STTResult | None:
        if audio_f32.size < int(settings.sample_rate * 0.25):
            return None

        model = self.load()
        language = None if src_lang in ("auto", "", None) else src_lang

        segments, info = model.transcribe(
            audio_f32,
            language=language,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )

        texts: list[str] = []
        for seg in segments:
            if seg.no_speech_prob > 0.65:
                continue
            chunk = seg.text.strip()
            if chunk:
                texts.append(chunk)

        full_text = " ".join(texts).strip()
        if not full_text:
            return None

        return STTResult(
            text=full_text,
            language=info.language or (src_lang if src_lang != "auto" else "en"),
            probability=float(getattr(info, "language_probability", 1.0)),
        )

