from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "user_state" / "settings.json"


class CompanionSettings:
    def __init__(self, path: Path | str = DEFAULT_SETTINGS_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._data = payload
        except (OSError, ValueError, TypeError):
            pass

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
