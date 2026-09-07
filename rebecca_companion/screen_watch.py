from __future__ import annotations

import threading
import time
import re
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable

from PIL import Image, ImageChops, ImageStat

from .capture import ScreenCaptureError, active_window_info, capture_active_monitor
from .client import RebeccaClient, RebeccaConnectionError
from .dbd_watch import DbdLocalObserver
from .live_watch import GeminiLiveVision, LiveComment, LiveQuestionError
from .comment_log import CommentJournal


def active_window_title() -> str:
    return active_window_info()[0]


def capture_thumbnail(size=(96, 54)) -> Image.Image:
    captured = capture_active_monitor()
    return captured.image.resize(size, Image.Resampling.BILINEAR).convert("L")


def image_difference_score(previous: Image.Image, current: Image.Image) -> float:
    if previous.size != current.size:
        current = current.resize(previous.size, Image.Resampling.BILINEAR)
    difference = ImageChops.difference(previous, current)
    return float(ImageStat.Stat(difference).mean[0])


@dataclass
class SceneChangeDetector:
    threshold: float = 7.5
    previous: Image.Image | None = None

    def changed(self, current: Image.Image) -> bool:
        if self.previous is None:
            self.previous = current.copy()
            return False
        score = image_difference_score(self.previous, current)
        self.previous = current.copy()
        return score >= self.threshold


class SpectatorMode:
    COOLDOWNS = {"silenciosa": 120, "normal": 60, "charlatana": 30}
    IGNORED_WINDOWS = ("rebecca companion", "n8n", "telegram", "explorador de archivos")
    SCENE_SETTLE_SECONDS = 0.65
    # Sólo evita capturar en medio de un corte brusco. En un juego normal los
    # píxeles cambian todo el tiempo, así que un umbral bajo silenciaba a
    # Rebecca incluso cuando seguíamos dentro del mismo juego.
    TRANSITION_THRESHOLD = 24.0
    DUPLICATE_TTL_SECONDS = 600.0

    def __init__(
        self,
        client: RebeccaClient,
        on_comment: Callable[[str], bool | None],
        on_status: Callable[[str], None],
        *,
        on_observation_start: Callable[[], object | None] | None = None,
        on_observation_speaking: Callable[[object], None] | None = None,
        on_observation_end: Callable[[object], None] | None = None,
        can_comment: Callable[[], bool] | None = None,
    ):
        self.client = client
        self.on_comment = on_comment
        self.on_status = on_status
        self.detector = SceneChangeDetector()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.mode = "normal"
        self._generation = 0
        self.on_observation_start = on_observation_start
        self.on_observation_speaking = on_observation_speaking
        self.on_observation_end = on_observation_end
        self._recent_comments: deque[tuple[float, str]] = deque(maxlen=8)
        self._comments_lock = threading.Lock()
        self._speech_lock = threading.Lock()
        self.journal = CommentJournal()
        self.game_role = "automatico"
        self.dbd_observer = DbdLocalObserver()
        self.can_comment = can_comment or (lambda: True)
        self._conversation = threading.Event()
        self._resume_at = 0.0
        self._dialogue_revision = 0
        self.live_vision = GeminiLiveVision(
            self._handle_live_comment, self.on_status, journal=self.journal,
            can_comment=self.can_comment, on_frame=self.client.game_context.observe_window,
            context_provider=lambda: self.client.game_context.summary(max_chars=1200),
        )

    def pause_for_conversation(self, paused: bool) -> None:
        if paused:
            if not self._conversation.is_set():
                self._dialogue_revision += 1
            self._conversation.set()
        else:
            if self._conversation.is_set():
                self._resume_at = time.monotonic() + 8.0
            self._conversation.clear()
        self.live_vision.pause_comments(paused)

    def ask_about_game(self, text: str) -> str:
        try:
            return self.live_vision.ask_question(text, self.client.game_context.summary())
        except LiveQuestionError as exc:
            # Do not silently spend another model call or pretend to see the
            # screen through the ordinary text-only chat after a visual failure.
            return str(exc)

    def _conversation_blocks_comment(self) -> bool:
        return self._conversation.is_set() or time.monotonic() < self._resume_at or not self.can_comment()

    def set_game_role(self, role: str) -> None:
        normalized = str(role or "automatico").strip().casefold()
        self.game_role = normalized if normalized in {"automatico", "asesino", "superviviente"} else "automatico"
        self.live_vision.set_role(self.game_role)

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, mode: str = "normal") -> None:
        self.mode = mode if mode in self.COOLDOWNS else "normal"
        if self.running:
            return
        self.stop_event.clear()
        self.detector.previous = None
        self._generation += 1
        generation = self._generation
        live_started = self.live_vision.start(self.mode, self.game_role)
        self.thread = threading.Thread(target=self._loop, args=(generation,), daemon=True)
        self.thread.start()
        if live_started:
            self.on_status("Conectando la visión continua de Rebecca...")

    def stop(self) -> None:
        self.stop_event.set()
        self.live_vision.stop()
        self._generation += 1
        self.client.game_context.clear()
        self.on_status("Modo espectadora apagado")

    def request_live_comment(self) -> bool:
        return self.live_vision.connected and self.live_vision.request_comment()

    def _handle_live_comment(self, result: LiveComment) -> bool:
        reason = self.live_vision.invalid_reason(result)
        if reason:
            self.journal.record("discarded", source="live", request_id=result.request_id, reason=reason)
            return False
        return self._deliver_comment(
            result.text, result.window_title, source="live", observed_at=result.requested_at,
            max_age=result.max_age, request_id=result.request_id,
        )

    def _deliver_comment(
        self, comment: str, observed_title: str, *, source: str,
        observed_at: float, max_age: float = 18.0, request_id: int = 0,
    ) -> bool:
        fields = dict(source=source, text=comment, role=self.game_role, request_id=request_id)
        reason = ""
        current_title = active_window_title()
        remaining = max_age - (time.monotonic() - observed_at)
        if self.stop_event.is_set():
            reason = "stopped"
        elif self._conversation_blocks_comment():
            reason = "user_turn"
        elif remaining <= 0:
            reason = "expired"
        elif observed_title.strip().casefold() != current_title.strip().casefold():
            reason = "window_changed"
        elif self._is_repeated_comment(comment, time.monotonic(), remember=False):
            reason = "duplicate"
        if reason:
            self.journal.record("discarded", **fields, reason=reason)
            return False
        if not self._speech_lock.acquire(blocking=False):
            self.journal.record("discarded", **fields, reason="busy")
            return False
        activity = None
        try:
            activity = self.on_observation_start() if self.on_observation_start else None
            if self.on_observation_start is not None and activity is None:
                self.journal.record("discarded", **fields, reason="busy")
                return False
            if activity is not None and self.on_observation_speaking is not None:
                if self.on_observation_speaking(activity) is False:
                    self.journal.record('discarded', **fields, reason='user_turn')
                    return False
            accepted = self.on_comment(comment)
            if accepted is False:
                self.journal.record("discarded", **fields, reason="busy")
                return False
            started = time.monotonic()
            self.journal.record("submitted_to_voice", **fields, age_ms=round((started - observed_at) * 1000))
            response = self.client.hablar(
                comment, expires_at=time.time() + max_age - (started - observed_at)
            )
            if response.get("status") != "success":
                self.journal.record("discarded" if response.get("status") == "skipped" else "failed",
                                    **fields, reason=response.get("reason", "voice_error"))
                return False
            if self.stop_event.is_set():
                return False
            self._is_repeated_comment(comment, time.monotonic())
            self.client.remember_observation(observed_title, comment, observed_at=observed_at)
            self.live_vision.note_spoken(comment)
            self.journal.record("voice_completed", **fields, duration_ms=round((time.monotonic() - started) * 1000))
            return True
        except RebeccaConnectionError as exc:
            self.journal.record("failed", **fields, reason="voice_connection")
            self.on_status(str(exc))
            return False
        finally:
            if activity is not None and self.on_observation_end is not None:
                self.on_observation_end(activity)
            self._speech_lock.release()

    @staticmethod
    def _normalized_comment(comment: str) -> str:
        return re.sub(r"[^a-z0-9áéíóúüñ]+", " ", comment.casefold()).strip()

    def _is_repeated_comment(self, comment: str, now: float, *, remember: bool = True) -> bool:
        normalized = self._normalized_comment(comment)
        if not normalized:
            return True
        with self._comments_lock:
            while self._recent_comments and now - self._recent_comments[0][0] > self.DUPLICATE_TTL_SECONDS:
                self._recent_comments.popleft()
            for _created_at, previous in self._recent_comments:
                if normalized == previous or SequenceMatcher(None, normalized, previous).ratio() >= 0.9:
                    return True
            if remember:
                self._recent_comments.append((now, normalized))
        return False

    def _loop(self, generation: int) -> None:
        self.on_status(f"Modo espectadora: {self.mode}")
        last_attempt = time.monotonic() - self.COOLDOWNS[self.mode] + 15
        offline_reported = False
        visual_failures = 0

        while not self.stop_event.wait(1.0):
            if generation != self._generation:
                return
            try:
                title = active_window_title()
                if any(blocked in title.lower() for blocked in self.IGNORED_WINDOWS):
                    continue
                if self._conversation_blocks_comment():
                    continue
                if "deadbydaylight" in title.casefold().replace(" ", ""):
                    local_observed_at = time.monotonic()
                    try:
                        local_event = self.dbd_observer.poll(self.game_role)
                    except (ScreenCaptureError, OSError, RuntimeError):
                        local_event = None
                    if local_event is not None:
                        accepted = self._deliver_comment(
                            local_event.comment, title, source="ocr", observed_at=local_observed_at
                        )
                        if accepted:
                            last_attempt = time.monotonic()
                        continue
                # Live ya está enviando cuadros y conserva el contexto entre
                # escenas. El ciclo clásico queda activo únicamente como
                # respaldo si la sesión no logra reconectarse en 20 segundos.
                if self.live_vision.connected or not self.live_vision.fallback_ready:
                    continue
                frame = capture_thumbnail()
                if self.stop_event.is_set() or generation != self._generation:
                    return
                self.client.game_context.observe_window(title)
                if not self.detector.changed(frame):
                    continue
                if time.monotonic() - last_attempt < self.COOLDOWNS[self.mode]:
                    continue
                if self.stop_event.wait(self.SCENE_SETTLE_SECONDS):
                    return
                stable_frame = capture_thumbnail()
                if image_difference_score(frame, stable_frame) >= self.TRANSITION_THRESHOLD:
                    # Fue una transición, animación o cambio fugaz. Esperar a la
                    # próxima escena estable evita comentarios sobre algo viejo.
                    continue
                if not self.client.health():
                    if not offline_reported:
                        self.on_status("Rebecca Core está apagado")
                        offline_reported = True
                    continue

                offline_reported = False
                last_attempt = time.monotonic()
                requested_role = self.game_role
                dialogue_revision = self._dialogue_revision
                role_context = (
                    f" Rol confirmado por Usuario: {self.game_role}."
                    if self.game_role != "automatico"
                    else " Rol todavía no confirmado."
                )
                comment = self.client.opinar_juego(f"Juego o ventana: {title}.{role_context}")
                visual_failures = 0
                if self.stop_event.is_set() or generation != self._generation:
                    return
                if dialogue_revision != self._dialogue_revision:
                    continue
                if requested_role != self.game_role:
                    self.journal.record("discarded", source="fallback", reason="context_changed")
                    continue
                current_title = active_window_title()
                if current_title.strip().lower() != title.strip().lower():
                    self.on_status("Cambié de ventana; descarté el comentario anterior")
                    continue
                normalized = comment.strip().lower()
                no_event = (
                    not normalized
                    or normalized.startswith(("error de captura:", "error de visión:"))
                    or "sin evento claro" in normalized
                    or "nada destacable" in normalized
                    or "no hay nada concreto" in normalized
                )
                if not no_event:
                    self._deliver_comment(comment, title, source="fallback", observed_at=last_attempt)
            except RebeccaConnectionError as exc:
                self.on_status(str(exc))
                # Retroceso progresivo: evita que una saturación temporal cree
                # una tormenta de consultas y queme la cuota gratuita.
                visual_failures += 1
                retry_delay = min(120.0, 20.0 * (2 ** (visual_failures - 1)))
                retry_delay = min(float(self.COOLDOWNS[self.mode]), retry_delay)
                last_attempt = time.monotonic() - self.COOLDOWNS[self.mode] + retry_delay
            except (ScreenCaptureError, OSError) as exc:
                self.on_status(str(exc))
            except Exception as exc:
                self.on_status(f"El modo espectadora se frenó: {exc}")
                self.stop_event.set()
                self.live_vision.stop()
