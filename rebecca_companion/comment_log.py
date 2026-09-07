"""Registro acotado de comentarios; nunca almacena capturas, audio ni claves."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4


class CommentJournal:
    FIELDS = {"text", "source", "role", "request_id", "age_ms", "duration_ms", "reason", "mode"}

    def __init__(self, path: Path | None = None, *, max_bytes: int = 512_000):
        self.path = path or Path(__file__).parent / "user_state" / "comments.jsonl"
        self.session_id = uuid4().hex[:12]
        self.max_bytes = max_bytes
        self._handler = None
        self._lock = threading.RLock()

    def record(self, event: str, **fields) -> None:
        if os.getenv("REBECCA_COMMENT_LOG", "1").casefold() in {"0", "false", "off"}:
            return
        with self._lock:
            try:
                if self._handler is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self._handler = RotatingFileHandler(
                        self.path, maxBytes=self.max_bytes, backupCount=2, encoding="utf-8"
                    )
                    self._handler.setFormatter(logging.Formatter("%(message)s"))
                row = {"at": datetime.now(timezone.utc).isoformat(), "session": self.session_id, "event": event}
                for name, value in fields.items():
                    if name in self.FIELDS:
                        row[name] = value[:400] if isinstance(value, str) else value
                message = json.dumps(row, ensure_ascii=False)
                self._handler.handle(logging.LogRecord("comments", logging.INFO, "", 0, message, (), None))
            except (OSError, ValueError):
                # Un disco lleno no debe detener el audio ni la captura.
                pass

    def close(self):
        with self._lock:
            if self._handler is not None:
                self._handler.close()
                self._handler = None
