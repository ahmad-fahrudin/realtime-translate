"""Pre-download offline CPU models (Whisper + Argos id<->en + Piper id/en)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline import TranslationEngine


def main() -> None:
    engine = TranslationEngine()
    print("Downloading & caching Faster-Whisper, Argos (id<->en), and Piper (id, en)...")
    engine.preload_defaults()
    print("All default models ready!")


if __name__ == "__main__":
    main()

