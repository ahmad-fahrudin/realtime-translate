from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
import threading
from typing import Callable
import httpx
from piper import PiperVoice, SynthesisConfig

from .config import PIPER_VOICES, piper_dir
from .downloader import stream_download_file

HF_PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
logger = logging.getLogger("rt-tts")


@dataclass
class TTSAudio:
    pcm16_bytes: bytes
    sample_rate: int


def apply_pitch_shift(pcm16_bytes: bytes, sample_rate: int, pitch_ratio: float) -> bytes:
    """Pitch-shift mono s16le PCM without changing duration via ffmpeg rubberband."""
    if not pcm16_bytes or abs(pitch_ratio - 1.0) < 0.01:
        return pcm16_bytes
    try:
        res = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-i",
                "pipe:0",
                "-af",
                f"rubberband=pitch={pitch_ratio:.3f}",
                "-f",
                "s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "pipe:1",
            ],
            input=pcm16_bytes,
            capture_output=True,
            check=True,
        )
        return res.stdout or pcm16_bytes
    except Exception as exc:
        logger.warning("Pitch shift (ratio=%.2f) failed (%s); returning original audio", pitch_ratio, exc)
        return pcm16_bytes


class PiperTTS:
    """Offline CPU ONNX Text-to-Speech using Piper with multi-voice & gender support."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voices: dict[str, PiperVoice] = {}

    def has_catalog_voice(self, lang: str) -> bool:
        return bool(PIPER_VOICES.get(lang))

    def resolve_voice(self, lang: str, voice_id: str | None = None) -> dict[str, object] | None:
        items = PIPER_VOICES.get(lang)
        if not items:
            return None
        if not voice_id or voice_id == "auto":
            return items[0]
        if voice_id in ("male", "female"):
            return next((it for it in items if it["gender"] == voice_id), items[0])
        return next((it for it in items if it["id"] == voice_id), items[0])

    def _model_files_exist(self, model_id: str) -> bool:
        base = piper_dir()
        return (base / f"{model_id}.onnx").exists() and (base / f"{model_id}.onnx.json").exists()

    def installed_languages(self) -> list[str]:
        installed: list[str] = []
        for lang, items in PIPER_VOICES.items():
            if any(self._model_files_exist(str(it["model_id"])) for it in items):
                installed.append(lang)
        return installed

    def is_voice_installed(self, lang: str, voice_id: str | None = None) -> bool:
        meta = self.resolve_voice(lang, voice_id)
        if meta is None:
            return False
        return self._model_files_exist(str(meta["model_id"]))

    def list_voices_by_lang(self) -> dict[str, list[dict[str, object]]]:
        result: dict[str, list[dict[str, object]]] = {}
        for lang, items in PIPER_VOICES.items():
            result[lang] = [
                {
                    "id": str(it["id"]),
                    "label": str(it["label"]),
                    "gender": str(it["gender"]),
                    "installed": self._model_files_exist(str(it["model_id"])),
                }
                for it in items
            ]
        return result

    def ensure_voice_meta(
        self,
        lang: str,
        voice_id: str | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[PiperVoice, dict[str, object]] | None:
        meta = self.resolve_voice(lang, voice_id)
        if meta is None:
            return None

        model_id = str(meta["model_id"])
        if model_id in self._voices:
            return self._voices[model_id], meta

        with self._lock:
            if model_id in self._voices:
                return self._voices[model_id], meta

            base = piper_dir()
            onnx_path = base / f"{model_id}.onnx"
            cfg_path = base / f"{model_id}.onnx.json"

            if not onnx_path.exists() or not cfg_path.exists():
                self._download_voice(
                    str(meta["repo_path"]),
                    onnx_path,
                    cfg_path,
                    label_tag=str(meta["id"]),
                    cancel_event=cancel_event,
                    on_progress=on_progress,
                )

            voice = PiperVoice.load(str(onnx_path), config_path=str(cfg_path))
            self._voices[model_id] = voice
            return voice, meta

    def ensure_voice(
        self,
        lang: str,
        voice_id: str | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> PiperVoice | None:
        res = self.ensure_voice_meta(
            lang=lang,
            voice_id=voice_id,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )
        return res[0] if res is not None else None

    def _download_voice(
        self,
        repo_path: str,
        onnx_path: Path,
        cfg_path: Path,
        label_tag: str = "",
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        onnx_url = f"{HF_PIPER_BASE}/{repo_path}.onnx"
        cfg_url = f"{HF_PIPER_BASE}/{repo_path}.onnx.json"

        with httpx.Client(follow_redirects=True, timeout=120.0) as client:
            if not cfg_path.exists():
                stream_download_file(client, cfg_url, cfg_path, cancel_event=cancel_event)

            if not onnx_path.exists():
                downloaded = 0

                def _on_chunk(delta: int, content_len: int | None) -> None:
                    nonlocal downloaded
                    downloaded += delta
                    if on_progress is not None:
                        on_progress(downloaded, content_len or 0, label_tag or onnx_path.stem)

                stream_download_file(
                    client,
                    onnx_url,
                    onnx_path,
                    cancel_event=cancel_event,
                    on_chunk=_on_chunk,
                )

    def synthesize(
        self,
        text: str,
        lang: str,
        voice_id: str | None = None,
        speed: float = 1.0,
    ) -> TTSAudio | None:
        clean = text.strip()
        if not clean:
            return None

        loaded = self.ensure_voice_meta(lang, voice_id=voice_id)
        if loaded is None:
            return None
        voice, meta = loaded

        speaker_id = meta.get("speaker_id")
        clamped_speed = max(0.5, min(2.0, float(speed)))
        length_scale = round(1.0 / clamped_speed, 3) if abs(clamped_speed - 1.0) >= 0.01 else None
        if speaker_id is not None or length_scale is not None:
            syn_cfg = SynthesisConfig(
                speaker_id=int(speaker_id) if speaker_id is not None else None,
                length_scale=length_scale,
            )
        else:
            syn_cfg = None

        chunks: list[bytes] = []
        for audio_chunk in voice.synthesize(clean, syn_config=syn_cfg):
            chunks.append(audio_chunk.audio_int16_bytes)

        pcm = b"".join(chunks)
        if not pcm:
            return None

        sample_rate = int(voice.config.sample_rate)
        pitch_ratio = float(meta.get("pitch_ratio", 1.0))
        if abs(pitch_ratio - 1.0) >= 0.01:
            pcm = apply_pitch_shift(pcm, sample_rate, pitch_ratio)

        return TTSAudio(pcm16_bytes=pcm, sample_rate=sample_rate)
