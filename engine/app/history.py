"""Lightweight SQLite persistence for live call translation transcripts."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any


class HistoryStore:
    """Thread-safe SQLite store for translation history under models_dir."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS transcripts (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        ts_ms INTEGER NOT NULL,
                        channel TEXT NOT NULL,
                        src_lang TEXT NOT NULL,
                        dst_lang TEXT NOT NULL,
                        source_text TEXT NOT NULL,
                        translated_text TEXT NOT NULL,
                        latency_ms INTEGER NOT NULL DEFAULT 0,
                        stt_ms INTEGER NOT NULL DEFAULT 0,
                        mt_ms INTEGER NOT NULL DEFAULT 0,
                        tts_ms INTEGER NOT NULL DEFAULT 0,
                        translator_backend TEXT NOT NULL DEFAULT 'nllb',
                        tts_warning TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_transcripts_ts ON transcripts (ts_ms DESC)"
                )

    def add(self, item: dict[str, Any]) -> dict[str, Any]:
        ts_ms = int(item.get("ts_ms") or time.time() * 1000)
        created_at = str(
            item.get("created_at")
            or datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()
        )
        record = {
            "id": str(item["id"]),
            "created_at": created_at,
            "ts_ms": ts_ms,
            "channel": str(item.get("channel", "outgoing")),
            "src_lang": str(item.get("src_lang", "")),
            "dst_lang": str(item.get("dst_lang", "")),
            "source_text": str(item.get("source_text", "")),
            "translated_text": str(item.get("translated_text", "")),
            "latency_ms": int(item.get("latency_ms", 0) or 0),
            "stt_ms": int(item.get("stt_ms", 0) or 0),
            "mt_ms": int(item.get("mt_ms", 0) or 0),
            "tts_ms": int(item.get("tts_ms", 0) or 0),
            "translator_backend": str(item.get("translator_backend", "nllb")),
            "tts_warning": item.get("tts_warning"),
        }
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO transcripts (
                        id, created_at, ts_ms, channel, src_lang, dst_lang,
                        source_text, translated_text, latency_ms, stt_ms, mt_ms, tts_ms,
                        translator_backend, tts_warning
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        record["created_at"],
                        record["ts_ms"],
                        record["channel"],
                        record["src_lang"],
                        record["dst_lang"],
                        record["source_text"],
                        record["translated_text"],
                        record["latency_ms"],
                        record["stt_ms"],
                        record["mt_ms"],
                        record["tts_ms"],
                        record["translator_backend"],
                        record["tts_warning"],
                    ),
                )
        return record

    def list_items(self, limit: int = 200, query: str = "") -> list[dict[str, Any]]:
        safe_limit = max(1, min(1000, int(limit)))
        clean_q = query.strip()
        with self._lock:
            with self._connect() as conn:
                if clean_q:
                    like = f"%{clean_q}%"
                    rows = conn.execute(
                        """
                        SELECT * FROM transcripts
                        WHERE source_text LIKE ? OR translated_text LIKE ?
                        ORDER BY ts_ms DESC
                        LIMIT ?
                        """,
                        (like, like, safe_limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM transcripts
                        ORDER BY ts_ms DESC
                        LIMIT ?
                        """,
                        (safe_limit,),
                    ).fetchall()
        items = [dict(r) for r in rows]
        items.reverse()  # chronological order for chat feed
        return items

    def clear(self) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM transcripts")
                return int(cur.rowcount or 0)

    def export_text(self, query: str = "") -> str:
        items = self.list_items(limit=1000, query=query)
        lines = []
        for it in items:
            who = "Anda" if it["channel"] == "outgoing" else "Lawan Bicara"
            ts = it["created_at"][:19].replace("T", " ")
            lines.append(f"[{ts}] {who} ({it['src_lang']} -> {it['dst_lang']})")
            lines.append(f"  Terjemahan : {it['translated_text']}")
            lines.append(f"  Asli       : {it['source_text']}")
            lines.append("")
        return "\n".join(lines)
