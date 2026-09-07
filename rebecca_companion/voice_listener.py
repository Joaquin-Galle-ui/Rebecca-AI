from __future__ import annotations

import io
import math
import re
import threading
import time
import unicodedata
import wave
from collections import deque
from typing import Callable

import numpy as np
import sounddevice as sd


WAKE_PATTERN = re.compile(r"^\s*[¡¿\"']*(?:(?:hola|oye|che|hey)\s*[,!:]?\s+)?(?:rebecca|rebeca|becca)\b[\s,.:;!?-]*(.*)$", re.IGNORECASE | re.DOTALL)
SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.1
CONVERSATION_EXIT_PATTERN = re.compile(
    r"^(?:rebecca\s+)?(?:listo(?:\s+gracias)?(?:\s+rebecca)?|gracias(?:\s+rebecca)?|ya esta|"
    r"dejemoslo aca|terminemos(?:\s+la charla)?|fin de (?:la )?conversacion|"
    r"podes dejar de escuchar|volve a esperar (?:la palabra )?rebecca)$"
)


def _plain(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def is_conversation_exit(text: str) -> bool:
    return bool(CONVERSATION_EXIT_PATTERN.fullmatch(_plain(text)))


def input_devices() -> list[tuple[int, str]]:
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if int(info.get("max_input_channels", 0)) > 0:
            devices.append((index, str(info.get("name", f"Micrófono {index}"))))
    return devices


def output_devices() -> list[tuple[int, str]]:
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if int(info.get("max_output_channels", 0)) > 0:
            devices.append((index, str(info.get("name", f"Salida {index}"))))
    return devices


def extract_wake_command(text: str) -> str | None:
    match = WAKE_PATTERN.match(text.strip())
    if not match:
        return None
    command = match.group(1).strip()
    return command or "decime"


def compatible_input_sample_rate(device: int | None) -> int:
    """Elige una frecuencia que el micrófono realmente acepte.

    Algunos dispositivos USB expuestos por Windows/WASAPI sólo admiten 48 kHz,
    aunque Whisper pueda trabajar luego con cualquier WAV válido.
    """
    info = sd.query_devices(device, "input")
    default_rate = int(round(float(info.get("default_samplerate", SAMPLE_RATE))))
    candidates = [default_rate, 48000, 44100, 32000, 24000, 16000]
    attempted: set[int] = set()
    for sample_rate in candidates:
        if sample_rate <= 0 or sample_rate in attempted:
            continue
        attempted.add(sample_rate)
        try:
            sd.check_input_settings(
                device=device,
                channels=1,
                dtype="int16",
                samplerate=sample_rate,
            )
            return sample_rate
        except Exception:
            continue
    name = str(info.get("name", f"dispositivo {device}"))
    raise RuntimeError(f'El micrófono "{name}" no ofrece un formato mono compatible')


def encode_wav(chunks: list[np.ndarray], sample_rate: int = SAMPLE_RATE) -> bytes:
    audio = np.concatenate(chunks).astype(np.int16, copy=False)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio.tobytes())
    return output.getvalue()


def record_utterance(
    on_status: Callable[[str], None],
    start_timeout: float = 7.0,
    max_seconds: float = 15.0,
    silence_seconds: float = 1.1,
    voice_threshold: float = 320.0,
    device: int | None = None,
    announce_listening: bool = True,
    announce_detected_speech: bool = True,
    min_voice_seconds: float = 0.35,
    on_speech_start: Callable[[], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> bytes | None:
    """Graba desde la primera voz y corta al detectar silencio; no envía silencio a n8n."""
    pre_roll: deque[np.ndarray] = deque(maxlen=3)
    chunks: list[np.ndarray] = []
    started = False
    voiced_blocks = 0
    silent_for = 0.0
    started_at = time.monotonic()
    speech_started_at = None
    sample_rate = compatible_input_sample_rate(device)
    block_size = max(1, int(sample_rate * BLOCK_SECONDS))
    block_seconds = block_size / sample_rate

    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=block_size,
        device=device,
    ) as stream:
        if announce_listening:
            on_status(f"Escuchando... ({sample_rate // 1000} kHz)")
        while True:
            if cancelled and cancelled():
                return None
            data, _overflowed = stream.read(block_size)
            samples = data[:, 0].copy()
            level = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            elapsed = time.monotonic() - started_at

            if not started:
                pre_roll.append(samples)
                if level >= voice_threshold:
                    started = True
                    speech_started_at = time.monotonic()
                    if on_speech_start:
                        on_speech_start()
                    voiced_blocks = 1
                    chunks.extend(pre_roll)
                    if announce_detected_speech:
                        on_status("Te estoy escuchando...")
                elif elapsed >= start_timeout:
                    return None
                continue

            chunks.append(samples)
            if level >= voice_threshold:
                voiced_blocks += 1
            silent_for = silent_for + block_seconds if level < voice_threshold else 0.0
            if silent_for >= silence_seconds or time.monotonic() - speech_started_at >= max_seconds:
                break

    # Un golpe de teclado o un ruido corto no es una frase. Evitar mandarlo a
    # transcribir reduce activaciones fantasma y consumo innecesario del modelo.
    minimum_voiced_blocks = max(2, math.ceil(min_voice_seconds / block_seconds))
    if len(chunks) < 4 or voiced_blocks < minimum_voiced_blocks:
        return None
    return encode_wav(chunks, sample_rate)


class VoiceListener:
    """Escucha manual o continua con VAD local y transcripción mediante n8n/Groq."""

    def __init__(
        self,
        transcribe: Callable[[bytes], str],
        on_text: Callable[[str], None],
        on_status: Callable[[str], None],
        on_wake: Callable[[], None] | None = None,
        on_capture_end: Callable[[], None] | None = None,
        on_followup_text: Callable[[str], None] | None = None,
        conversation_timeout: float = 45.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.transcribe = transcribe
        self.on_text = on_text
        self.on_status = on_status
        self.on_wake = on_wake or (lambda: None)
        self.on_capture_end = on_capture_end or (lambda: None)
        self.on_followup_text = on_followup_text or self.on_text
        self.conversation_timeout = max(15.0, min(120.0, float(conversation_timeout)))
        self._clock = clock
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.single_thread: threading.Thread | None = None
        self._paused_until = 0.0
        self._audio_generation = 0
        self._record_lock = threading.Lock()
        self._processing = threading.Event()
        self._capturing_speech = threading.Event()
        self._conversation_until = 0.0
        self._conversation_transcriptions = 0
        self._conversation_lock = threading.RLock()
        try:
            default_input = sd.default.device[0]
        except (TypeError, IndexError):
            default_input = sd.default.device
        self.device = int(default_input) if default_input is not None and int(default_input) >= 0 else None

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    @property
    def processing(self) -> bool:
        return self._processing.is_set()

    @property
    def capturing_speech(self) -> bool:
        return self._capturing_speech.is_set()

    @property
    def conversation_active(self) -> bool:
        with self._conversation_lock:
            return self._conversation_until > self._clock()

    def begin_conversation(self) -> None:
        with self._conversation_lock:
            self._conversation_until = self._clock() + self.conversation_timeout
            self._conversation_transcriptions = 0

    def close_conversation(self, *, announce: bool = True) -> None:
        with self._conversation_lock:
            was_active = self._conversation_until > 0
            self._conversation_until = 0.0
            self._conversation_transcriptions = 0
        if announce and was_active:
            self.on_status('Charla cerrada · volvé a decir “Rebecca” para llamarme')

    def _renew_conversation(self) -> None:
        with self._conversation_lock:
            if self._conversation_until > 0:
                self._conversation_until = self._clock() + self.conversation_timeout

    def _conversation_expired(self) -> bool:
        with self._conversation_lock:
            if not self._conversation_until or self._clock() < self._conversation_until:
                return False
            self._conversation_until = 0.0
            self._conversation_transcriptions = 0
            return True

    def begin_processing(self) -> None:
        self._audio_generation += 1
        self._processing.set()

    def pause_for(self, seconds: float) -> None:
        self._audio_generation += 1
        self._paused_until = max(self._paused_until, time.monotonic() + seconds)

    def finish_processing(self, settle_seconds: float = 0.35) -> None:
        """Reanuda la escucha cuando la respuesta ya terminó de procesarse."""
        self.pause_for(settle_seconds)
        self._processing.clear()
        if self.conversation_active:
            self._renew_conversation()
            self.on_status('Charla abierta · podés seguir hablando sin repetir “Rebecca”')

    def set_device(self, device: int | None) -> None:
        self._audio_generation += 1
        self.device = device

    def listen_once(self) -> None:
        if self.running or (self.single_thread and self.single_thread.is_alive()):
            self.on_status("Rebecca ya está escuchando")
            return
        self.stop_event.clear()
        self.single_thread = threading.Thread(target=lambda: self._listen_once(False), daemon=True)
        self.single_thread.start()

    def start_continuous(self) -> None:
        if self.running:
            return
        self.stop_event.clear()
        self._processing.clear()
        self.thread = threading.Thread(target=self._continuous_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._audio_generation += 1
        self.stop_event.set()
        self._processing.clear()
        self.close_conversation(announce=False)
        self.on_status("Escucha activa apagada")

    def _listen_once(self, require_wake_word: bool) -> None:
        if time.monotonic() < self._paused_until:
            return
        try:
            generation = self._audio_generation
            cancelled = lambda: self.stop_event.is_set() or generation != self._audio_generation
            open_conversation = require_wake_word and self.conversation_active
            if not require_wake_word:
                self._capturing_speech.set()
            with self._record_lock:
                audio = record_utterance(
                    self.on_status,
                    start_timeout=4.0 if open_conversation else (3.0 if require_wake_word else 8.0),
                    silence_seconds=1.35 if open_conversation else (1.8 if require_wake_word else 1.1),
                    device=self.device,
                    announce_listening=not require_wake_word,
                    # En escucha activa no cambiar el estado por cualquier
                    # sonido ambiente: primero hay que confirmar la palabra.
                    announce_detected_speech=not require_wake_word,
                    on_speech_start=self._capturing_speech.set,
                    cancelled=cancelled,
                )
            if not audio or cancelled() or time.monotonic() < self._paused_until:
                if not require_wake_word:
                    self.on_status("No detecté una frase")
                return
            if not require_wake_word:
                self.on_status("Entendiendo lo que dijiste...")
            elif open_conversation:
                with self._conversation_lock:
                    self._conversation_transcriptions += 1
                    if self._conversation_transcriptions > 8:
                        self.close_conversation()
                        return
            self.on_status('Procesando voz detectada...' if require_wake_word else 'Entendiendo lo que dijiste...')
            text = self.transcribe(audio).strip()
            # TTS or Stop may have started while a slow transcription was in
            # flight. Never turn Rebecca's own audio into a new user command.
            if cancelled() or time.monotonic() < self._paused_until:
                return
            if not require_wake_word:
                manual_wake = WAKE_PATTERN.match(text)
                if manual_wake:
                    text = manual_wake.group(1).strip() or "decime"
                    self.begin_conversation()
            if require_wake_word:
                wake_match = WAKE_PATTERN.match(text)
                explicit_wake = wake_match is not None
                if not explicit_wake and not open_conversation:
                    # El audio ambiente se descarta sin cambiar el estado de la
                    # interfaz: no hacerle creer a Usuario que Rebecca fue llamada.
                    self.on_status('Voz sin llamada reconocida · decí “Rebecca” al principio')
                    self.pause_for(0.15)
                    return
                if explicit_wake:
                    self.on_wake()
                    text = wake_match.group(1).strip()
                if explicit_wake and not text:
                    self.on_status("Sí, te escucho...")
                    with self._record_lock:
                        follow_up = record_utterance(
                            self.on_status,
                            start_timeout=6.0,
                            silence_seconds=1.5,
                            device=self.device,
                            announce_listening=False,
                            announce_detected_speech=True,
                            cancelled=cancelled,
                        )
                    if not follow_up:
                        self.on_status('Escucha activa: decí “Rebecca” y la orden')
                        return
                    self.on_status("Entendiendo la orden...")
                    text = self.transcribe(follow_up).strip()
                    if cancelled():
                        return
                    repeated_wake = WAKE_PATTERN.match(text)
                    if repeated_wake:
                        text = repeated_wake.group(1).strip()
                    if not text:
                        self.on_status('Escucha activa: decí “Rebecca” y la orden')
                        return
                if is_conversation_exit(text):
                    self.close_conversation()
                    return
                if explicit_wake:
                    self.begin_conversation()
            if text:
                self.on_status(f"Te escuché: {text}")
                self.begin_processing()
                if require_wake_word and open_conversation and not explicit_wake:
                    self._renew_conversation()
                    self.on_followup_text(text)
                else:
                    self.on_text(text)
        except Exception as exc:
            self._processing.clear()
            if not self.stop_event.is_set():
                self.on_status(f"No pude usar el micrófono: {exc}")
                self.pause_for(2.0)
        finally:
            self._capturing_speech.clear()
            self.on_capture_end()

    def _continuous_loop(self) -> None:
        self.on_status('Escucha activa: decí “Rebecca” y la orden')
        while not self.stop_event.is_set():
            if self._conversation_expired():
                self.on_status('La charla quedó en pausa · decí “Rebecca” para continuar')
            if self._processing.is_set() or time.monotonic() < self._paused_until:
                self.stop_event.wait(0.25)
                continue
            self._listen_once(True)
            self.stop_event.wait(0.15)
