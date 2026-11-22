import re

# Conservative word-boundary colloquial normalization for Indonesian spoken speech.
_ID_SPOKEN_MAP: dict[str, str] = {
    "gak": "tidak",
    "nggak": "tidak",
    "ngga": "tidak",
    "ga": "tidak",
    "gk": "tidak",
    "ndak": "tidak",
    "udah": "sudah",
    "udh": "sudah",
    "sdh": "sudah",
    "belom": "belum",
    "blm": "belum",
    "gimana": "bagaimana",
    "gmn": "bagaimana",
    "kenapa": "mengapa",
    "knp": "mengapa",
    "bentar": "sebentar",
    "ntar": "nanti",
    "aja": "saja",
    "doang": "saja",
    "banget": "sangat",
    "bgt": "sangat",
    "bener": "benar",
    "beneran": "sungguh",
    "emang": "memang",
    "emg": "memang",
    "kalo": "kalau",
    "kl": "kalau",
    "karna": "karena",
    "krn": "karena",
    "tapi": "tetapi",
    "tp": "tetapi",
    "sm": "dengan",
    "yg": "yang",
    "dgn": "dengan",
    "trs": "terus",
    "skrg": "sekarang",
    "org": "orang",
    "lg": "sedang",
    "ngetes": "mengetes",
    "ngecek": "mengecek",
    "ngomong": "berbicara",
    "ngerti": "mengerti",
    "kedengeran": "terdengar",
    "kelihatan": "terlihat",
    "siapin": "siapkan",
    "jelasin": "jelaskan",
    "dengerin": "dengarkan",
    "bantuin": "bantu",
    "bikin": "membuat",
    "dapet": "dapat",
    "pake": "memakai",
    "makasih": "terima kasih",
    "cuman": "hanya",
    "cuma": "hanya",
    "kayak": "seperti",
    "kyk": "seperti",
    "gitu": "begitu",
    "gini": "begini",
    "nih": "ini",
    "tuh": "itu",
    "sih": "",
    "dong": "",
    "deh": "",
    "kok": "",
    "lho": "",
    "loh": "",
}

_ID_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_ID_SPOKEN_MAP, key=len, reverse=True)) + r")\b",
    flags=re.IGNORECASE,
)


def _preserve_case(replacement: str, original: str) -> str:
    if not replacement:
        return ""
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def normalize_spoken_text(text: str, lang: str) -> str:
    """Normalize colloquial contractions before machine translation."""
    clean = " ".join(text.split()).strip()
    if not clean:
        return ""
    if lang != "id":
        return clean

    def _sub(match: re.Match[str]) -> str:
        word = match.group(1)
        rep = _ID_SPOKEN_MAP.get(word.lower(), word)
        return _preserve_case(rep, word)

    normalized = _ID_PATTERN.sub(_sub, clean)
    # Collapse extra spaces left by removed filler particles (sih/dong/deh/kok)
    normalized = re.sub(r"\s+([,.?!])", r"\1", normalized)
    normalized = " ".join(normalized.split()).strip()
    return normalized or clean
