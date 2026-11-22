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

# Verified rhasspy/piper-voices paths on Hugging Face.
# Languages with a single native voice (e.g. id, ar) also expose a pitch-shifted
# offline variant via ffmpeg rubberband so both male/female presets are available.
PIPER_VOICES: dict[str, list[dict[str, object]]] = {
    "id": [
        {
            "id": "id_ID-news_tts-medium",
            "model_id": "id_ID-news_tts-medium",
            "label": "News TTS (Perempuan)",
            "gender": "female",
            "repo_path": "id/id_ID/news_tts/medium/id_ID-news_tts-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "id_ID-news_tts-medium#male",
            "model_id": "id_ID-news_tts-medium",
            "label": "News TTS · Nada Rendah (Laki-laki)",
            "gender": "male",
            "repo_path": "id/id_ID/news_tts/medium/id_ID-news_tts-medium",
            "pitch_ratio": 0.78,
        },
    ],
    "en": [
        {
            "id": "en_US-lessac-medium",
            "model_id": "en_US-lessac-medium",
            "label": "Lessac US (Perempuan)",
            "gender": "female",
            "repo_path": "en/en_US/lessac/medium/en_US-lessac-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "en_US-amy-medium",
            "model_id": "en_US-amy-medium",
            "label": "Amy US (Perempuan)",
            "gender": "female",
            "repo_path": "en/en_US/amy/medium/en_US-amy-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "en_US-ryan-medium",
            "model_id": "en_US-ryan-medium",
            "label": "Ryan US (Laki-laki)",
            "gender": "male",
            "repo_path": "en/en_US/ryan/medium/en_US-ryan-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "en_GB-alan-medium",
            "model_id": "en_GB-alan-medium",
            "label": "Alan UK (Laki-laki)",
            "gender": "male",
            "repo_path": "en/en_GB/alan/medium/en_GB-alan-medium",
            "pitch_ratio": 1.0,
        },
    ],
    "es": [
        {
            "id": "es_ES-davefx-medium",
            "model_id": "es_ES-davefx-medium",
            "label": "Davefx ES (Laki-laki)",
            "gender": "male",
            "repo_path": "es/es_ES/davefx/medium/es_ES-davefx-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "es_AR-daniela-high",
            "model_id": "es_AR-daniela-high",
            "label": "Daniela AR (Perempuan)",
            "gender": "female",
            "repo_path": "es/es_AR/daniela/high/es_AR-daniela-high",
            "pitch_ratio": 1.0,
        },
    ],
    "fr": [
        {
            "id": "fr_FR-siwis-medium",
            "model_id": "fr_FR-siwis-medium",
            "label": "Siwis FR (Perempuan)",
            "gender": "female",
            "repo_path": "fr/fr_FR/siwis/medium/fr_FR-siwis-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "fr_FR-tom-medium",
            "model_id": "fr_FR-tom-medium",
            "label": "Tom FR (Laki-laki)",
            "gender": "male",
            "repo_path": "fr/fr_FR/tom/medium/fr_FR-tom-medium",
            "pitch_ratio": 1.0,
        },
    ],
    "de": [
        {
            "id": "de_DE-thorsten-medium",
            "model_id": "de_DE-thorsten-medium",
            "label": "Thorsten DE (Laki-laki)",
            "gender": "male",
            "repo_path": "de/de_DE/thorsten/medium/de_DE-thorsten-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "de_DE-kerstin-medium",
            "model_id": "de_DE-kerstin-medium",
            "label": "Kerstin DE (Perempuan)",
            "gender": "female",
            "repo_path": "de/de_DE/kerstin/medium/de_DE-kerstin-medium",
            "pitch_ratio": 1.0,
        },
    ],
    "zh": [
        {
            "id": "zh_CN-huayan-medium",
            "model_id": "zh_CN-huayan-medium",
            "label": "Huayan ZH (Perempuan)",
            "gender": "female",
            "repo_path": "zh/zh_CN/huayan/medium/zh_CN-huayan-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "zh_CN-chaowen-medium",
            "model_id": "zh_CN-chaowen-medium",
            "label": "Chaowen ZH (Laki-laki)",
            "gender": "male",
            "repo_path": "zh/zh_CN/chaowen/medium/zh_CN-chaowen-medium",
            "pitch_ratio": 1.0,
        },
    ],
    "ar": [
        {
            "id": "ar_JO-kareem-medium",
            "model_id": "ar_JO-kareem-medium",
            "label": "Kareem AR (Laki-laki)",
            "gender": "male",
            "repo_path": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium",
            "pitch_ratio": 1.0,
        },
        {
            "id": "ar_JO-kareem-medium#female",
            "model_id": "ar_JO-kareem-medium",
            "label": "Kareem · Nada Tinggi (Perempuan)",
            "gender": "female",
            "repo_path": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium",
            "pitch_ratio": 1.24,
        },
    ],
}

# Backwards-compatible default voice map (first entry per language)
PIPER_VOICE_CATALOG: dict[str, dict[str, str]] = {
    lang: {
        "voice_id": str(items[0]["model_id"]),
        "repo_path": str(items[0]["repo_path"]),
    }
    for lang, items in PIPER_VOICES.items()
}


WHISPER_MODEL_CATALOG: list[dict[str, str]] = [
    {"id": "tiny", "label": "Tiny · Tercepat (akurasi rendah)"},
    {"id": "base", "label": "Base · Cepat"},
    {"id": "small", "label": "Small · Seimbang (Recommended)"},
    {"id": "medium", "label": "Medium · Akurasi tinggi"},
    {"id": "large-v3-turbo", "label": "Large v3 Turbo · Sangat tinggi"},
    {"id": "large-v3", "label": "Large v3 · Maksimal (berat di VRAM 3 GB)"},
]

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


def whisper_dir() -> Path:
    path = Path(settings.models_dir) / "whisper"
    path.mkdir(parents=True, exist_ok=True)
    return path

