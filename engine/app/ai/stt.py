from dataclasses import dataclass
import gc
import logging
from pathlib import Path
import re
import threading
import time
from typing import Callable
import ctranslate2
from faster_whisper import WhisperModel
from faster_whisper.utils import _MODELS as WHISPER_REPOS
from huggingface_hub import try_to_load_from_cache
import numpy as np

from ..core.config import WHISPER_MODEL_CATALOG, settings, whisper_dir
from ..core.downloader import DownloadCancelled, download_hf_files

_WHISPER_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

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
        self.active_model_name: str = self._load_saved_model_name()
        self.active_device: str = "unloaded"

    def _state_file(self) -> Path:
        return Path(settings.models_dir) / "active_whisper_model.txt"

    def _load_saved_model_name(self) -> str:
        valid_ids = {item["id"] for item in WHISPER_MODEL_CATALOG}
        state_path = self._state_file()
        try:
            if state_path.exists():
                saved = state_path.read_text(encoding="utf-8").strip()
                if saved in valid_ids:
                    return saved
        except OSError:
            pass
        return settings.whisper_model

    def _save_active_model_name(self, model_name: str) -> None:
        try:
            state_path = self._state_file()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(model_name, encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to persist active Whisper model '%s': %s", model_name, exc)

    def _resolve_device(self) -> str:
        req = (settings.whisper_device or "auto").lower()
        if req == "auto":
            try:
                return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                return "cpu"
        return req

    def _local_model_dir(self, model_name: str) -> Path:
        return whisper_dir() / model_name

    def _is_in_hf_cache(self, model_name: str) -> bool:
        repo = WHISPER_REPOS.get(model_name)
        if not repo:
            return False
        hub_cache = Path(settings.models_dir) / "huggingface" / "hub"
        cached = try_to_load_from_cache(
            repo_id=repo,
            filename="model.bin",
            cache_dir=str(hub_cache),
        )
        return isinstance(cached, str) and Path(cached).exists()

    def _is_model_installed(self, model_name: str) -> bool:
        local_bin = self._local_model_dir(model_name) / "model.bin"
        return local_bin.exists() or self._is_in_hf_cache(model_name)

    def installed_models(self) -> list[str]:
        return [item["id"] for item in WHISPER_MODEL_CATALOG if self._is_model_installed(item["id"])]

    def _resolve_model_arg(self, model_name: str) -> str:
        local_dir = self._local_model_dir(model_name)
        if (local_dir / "model.bin").exists():
            return str(local_dir)
        return model_name

    def _load_unlocked(self, model_name: str) -> WhisperModel:
        device = self._resolve_device()
        model_arg = self._resolve_model_arg(model_name)
        try:
            model = WhisperModel(
                model_arg,
                device=device,
                compute_type=settings.whisper_compute_type,
                cpu_threads=settings.whisper_cpu_threads,
            )
            self.active_device = device
            logger.info(
                "Loaded Whisper '%s' on %s (%s)",
                model_name,
                device,
                settings.whisper_compute_type,
            )
            return model
        except Exception as exc:
            if device != "cpu":
                logger.warning("Whisper CUDA load failed (%s); falling back to CPU int8", exc)
                model = WhisperModel(
                    model_arg,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=settings.whisper_cpu_threads,
                )
                self.active_device = "cpu"
                return model
            raise

    def load(self) -> WhisperModel:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = self._load_unlocked(self.active_model_name)
        return self._model

    def switch_model(
        self,
        model_name: str,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, object]:
        valid_ids = {item["id"] for item in WHISPER_MODEL_CATALOG}
        if model_name not in valid_ids:
            raise ValueError(f"Model Whisper tidak dikenal: {model_name}")

        if model_name == self.active_model_name and self._model is not None:
            return {
                "active_model": self.active_model_name,
                "active_device": self.active_device,
                "installed_models": self.installed_models(),
            }

        # Download first (without holding _infer_lock or unloading current model)
        # so cancellation leaves the currently active model intact.
        if not self._is_model_installed(model_name):
            repo_id = WHISPER_REPOS.get(model_name)
            if not repo_id:
                raise ValueError(f"Repo Whisper tidak ditemukan untuk '{model_name}'")
            download_hf_files(
                repo_id=repo_id,
                dest_dir=self._local_model_dir(model_name),
                allow_patterns=_WHISPER_ALLOW_PATTERNS,
                cancel_event=cancel_event,
                on_progress=on_progress,
            )

        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Unduhan dibatalkan oleh pengguna.")

        with self._lock:
            with self._infer_lock:
                # Free old model VRAM before loading new weights (important on 3 GB GPUs)
                if self._model is not None:
                    del self._model
                    self._model = None
                    gc.collect()
                self._context.clear()
                self._model = self._load_unlocked(model_name)
                self.active_model_name = model_name
                self._save_active_model_name(model_name)

        return {
            "active_model": self.active_model_name,
            "active_device": self.active_device,
            "installed_models": self.installed_models(),
        }

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

