from dataclasses import dataclass
from pathlib import Path
import threading
import httpx
from piper import PiperVoice

from .config import PIPER_VOICE_CATALOG, piper_dir

HF_PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


@dataclass
class TTSAudio:
    pcm16_bytes: bytes
    sample_rate: int


class PiperTTS:
    """Offline CPU ONNX Text-to-Speech using Piper."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voices: dict[str, PiperVoice] = {}

    def has_catalog_voice(self, lang: str) -> bool:
        return lang in PIPER_VOICE_CATALOG

    def installed_languages(self) -> list[str]:
        base = piper_dir()
        installed: list[str] = []
        for lang, meta in PIPER_VOICE_CATALOG.items():
            onnx_file = base / f"{meta['voice_id']}.onnx"
            cfg_file = base / f"{meta['voice_id']}.onnx.json"
            if onnx_file.exists() and cfg_file.exists():
                installed.append(lang)
        return installed

    def ensure_voice(self, lang: str) -> PiperVoice | None:
        if lang in self._voices:
            return self._voices[lang]

        meta = PIPER_VOICE_CATALOG.get(lang)
        if meta is None:
            return None

        with self._lock:
            if lang in self._voices:
                return self._voices[lang]

            base = piper_dir()
            onnx_path = base / f"{meta['voice_id']}.onnx"
            cfg_path = base / f"{meta['voice_id']}.onnx.json"

            if not onnx_path.exists() or not cfg_path.exists():
                self._download_voice(meta["repo_path"], onnx_path, cfg_path)

            voice = PiperVoice.load(str(onnx_path), config_path=str(cfg_path))
            self._voices[lang] = voice
            return voice

    def _download_voice(self, repo_path: str, onnx_path: Path, cfg_path: Path) -> None:
        onnx_url = f"{HF_PIPER_BASE}/{repo_path}.onnx"
        cfg_url = f"{HF_PIPER_BASE}/{repo_path}.onnx.json"

        with httpx.Client(follow_redirects=True, timeout=120.0) as client:
            if not cfg_path.exists():
                resp = client.get(cfg_url)
                resp.raise_for_status()
                cfg_path.write_bytes(resp.content)

            if not onnx_path.exists():
                tmp_path = onnx_path.with_suffix(".onnx.tmp")
                with client.stream("GET", onnx_url) as resp:
                    resp.raise_for_status()
                    with tmp_path.open("wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                            f.write(chunk)
                tmp_path.replace(onnx_path)

    def synthesize(self, text: str, lang: str) -> TTSAudio | None:
        clean = text.strip()
        if not clean:
            return None

        voice = self.ensure_voice(lang)
        if voice is None:
            return None

        chunks: list[bytes] = []
        # Handle both piper-tts >= 1.3 (AudioChunk iterable) and older stream_raw API
        if hasattr(voice, "synthesize_stream_raw"):
            for raw in voice.synthesize_stream_raw(clean):
                chunks.append(raw)
        else:
            for audio_chunk in voice.synthesize(clean):
                chunks.append(audio_chunk.audio_int16_bytes)

        pcm = b"".join(chunks)
        if not pcm:
            return None

        return TTSAudio(pcm16_bytes=pcm, sample_rate=int(voice.config.sample_rate))

