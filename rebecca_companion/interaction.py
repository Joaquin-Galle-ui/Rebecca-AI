from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum


class InteractionPhase(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    UNDERSTANDING = "understanding"
    THINKING = "thinking"
    OBSERVING = "observing"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class InteractionLease:
    identifier: int
    owner: str


@dataclass(frozen=True)
class InteractionSnapshot:
    phase: InteractionPhase
    owner: str
    passive_listening: bool


class InteractionCoordinator:
    """Serializa las actividades que no deben pisarse entre sí.

    La escucha continua es pasiva y puede quedar armada mientras Rebecca está
    disponible. Pensar, observar y hablar usan un lease exclusivo; un resultado
    viejo no puede liberar por accidente una interacción posterior.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._next_identifier = 0
        self._lease: InteractionLease | None = None
        self._phase = InteractionPhase.IDLE
        self._passive_listening = False

    def set_passive_listening(self, enabled: bool) -> None:
        with self._lock:
            self._passive_listening = bool(enabled)

    def try_begin(self, owner: str, phase: InteractionPhase) -> InteractionLease | None:
        with self._lock:
            if self._lease is not None:
                return None
            self._next_identifier += 1
            self._lease = InteractionLease(self._next_identifier, str(owner or "interaction"))
            self._phase = phase
            return self._lease

    def transition(self, lease: InteractionLease, phase: InteractionPhase) -> bool:
        with self._lock:
            if self._lease != lease:
                return False
            self._phase = phase
            return True

    def finish(self, lease: InteractionLease) -> bool:
        with self._lock:
            if self._lease != lease:
                return False
            self._lease = None
            self._phase = InteractionPhase.IDLE
            return True

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._lease is not None

    def snapshot(self) -> InteractionSnapshot:
        with self._lock:
            visible_phase = self._phase
            if self._lease is None and self._passive_listening:
                visible_phase = InteractionPhase.LISTENING
            return InteractionSnapshot(
                phase=visible_phase,
                owner=self._lease.owner if self._lease else "",
                passive_listening=self._passive_listening,
            )
