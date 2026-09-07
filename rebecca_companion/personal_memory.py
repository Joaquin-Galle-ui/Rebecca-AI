from __future__ import annotations

import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MEMORY_PATH = Path(__file__).resolve().parent / "user_state" / "personal_memory.json"


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return normalized.encode("ascii", "ignore").decode("ascii").casefold()


def _clean_fact(value: str) -> str:
    value = re.split(r"[\n\r]", str(value or ""), maxsplit=1)[0]
    value = re.split(r"[.!?](?:\s|$)", value, maxsplit=1)[0]
    return re.sub(r"\s+", " ", value).strip(" ,;:-")[:180]


@dataclass(frozen=True)
class MemoryFact:
    kind: str
    text: str


def extract_explicit_memories(text: str) -> list[MemoryFact]:
    """Extrae sólo datos que el usuario expresa como gustos o recuerdos claros."""
    original = re.sub(r"\s+", " ", str(text or "")).strip()
    if not original:
        return []

    patterns = (
        ("nota", r"\b(?:record[aá]|acordate|quiero que recuerdes)\s+que\s+(.+)"),
        ("gusto", r"(?<!no )\bme\s+gustan?\s+(.+)"),
        ("preferencia", r"\bprefiero\s+(.+)"),
        ("rechazo", r"\bno\s+me\s+gustan?\s+(.+)"),
        ("fecha", r"\bmi\s+(?:cumplea(?:ñ|n)os|cumple)\s+(?:es|cae)\s+(?:el\s+)?(.+)"),
        (
            "favorito",
            r"\bmi\s+(juego|canci[oó]n|artista|banda|comida|color|pel[ií]cula|serie)\s+"
            r"favorit[oa]\s+es\s+(.+)",
        ),
    )
    memories: list[MemoryFact] = []
    for kind, pattern in patterns:
        match = re.search(pattern, original, flags=re.IGNORECASE)
        if not match:
            continue
        if kind == "favorito":
            value = _clean_fact(f"{match.group(1)} favorito: {match.group(2)}")
        else:
            value = _clean_fact(match.group(1))
        if len(value) >= 2:
            memories.append(MemoryFact(kind, value))
    unique: dict[tuple[str, str], MemoryFact] = {}
    for memory in memories:
        unique[(memory.kind, _plain(memory.text))] = memory
    return list(unique.values())


class PersonalMemory:
    """Memoria local pequeña, explícita y legible por el usuario."""

    def __init__(self, path: Path | str = DEFAULT_MEMORY_PATH, limit: int = 60) -> None:
        self.path = Path(path)
        self.limit = max(10, int(limit))
        self._lock = threading.RLock()
        self._items: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            items = payload.get("items", []) if isinstance(payload, dict) else []
            self._items = [item for item in items if isinstance(item, dict) and item.get("text")]
        except (OSError, ValueError, TypeError):
            self._items = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = {"version": 1, "items": self._items[-self.limit :]}
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def learn(self, text: str) -> list[MemoryFact]:
        learned = extract_explicit_memories(text)
        if not learned:
            return []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            for fact in learned:
                key = (fact.kind, _plain(fact.text))
                self._items = [
                    item
                    for item in self._items
                    if (str(item.get("kind", "")), _plain(str(item.get("text", "")))) != key
                ]
                self._items.append({"kind": fact.kind, "text": fact.text, "updated_at": now})
            self._items = self._items[-self.limit :]
            self._save()
        return learned

    def summary(self, max_items: int = 18, max_chars: int = 1400) -> str:
        with self._lock:
            items = list(self._items[-max(1, int(max_items)) :])
        if not items:
            return ""
        lines = [f"- {item.get('kind', 'dato')}: {item.get('text', '')}" for item in items]
        result = "\n".join(lines)
        return result[:max_chars].rstrip()

    def items(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(item) for item in self._items]
