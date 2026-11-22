import logging
from pathlib import Path
import re
import threading
import argostranslate.package
import argostranslate.translate
import ctranslate2
from huggingface_hub import snapshot_download
from tokenizers import Tokenizer

from ..core.config import NLLB_LANG_CODES, nllb_dir, settings

logger = logging.getLogger("rt-translator")

_NLLB_LANG_TOKEN_RE = re.compile(r"^[a-z]{3}_[A-Z][a-z]{3}$")
_NLLB_SPECIAL_TOKENS = {"<s>", "</s>", "<pad>", "<unk>"}


class ArgosTranslator:
    """Offline CPU neural machine translation powered by Argos Translate (OpenNMT/CTranslate2)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index_updated = False
        self._installed_pairs: set[tuple[str, str]] = set()
        self._refresh_installed()

    def _refresh_installed(self) -> None:
        installed = argostranslate.package.get_installed_packages()
        self._installed_pairs = {(pkg.from_code, pkg.to_code) for pkg in installed}

    def installed_pairs(self) -> list[tuple[str, str]]:
        self._refresh_installed()
        return sorted(self._installed_pairs)

    def _install_direct_pair(self, src: str, dst: str) -> bool:
        if (src, dst) in self._installed_pairs:
            return True

        with self._lock:
            self._refresh_installed()
            if (src, dst) in self._installed_pairs:
                return True

            if not self._index_updated:
                argostranslate.package.update_package_index()
                self._index_updated = True

            available = argostranslate.package.get_available_packages()
            match = next(
                (p for p in available if p.from_code == src and p.to_code == dst),
                None,
            )
            if match is None:
                return False

            download_path = match.download()
            argostranslate.package.install_from_path(download_path)
            self._refresh_installed()
            return (src, dst) in self._installed_pairs

    def ensure_language_pair(self, src: str, dst: str) -> bool:
        """Ensure direct translation or 2-hop pivot via English is installed."""
        if src == dst:
            return True

        if self._install_direct_pair(src, dst):
            return True

        # Fallback: 2-hop pivot via English (e.g. id -> en -> ja)
        if src != "en" and dst != "en":
            ok_1 = self._install_direct_pair(src, "en")
            ok_2 = self._install_direct_pair("en", dst)
            return ok_1 and ok_2

        return False

    def translate(self, text: str, src_lang: str, dst_lang: str) -> str:
        clean = text.strip()
        if not clean or src_lang == dst_lang:
            return clean

        if not self.ensure_language_pair(src_lang, dst_lang):
            raise RuntimeError(f"Argos translation package unavailable for {src_lang} -> {dst_lang}")

        return argostranslate.translate.translate(clean, src_lang, dst_lang).strip()


class NLLBTranslator:
    """Direct 200-language NMT powered by NLLB-200-distilled-600M (CTranslate2 INT8)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._translator: ctranslate2.Translator | None = None
        self._tokenizer: Tokenizer | None = None
        self.active_device: str = "unloaded"

    def _model_path(self) -> Path:
        folder_name = settings.nllb_model_id.split("/")[-1]
        return nllb_dir() / folder_name

    def _resolve_device(self) -> str:
        req = (settings.nllb_device or "auto").lower()
        if req == "auto":
            try:
                return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                return "cpu"
        return req

    def supports_pair(self, src: str, dst: str) -> bool:
        return src in NLLB_LANG_CODES and dst in NLLB_LANG_CODES

    def load(self) -> tuple[ctranslate2.Translator, Tokenizer]:
        if self._translator is not None and self._tokenizer is not None:
            return self._translator, self._tokenizer

        with self._lock:
            if self._translator is not None and self._tokenizer is not None:
                return self._translator, self._tokenizer

            model_dir = self._model_path()
            model_bin = model_dir / "model.bin"
            tok_file = model_dir / "tokenizer.json"

            if not model_bin.exists() or not tok_file.exists():
                logger.info("Downloading NLLB-200 CT2 model (%s)...", settings.nllb_model_id)
                snapshot_download(
                    repo_id=settings.nllb_model_id,
                    local_dir=str(model_dir),
                    ignore_patterns=[".gitattributes", "README.md"],
                )

            tokenizer = Tokenizer.from_file(str(tok_file))
            device = self._resolve_device()
            try:
                translator = ctranslate2.Translator(
                    str(model_dir),
                    device=device,
                    compute_type="int8",
                    intra_threads=settings.whisper_cpu_threads,
                )
                self.active_device = device
            except Exception as exc:
                if device != "cpu":
                    logger.warning("NLLB CUDA load failed (%s); falling back to CPU int8", exc)
                    translator = ctranslate2.Translator(
                        str(model_dir),
                        device="cpu",
                        compute_type="int8",
                        intra_threads=settings.whisper_cpu_threads,
                    )
                    self.active_device = "cpu"
                else:
                    raise

            self._tokenizer = tokenizer
            self._translator = translator
            logger.info("Loaded NLLB-200 CT2 on %s (int8)", self.active_device)
            return self._translator, self._tokenizer

    def translate(self, text: str, src_lang: str, dst_lang: str) -> str:
        clean = text.strip()
        if not clean or src_lang == dst_lang:
            return clean

        src_code = NLLB_LANG_CODES.get(src_lang)
        dst_code = NLLB_LANG_CODES.get(dst_lang)
        if not src_code or not dst_code:
            raise RuntimeError(f"NLLB language code unavailable for {src_lang} -> {dst_lang}")

        # NLLB-200 is sentence-level; translating per sentence avoids dropping trailing sentences.
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", clean) if p.strip()]
        if not parts:
            parts = [clean]

        translator, tokenizer = self.load()
        batch_tokens: list[list[str]] = []
        target_prefixes: list[list[str]] = []
        for part in parts:
            encoded = tokenizer.encode(part, add_special_tokens=False)
            batch_tokens.append([src_code, *encoded.tokens, "</s>"])
            target_prefixes.append([dst_code])

        with self._infer_lock:
            results = translator.translate_batch(
                batch_tokens,
                target_prefix=target_prefixes,
                beam_size=3,
                repetition_penalty=1.15,
                no_repeat_ngram_size=3,
                max_decoding_length=256,
            )

        decoded_parts: list[str] = []
        for res in results:
            out_tokens = res.hypotheses[0]
            cleaned_ids: list[int] = []
            for tok in out_tokens:
                if tok in _NLLB_SPECIAL_TOKENS or _NLLB_LANG_TOKEN_RE.match(tok):
                    continue
                tid = tokenizer.token_to_id(tok)
                if tid is not None:
                    cleaned_ids.append(tid)
            piece = tokenizer.decode(cleaned_ids, skip_special_tokens=True).strip()
            if piece:
                decoded_parts.append(piece)

        return " ".join(decoded_parts).strip()


class HybridTranslator:
    """Primary NLLB-200 CT2 translator with automatic Argos Translate fallback."""

    def __init__(self) -> None:
        self.nllb = NLLBTranslator()
        self.argos = ArgosTranslator()
        self.preferred_backend: str = (
            "nllb" if settings.translator_backend.lower() == "nllb" else "argos"
        )
        self.active_backend: str = self.preferred_backend

    def set_backend(self, backend: str) -> str:
        clean = backend.strip().lower()
        if clean not in ("nllb", "argos"):
            raise ValueError(f"Backend terjemahan tidak dikenal: {backend}")
        self.preferred_backend = clean
        self.active_backend = clean
        return self.active_backend

    @property
    def active_device(self) -> str:
        if self.active_backend == "nllb":
            return self.nllb.active_device
        return "cpu"

    def installed_pairs(self) -> list[tuple[str, str]]:
        if self.active_backend == "nllb" and self.nllb._translator is not None:
            langs = sorted(NLLB_LANG_CODES.keys())
            return [(s, d) for s in langs for d in langs if s != d]
        return self.argos.installed_pairs()

    def ensure_language_pair(self, src: str, dst: str) -> bool:
        if src == dst:
            return True
        if self.preferred_backend == "nllb" and self.nllb.supports_pair(src, dst):
            try:
                self.nllb.load()
                self.active_backend = "nllb"
                return True
            except Exception as exc:
                logger.warning("NLLB load failed (%s); falling back to Argos", exc)
                self.active_backend = "argos"
        self.active_backend = "argos"
        return self.argos.ensure_language_pair(src, dst)

    def translate(self, text: str, src_lang: str, dst_lang: str) -> str:
        clean = text.strip()
        if not clean or src_lang == dst_lang:
            return clean

        if self.preferred_backend == "nllb" and self.nllb.supports_pair(src_lang, dst_lang):
            try:
                out = self.nllb.translate(clean, src_lang=src_lang, dst_lang=dst_lang)
                self.active_backend = "nllb"
                if out:
                    return out
            except Exception as exc:
                logger.warning("NLLB translate failed (%s); falling back to Argos", exc)
                self.active_backend = "argos"

        self.active_backend = "argos"
        return self.argos.translate(clean, src_lang=src_lang, dst_lang=dst_lang)
