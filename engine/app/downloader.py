from fnmatch import fnmatch
from pathlib import Path
import threading
from typing import Callable

import httpx
from huggingface_hub import HfApi


class DownloadCancelled(Exception):
    """Raised when an active model or language package download is cancelled."""


def format_mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def stream_download_file(
    client: httpx.Client,
    url: str,
    dest: Path,
    cancel_event: threading.Event | None = None,
    on_chunk: Callable[[int, int | None], None] | None = None,
) -> int:
    """Stream-download a single URL to `dest` with chunk-level cancellation."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_name(dest.name + ".tmp")
    written = 0

    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            raw_len = resp.headers.get("content-length")
            content_length = int(raw_len) if raw_len and raw_len.isdigit() else None
            with tmp_path.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled("Unduhan dibatalkan oleh pengguna.")
                    f.write(chunk)
                    written += len(chunk)
                    if on_chunk is not None:
                        on_chunk(len(chunk), content_length)
        tmp_path.replace(dest)
        return written
    except BaseException:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def download_hf_files(
    repo_id: str,
    dest_dir: Path,
    allow_patterns: list[str],
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    """Download matching Hugging Face repository files with exact byte progress."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    info = HfApi().model_info(repo_id, files_metadata=True)

    targets: list[tuple[str, int]] = []
    for sib in info.siblings or []:
        name = sib.rfilename
        if any(fnmatch(name, pat) for pat in allow_patterns):
            targets.append((name, int(sib.size or 0)))

    if not targets:
        raise RuntimeError(f"Tidak ada file model yang cocok pada repo '{repo_id}'.")

    total_bytes = sum(size for _, size in targets)
    downloaded_bytes = 0
    for filename, size in targets:
        if (dest_dir / filename).exists():
            downloaded_bytes += size

    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        for filename, size in targets:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled("Unduhan dibatalkan oleh pengguna.")
            dest_file = dest_dir / filename
            if dest_file.exists():
                continue

            url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"

            def _on_chunk(delta: int, header_len: int | None, fname: str = filename) -> None:
                nonlocal downloaded_bytes, total_bytes
                downloaded_bytes += delta
                if total_bytes == 0 and header_len:
                    total_bytes = header_len
                if on_progress is not None:
                    on_progress(downloaded_bytes, total_bytes, fname)

            stream_download_file(
                client=client,
                url=url,
                dest=dest_file,
                cancel_event=cancel_event,
                on_chunk=_on_chunk,
            )

    if on_progress is not None and total_bytes > 0:
        on_progress(total_bytes, total_bytes, "done")
    return dest_dir
