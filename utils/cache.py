"""
SQLite-backed cache for translation results.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger(__name__)


class TranslationCache:
    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS translations (
                    src_lang TEXT NOT NULL,
                    tgt_lang TEXT NOT NULL,
                    source TEXT NOT NULL,
                    translated TEXT NOT NULL,
                    PRIMARY KEY (src_lang, tgt_lang, source)
                )
                """
            )
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.commit()
            LOGGER.debug("Translation cache initialised at %s", self.path)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def lookup(self, source: str, src_lang: str, tgt_lang: str) -> Optional[str]:
        conn = self._connection
        with self._lock:
            cursor = conn.execute(
                """
                SELECT translated FROM translations
                WHERE src_lang = ? AND tgt_lang = ? AND source = ?
                """,
                (src_lang, tgt_lang, source),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def store(self, source: str, src_lang: str, tgt_lang: str, translated: str) -> None:
        conn = self._connection
        with self._lock:
            conn.execute(
                """
                INSERT OR REPLACE INTO translations (src_lang, tgt_lang, source, translated)
                VALUES (?, ?, ?, ?)
                """,
                (src_lang, tgt_lang, source, translated),
            )
            conn.commit()

    @property
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Cache connection not initialised. Call connect() first.")
        return self._conn