from dataclasses import dataclass
import time
import uuid
import numpy as np

from .stt import FasterWhisperSTT
from .translator import ArgosTranslator
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


class TranslationEngine:
    """Coordinates STT -> Machine Translation -> TTS on CPU."""

    def __init__(self) -> None:
        self.stt = FasterWhisperSTT()
        self.translator = ArgosTranslator()
        self.tts = PiperTTS()

    def preload_defaults(self) -> None:
        self.stt.load()
        self.translator.ensure_language_pair("id", "en")
        self.translator.ensure_language_pair("en", "id")
        self.tts.ensure_voice("id")
        self.tts.ensure_voice("en")

    def prepare_pair(self, src_lang: str, dst_lang: str) -> dict[str, object]:
        self.stt.load()
        translation_ok = True
        if src_lang != "auto" and dst_lang != "auto" and src_lang != dst_lang:
            translation_ok = self.translator.ensure_language_pair(src_lang, dst_lang)

        tts_ok = False
        if dst_lang != "auto" and self.tts.has_catalog_voice(dst_lang):
            tts_ok = self.tts.ensure_voice(dst_lang) is not None

        return {
            "src_lang": src_lang,
            "dst_lang": dst_lang,
            "translation_ready": translation_ok,
            "tts_ready": tts_ok,
        }

    def process_audio(
        self,
        audio_f32: np.ndarray,
        channel: str,
        src_lang: str,
        dst_lang: str,
        tts_enabled: bool = True,
    ) -> TranslationOutput | None:
        t0 = time.perf_counter()
        stt_res = self.stt.transcribe(audio_f32, src_lang=src_lang)
        if stt_res is None:
            return None

        detected_src = stt_res.language if src_lang == "auto" else src_lang
        return self._translate_and_synthesize(
            text=stt_res.text,
            channel=channel,
            src_lang=detected_src,
            dst_lang=dst_lang,
            tts_enabled=tts_enabled,
            t0=t0,
        )

    def process_text(
        self,
        text: str,
        channel: str,
        src_lang: str,
        dst_lang: str,
        tts_enabled: bool = True,
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
        )

    def _translate_and_synthesize(
        self,
        text: str,
        channel: str,
        src_lang: str,
        dst_lang: str,
        tts_enabled: bool,
        t0: float,
    ) -> TranslationOutput:
        translated = self.translator.translate(text, src_lang=src_lang, dst_lang=dst_lang)

        tts_pcm16: bytes | None = None
        tts_sr: int | None = None
        tts_warning: str | None = None

        if tts_enabled and translated:
            if self.tts.has_catalog_voice(dst_lang):
                audio = self.tts.synthesize(translated, lang=dst_lang)
                if audio is not None:
                    tts_pcm16 = audio.pcm16_bytes
                    tts_sr = audio.sample_rate
            else:
                tts_warning = f"Piper offline voice unavailable for '{dst_lang}'; subtitle-only."

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
        )

