from dataclasses import dataclass
import threading
import time
from typing import Callable
import uuid
import numpy as np

from .downloader import DownloadCancelled
from .stt import FasterWhisperSTT
from .text_norm import normalize_spoken_text
from .translator import HybridTranslator
from .tts import PiperTTS


@dataclass
class TranslationOutput:
    utterance_id: str
    channel: str
    src_lang: str
    dst_lang: str
    source_text: str
    translated_text: str
    latency_ms: int
    tts_pcm16: bytes | None = None
    tts_sample_rate: int | None = None
    tts_warning: str | None = None
    stt_ms: int = 0
    mt_ms: int = 0
    tts_ms: int = 0
    translator_backend: str = "nllb"


class TranslationEngine:
    """Coordinates STT -> Spoken Normalization -> Machine Translation -> TTS."""

    def __init__(self) -> None:
        self.stt = FasterWhisperSTT()
        self.translator = HybridTranslator()
        self.tts = PiperTTS()

    def preload_defaults(self) -> None:
        self.stt.load()
        self.translator.ensure_language_pair("id", "en")
        self.translator.ensure_language_pair("en", "id")
        self.tts.ensure_voice("id")
        self.tts.ensure_voice("en")

    def prepare_pair(
        self,
        src_lang: str,
        dst_lang: str,
        bidirectional: bool = False,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        outgoing_voice: str = "auto",
        incoming_voice: str = "auto",
    ) -> dict[str, object]:
        self.stt.load()
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Unduhan dibatalkan oleh pengguna.")

        pairs = [(src_lang, dst_lang)]
        if bidirectional and src_lang != dst_lang:
            pairs.append((dst_lang, src_lang))

        translation_ok = True
        for s_lang, d_lang in pairs:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled("Unduhan dibatalkan oleh pengguna.")
            if s_lang != "auto" and d_lang != "auto" and s_lang != d_lang:
                if not self.translator.ensure_language_pair(s_lang, d_lang):
                    translation_ok = False

        # Outgoing speaks in dst_lang (peer's language); incoming speaks in src_lang (my language)
        voice_targets: list[tuple[str, str]] = []
        if dst_lang != "auto" and self.tts.has_catalog_voice(dst_lang):
            voice_targets.append((dst_lang, outgoing_voice))
        if bidirectional and src_lang != "auto" and self.tts.has_catalog_voice(src_lang):
            voice_targets.append((src_lang, incoming_voice))

        # Deduplicate by resolved physical model_id so we don't report duplicate downloads
        unique_targets: list[tuple[str, str]] = []
        seen_models: set[str] = set()
        for lang, vid in voice_targets:
            meta = self.tts.resolve_voice(lang, vid)
            if meta is None:
                continue
            model_id = str(meta["model_id"])
            if model_id not in seen_models:
                seen_models.add(model_id)
                unique_targets.append((lang, vid))

        tts_ok = True if unique_targets else False
        total_voices = len(unique_targets)
        for idx, (lang, vid) in enumerate(unique_targets):
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled("Unduhan dibatalkan oleh pengguna.")

            def _voice_progress(
                dl: int, tot: int, label: str, voice_idx: int = idx
            ) -> None:
                if on_progress is None:
                    return
                if tot > 0:
                    voice_frac = min(1.0, dl / tot)
                    overall_pct = int(((voice_idx + voice_frac) / max(1, total_voices)) * 100)
                    on_progress(dl, tot, f"{label}|{overall_pct}")

            need_dl = not self.tts.is_voice_installed(lang, vid)
            ok = (
                self.tts.ensure_voice(
                    lang,
                    voice_id=vid,
                    cancel_event=cancel_event,
                    on_progress=_voice_progress if need_dl else None,
                )
                is not None
            )
            if not ok:
                tts_ok = False

        return {
            "src_lang": src_lang,
            "dst_lang": dst_lang,
            "translation_ready": translation_ok,
            "tts_ready": tts_ok,
            "translator_backend": self.translator.active_backend,
        }

    def process_audio(
        self,
        audio_f32: np.ndarray,
        channel: str,
        src_lang: str,
        dst_lang: str,
        tts_enabled: bool = True,
        voice_id: str | None = None,
        tts_speed: float = 1.0,
    ) -> TranslationOutput | None:
        t0 = time.perf_counter()
        stt_res = self.stt.transcribe(audio_f32, src_lang=src_lang, channel=channel)
        if stt_res is None:
            return None
        stt_ms = int((time.perf_counter() - t0) * 1000)

        detected_src = stt_res.language if src_lang == "auto" else src_lang
        return self._translate_and_synthesize(
            text=stt_res.text,
            channel=channel,
            src_lang=detected_src,
            dst_lang=dst_lang,
            tts_enabled=tts_enabled,
            t0=t0,
            stt_ms=stt_ms,
            voice_id=voice_id,
            tts_speed=tts_speed,
        )

    def process_text(
        self,
        text: str,
        channel: str,
        src_lang: str,
        dst_lang: str,
        tts_enabled: bool = True,
        voice_id: str | None = None,
        tts_speed: float = 1.0,
    ) -> TranslationOutput | None:
        clean = text.strip()
        if not clean:
            return None
        resolved_src = "id" if src_lang == "auto" else src_lang
        return self._translate_and_synthesize(
            text=clean,
            channel=channel,
            src_lang=resolved_src,
            dst_lang=dst_lang,
            tts_enabled=tts_enabled,
            t0=time.perf_counter(),
            stt_ms=0,
            voice_id=voice_id,
            tts_speed=tts_speed,
        )

    def _translate_and_synthesize(
        self,
        text: str,
        channel: str,
        src_lang: str,
        dst_lang: str,
        tts_enabled: bool,
        t0: float,
        stt_ms: int,
        voice_id: str | None = None,
        tts_speed: float = 1.0,
    ) -> TranslationOutput:
        mt_t0 = time.perf_counter()
        normalized = normalize_spoken_text(text, src_lang)
        translated = self.translator.translate(normalized, src_lang=src_lang, dst_lang=dst_lang)
        mt_ms = int((time.perf_counter() - mt_t0) * 1000)

        tts_pcm16: bytes | None = None
        tts_sr: int | None = None
        tts_warning: str | None = None
        tts_t0 = time.perf_counter()

        if tts_enabled and translated:
            if self.tts.has_catalog_voice(dst_lang):
                audio = self.tts.synthesize(
                    translated, lang=dst_lang, voice_id=voice_id, speed=tts_speed
                )
                if audio is not None:
                    tts_pcm16 = audio.pcm16_bytes
                    tts_sr = audio.sample_rate
            else:
                tts_warning = f"Piper offline voice unavailable for '{dst_lang}'; subtitle-only."

        tts_ms = int((time.perf_counter() - tts_t0) * 1000)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        return TranslationOutput(
            utterance_id=uuid.uuid4().hex[:12],
            channel=channel,
            src_lang=src_lang,
            dst_lang=dst_lang,
            source_text=text,
            translated_text=translated,
            latency_ms=latency_ms,
            tts_pcm16=tts_pcm16,
            tts_sample_rate=tts_sr,
            tts_warning=tts_warning,
            stt_ms=stt_ms,
            mt_ms=mt_ms,
            tts_ms=tts_ms,
            translator_backend=self.translator.active_backend,
        )
