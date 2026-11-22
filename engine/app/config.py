from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RT_")

    host: str = "0.0.0.0"
    port: int = 8765
    models_dir: str = "/models"

    # STT (Faster-Whisper)
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    whisper_cpu_threads: int = 4
    stt_beam_size: int = 3
    stt_context_ttl_sec: float = 15.0

    # VAD & Audio (16kHz mono PCM16)
    sample_rate: int = 16000
    frame_duration_ms: int = 20
    vad_aggressiveness: int = 2
    vad_energy_threshold: float = 350.0
    vad_continue_energy_threshold: float = 180.0
    vad_silence_ms: int = 750
    vad_min_speech_ms: int = 240
    vad_max_utterance_ms: int = 10000
    vad_pre_roll_ms: int = 240
    vad_post_roll_ms: int = 200

    # Translation Backend ("nllb" primary with "argos" fallback)
    translator_backend: str = "nllb"
    nllb_model_id: str = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
    nllb_device: str = "auto"

    # Preload default ID <-> EN models on startup if missing
    preload_default_models: bool = True


settings = Settings()

SUPPORTED_LANGUAGES: dict[str, dict[str, str]] = {
    "id": {"name": "Indonesian", "native": "Bahasa Indonesia"},
    "en": {"name": "English", "native": "English"},
    "es": {"name": "Spanish", "native": "Español"},
    "fr": {"name": "French", "native": "Français"},
    "de": {"name": "German", "native": "Deutsch"},
    "zh": {"name": "Chinese", "native": "中文"},
    "ar": {"name": "Arabic", "native": "العربية"},
    "ja": {"name": "Japanese", "native": "日本語"},
    "ko": {"name": "Korean", "native": "한국어"},
}

# Verified rhasspy/piper-voices paths on Hugging Face
PIPER_VOICE_CATALOG: dict[str, dict[str, str]] = {
    "id": {
        "voice_id": "id_ID-news_tts-medium",
        "repo_path": "id/id_ID/news_tts/medium/id_ID-news_tts-medium",
    },
    "en": {
        "voice_id": "en_US-lessac-medium",
        "repo_path": "en/en_US/lessac/medium/en_US-lessac-medium",
    },
    "es": {
        "voice_id": "es_ES-davefx-medium",
        "repo_path": "es/es_ES/davefx/medium/es_ES-davefx-medium",
    },
    "fr": {
        "voice_id": "fr_FR-siwis-medium",
        "repo_path": "fr/fr_FR/siwis/medium/fr_FR-siwis-medium",
    },
    "de": {
        "voice_id": "de_DE-thorsten-medium",
        "repo_path": "de/de_DE/thorsten/medium/de_DE-thorsten-medium",
    },
    "zh": {
        "voice_id": "zh_CN-huayan-medium",
        "repo_path": "zh/zh_CN/huayan/medium/zh_CN-huayan-medium",
    },
    "ar": {
        "voice_id": "ar_JO-kareem-medium",
        "repo_path": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium",
    },
}


NLLB_LANG_CODES: dict[str, str] = {
    "id": "ind_Latn",
    "en": "eng_Latn",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "zh": "zho_Hans",
    "ar": "arb_Arab",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
}


def piper_dir() -> Path:
    path = Path(settings.models_dir) / "piper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def nllb_dir() -> Path:
    path = Path(settings.models_dir) / "nllb"
    path.mkdir(parents=True, exist_ok=True)
    return path

