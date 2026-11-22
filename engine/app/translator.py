import threading
import argostranslate.package
import argostranslate.translate


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

