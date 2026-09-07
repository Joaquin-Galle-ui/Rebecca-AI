from __future__ import annotations

import ctypes
import random
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import keyboard
import mss

from .avatar import AvatarRenderer, infer_expression
from .client import RebeccaClient, RebeccaConnectionError
from .command_router import route_command
from .interaction import InteractionCoordinator, InteractionLease, InteractionPhase
from .instance_control import CompanionControlServer
from .screen_watch import SpectatorMode, active_window_title
from .settings import CompanionSettings
from .tray import CompanionTray
from .voice_listener import VoiceListener, input_devices, output_devices


ROOT = Path(__file__).resolve().parent
NEUTRAL_BACKGROUND = "#202833"
TRANSPARENT_BACKGROUND = "#010203"
WINDOW_WIDTH = 390
WINDOW_HEIGHT = 740
AVATAR_SIZE = 360


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", ctypes.c_ulong),
    ]


def _monitor_with_work_area(screen: dict[str, int]) -> dict[str, int]:
    result = dict(screen)
    try:
        user32 = ctypes.windll.user32
        center = _Point(
            int(screen["left"]) + int(screen["width"]) // 2,
            int(screen["top"]) + int(screen["height"]) // 2,
        )
        user32.MonitorFromPoint.argtypes = [_Point, ctypes.c_ulong]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor_handle = user32.MonitorFromPoint(center, 2)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if monitor_handle and user32.GetMonitorInfoW(ctypes.c_void_p(monitor_handle), ctypes.byref(info)):
            result.update(
                work_left=int(info.rcWork.left),
                work_top=int(info.rcWork.top),
                work_width=int(info.rcWork.right - info.rcWork.left),
                work_height=int(info.rcWork.bottom - info.rcWork.top),
            )
    except Exception:
        pass
    return result


def physical_monitors() -> list[dict[str, int]]:
    """Devuelve los monitores físicos en el mismo orden que muestra Windows."""
    try:
        with mss.MSS() as capture:
            screens = capture.monitors[1:]
            if screens:
                return [
                    _monitor_with_work_area({
                        "left": int(screen["left"]),
                        "top": int(screen["top"]),
                        "width": int(screen["width"]),
                        "height": int(screen["height"]),
                    })
                    for screen in screens
                ]
    except Exception:
        pass
    return [{"left": 0, "top": 0, "width": 1920, "height": 1080}]


def _position_geometry(width: int, height: int, x: int, y: int) -> str:
    x_part = f"+{x}" if x >= 0 else str(x)
    y_part = f"+{y}" if y >= 0 else str(y)
    return f"{width}x{height}{x_part}{y_part}"


def monitor_geometry(
    monitor: dict[str, int],
    width: int,
    height: int,
    *,
    placement: str = "center",
    saved_position: object = None,
) -> str:
    x, y = monitor_position(
        monitor,
        width,
        height,
        placement=placement,
        saved_position=saved_position,
    )
    return _position_geometry(width, height, x, y)


def monitor_position(
    monitor: dict[str, int],
    width: int,
    height: int,
    *,
    placement: str = "center",
    saved_position: object = None,
) -> tuple[int, int]:
    left = int(monitor.get("work_left", monitor["left"]))
    top = int(monitor.get("work_top", monitor["top"]))
    area_width = int(monitor.get("work_width", monitor["width"]))
    area_height = int(monitor.get("work_height", monitor["height"]))
    right = left + area_width
    bottom = top + area_height
    if (
        isinstance(saved_position, (list, tuple))
        and len(saved_position) == 2
        and all(isinstance(value, (int, float)) for value in saved_position)
    ):
        x, y = int(saved_position[0]), int(saved_position[1])
        if left <= x <= right - width and top <= y <= bottom - height:
            return x, y
    if placement == "bottom-right":
        x = max(left, right - width - 35)
        y = max(top, bottom - height - 35)
    else:
        x = left + max(0, (area_width - width) // 2)
        y = top + max(0, (area_height - height) // 2)
    return x, y


def dashboard_height(monitor: dict[str, int]) -> int:
    work_height = int(monitor.get("work_height", monitor["height"]))
    # Tk informa el tamaño del área cliente; dejamos espacio para el marco y
    # un pequeño margen para no tocar la barra de tareas.
    return max(520, min(WINDOW_HEIGHT, work_height - 42))


def monitor_number_at(monitors: list[dict[str, int]], x: int, y: int) -> int | None:
    for number, monitor in enumerate(monitors, 1):
        left, top = int(monitor["left"]), int(monitor["top"])
        if left <= x < left + int(monitor["width"]) and top <= y < top + int(monitor["height"]):
            return number
    return None


def move_window_absolute(root: tk.Tk, x: int, y: int) -> None:
    """Mueve una ventana a coordenadas reales del escritorio virtual de Windows."""
    try:
        root.update_idletasks()
        user32 = ctypes.windll.user32
        user32.GetAncestor.restype = ctypes.c_void_p
        window_handle = user32.GetAncestor(int(root.winfo_id()), 2) or int(root.winfo_id())
        moved = user32.SetWindowPos(
            ctypes.c_void_p(window_handle),
            None,
            int(x),
            int(y),
            0,
            0,
            0x0001 | 0x0004 | 0x0010,  # conservar tamaño, orden Z y activación
        )
        if not moved:
            raise OSError("SetWindowPos no pudo mover la ventana")
    except Exception:
        # Respaldo para otros sistemas o instalaciones sin acceso a user32.
        root.geometry(_position_geometry(root.winfo_width(), root.winfo_height(), x, y))


def secondary_monitor_geometry(width: int, height: int) -> str:
    screens = physical_monitors()
    monitor = screens[1] if len(screens) > 1 else screens[0]
    return monitor_geometry(monitor, width, height)


class RebeccaCompanionApp:
    def __init__(self):
        self.settings = CompanionSettings()
        self.monitors = physical_monitors()
        try:
            saved_monitor = int(self.settings.get("monitor_number", 2 if len(self.monitors) > 1 else 1))
        except (TypeError, ValueError):
            saved_monitor = 1
        self.monitor_number = min(max(saved_monitor, 1), len(self.monitors))
        self.avatar_only = False
        self._hidden = False
        self._position_before_hiding: tuple[int, int] | None = None
        self.root = tk.Tk()
        self.root.title("Rebecca Companion")
        self.root.geometry(
            monitor_geometry(
                self._current_monitor(),
                WINDOW_WIDTH,
                dashboard_height(self._current_monitor()),
            )
        )
        self.root.configure(bg=NEUTRAL_BACKGROUND)
        self.root.attributes("-topmost", True)

        self.client = RebeccaClient()
        self.interactions = InteractionCoordinator()
        self.renderer = AvatarRenderer(ROOT.parent / "Rebecca_Sprites", width=360)
        self.busy = False
        self._comment_in_progress = False
        self._spectator_activity_lease: InteractionLease | None = None
        self._pending_voice_text: str | None = None
        self._pending_voice_poll_scheduled = False
        self.expression = "neutral"
        self._animation_generation = 0
        self._expression_generation = 0
        self._blink_generation = 0

        self.avatar = tk.Label(self.root, bd=0, bg=NEUTRAL_BACKGROUND)
        self.avatar.pack(pady=(0, 0))

        self.dashboard_container = tk.Frame(self.root, bg=NEUTRAL_BACKGROUND)
        self.dashboard_container.pack(fill="both", expand=True)
        self.dashboard_scrollbar = ttk.Scrollbar(self.dashboard_container, orient="vertical")
        self.dashboard_scrollbar.pack(side="right", fill="y")
        self.dashboard_canvas = tk.Canvas(
            self.dashboard_container,
            bg=NEUTRAL_BACKGROUND,
            bd=0,
            highlightthickness=0,
            yscrollcommand=self.dashboard_scrollbar.set,
        )
        self.dashboard_canvas.pack(side="left", fill="both", expand=True)
        self.dashboard_scrollbar.configure(command=self.dashboard_canvas.yview)
        self.dashboard_content = tk.Frame(self.dashboard_canvas, bg=NEUTRAL_BACKGROUND)
        self.dashboard_window = self.dashboard_canvas.create_window(
            (0, 0),
            window=self.dashboard_content,
            anchor="nw",
        )
        self.dashboard_content.bind(
            "<Configure>",
            lambda _event: self.dashboard_canvas.configure(
                scrollregion=self.dashboard_canvas.bbox("all")
            ),
        )
        self.dashboard_canvas.bind(
            "<Configure>",
            lambda event: self.dashboard_canvas.itemconfigure(self.dashboard_window, width=event.width),
        )
        self.root.bind_all("<MouseWheel>", self._scroll_dashboard)

        self.bubble = tk.Label(
            self.dashboard_content,
            text="Iniciando a Rebecca...",
            wraplength=350,
            justify="left",
            bg="#0b1220",
            fg="#8fffd0",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=9,
        )
        self.bubble.pack(fill="x", padx=15)

        self.panel = tk.Frame(self.dashboard_content, bg="#111827", padx=10, pady=10)
        self.panel.pack(fill="x", padx=15, pady=(8, 0))

        self.entry = tk.Entry(self.panel, bg="#1f2937", fg="white", insertbackground="white", relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda _event: self.send_message())
        tk.Button(self.panel, text="Hablar", command=self.send_message, bg="#00c67a", fg="#07130f", relief="flat").pack(
            side="left", padx=(8, 0)
        )

        self.controls = tk.Frame(self.dashboard_content, bg="#111827", padx=10, pady=8)
        self.controls.pack(fill="x", padx=15, pady=(5, 0))

        saved_mode = str(self.settings.get("mode", "normal"))
        if saved_mode not in {"silenciosa", "normal", "charlatana"}:
            saved_mode = "normal"
        self.mode = tk.StringVar(value=saved_mode)
        mode_box = ttk.Combobox(
            self.controls,
            textvariable=self.mode,
            values=["silenciosa", "normal", "charlatana"],
            state="readonly",
            width=11,
        )
        mode_box.pack(side="left")
        self.spectator_button = tk.Button(
            self.controls,
            text="👁 Espectadora",
            command=self.toggle_spectator,
            bg="#173b77",
            fg="white",
            relief="flat",
        )
        self.spectator_button.pack(side="left", padx=6)
        self.comment_button = tk.Button(
            self.controls, text="Opinar ahora", command=self.comment_now, relief="flat"
        )
        self.comment_button.pack(side="left")
        tk.Button(self.controls, text="🔇", command=self.client.detener_voz, relief="flat").pack(side="right")

        saved_game_role = str(self.settings.get("dbd_role", "automatico")).casefold()
        if saved_game_role not in {"automatico", "asesino", "superviviente"}:
            saved_game_role = "automatico"
        self.game_role = tk.StringVar(value=saved_game_role)
        self.game_role_row = tk.Frame(self.dashboard_content, bg="#111827", padx=10, pady=5)
        self.game_role_row.pack(fill="x", padx=15, pady=(2, 0))
        tk.Label(
            self.game_role_row,
            text="Rol DBD:",
            bg="#111827",
            fg="#94a3b8",
        ).pack(side="left")
        game_role_box = ttk.Combobox(
            self.game_role_row,
            textvariable=self.game_role,
            values=["automatico", "asesino", "superviviente"],
            state="readonly",
            width=15,
        )
        game_role_box.pack(side="left", padx=(5, 0))
        game_role_box.bind("<<ComboboxSelected>>", self.select_game_role)
        tk.Label(
            self.game_role_row,
            text="Evita confundir tu perspectiva",
            bg="#111827",
            fg="#64748b",
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(8, 0))

        self.voice_controls = tk.Frame(self.dashboard_content, bg="#111827", padx=10, pady=8)
        self.voice_controls.pack(fill="x", padx=15, pady=(5, 0))
        tk.Button(
            self.voice_controls,
            text="🎤 Hablar ahora",
            command=self.listen_once,
            bg="#5935a5",
            fg="white",
            relief="flat",
        ).pack(side="left")
        self.listener_button = tk.Button(
            self.voice_controls,
            text="🎧 Escucha activa",
            command=self.toggle_listening,
            bg="#26364f",
            fg="white",
            relief="flat",
        )
        self.listener_button.pack(side="left", padx=6)
        tk.Label(
            self.voice_controls,
            text='Decí “Rebecca…”',
            bg="#111827",
            fg="#94a3b8",
            font=("Segoe UI", 8),
        ).pack(side="left")

        self.microphone_row = tk.Frame(self.dashboard_content, bg="#111827", padx=10, pady=5)
        self.microphone_row.pack(fill="x", padx=15, pady=(2, 0))
        tk.Label(self.microphone_row, text="Micrófono:", bg="#111827", fg="#94a3b8").pack(side="left")
        self.microphones = input_devices()
        microphone_values = [f"{index}: {name}" for index, name in self.microphones]
        self.microphone_choice = tk.StringVar()
        microphone_box = ttk.Combobox(
            self.microphone_row,
            textvariable=self.microphone_choice,
            values=microphone_values,
            state="readonly",
            width=31,
        )
        microphone_box.pack(side="left", padx=(5, 0))
        microphone_box.bind("<<ComboboxSelected>>", self.select_microphone)

        self.speaker_row = tk.Frame(self.dashboard_content, bg="#111827", padx=10, pady=5)
        self.speaker_row.pack(fill="x", padx=15, pady=(2, 0))
        tk.Label(self.speaker_row, text="Sonido:", bg="#111827", fg="#94a3b8").pack(side="left")
        self.speakers = output_devices()
        speaker_values = [f"{index}: {name}" for index, name in self.speakers]
        self.speaker_choice = tk.StringVar()
        speaker_box = ttk.Combobox(
            self.speaker_row,
            textvariable=self.speaker_choice,
            values=speaker_values,
            state="readonly",
            width=24,
        )
        speaker_box.pack(side="left", padx=(5, 0))
        speaker_box.bind("<<ComboboxSelected>>", self.select_speaker)
        tk.Button(
            self.speaker_row,
            text="Probar",
            command=self.test_speaker,
            bg="#26364f",
            fg="white",
            relief="flat",
        ).pack(side="left", padx=(5, 0))
        try:
            import sounddevice as sd

            default_output = int(sd.default.device[1])
        except (ImportError, TypeError, ValueError, IndexError):
            default_output = -1
        saved_speaker_name = str(self.settings.get("speaker_name", "")).casefold()
        default_speaker_prefix = f"{default_output}:"
        default_speaker = next(
            (
                value
                for value in speaker_values
                if value.partition(": ")[2].casefold() == saved_speaker_name
            ),
            next((value for value in speaker_values if value.startswith(default_speaker_prefix)), ""),
        )
        if default_speaker:
            self.speaker_choice.set(default_speaker)
            self.client.output_device = int(default_speaker.partition(":")[0])

        self.display_row = tk.Frame(self.dashboard_content, bg="#111827", padx=10, pady=5)
        self.display_row.pack(fill="x", padx=15, pady=(2, 0))
        tk.Label(self.display_row, text="Pantalla:", bg="#111827", fg="#94a3b8").pack(side="left")
        self.monitor_choice = tk.StringVar(value=self._monitor_label(self.monitor_number))
        self.monitor_box = ttk.Combobox(
            self.display_row,
            textvariable=self.monitor_choice,
            values=[self._monitor_label(number) for number in range(1, len(self.monitors) + 1)],
            state="readonly",
            width=16,
        )
        self.monitor_box.pack(side="left", padx=(5, 0))
        self.monitor_box.bind("<<ComboboxSelected>>", self.select_monitor)
        tk.Button(
            self.display_row,
            text="Solo avatar",
            command=self.show_avatar_only,
            bg="#5935a5",
            fg="white",
            relief="flat",
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            self.display_row,
            text="Ocultar",
            command=self.hide_until_called,
            bg="#26364f",
            fg="white",
            relief="flat",
        ).pack(side="left", padx=(5, 0))

        self.status = tk.Label(
            self.dashboard_content,
            text="Ctrl+Alt+Espacio: hablar · Ctrl+Alt+O: opinar",
            bg="#111827",
            fg="#94a3b8",
            font=("Segoe UI", 8),
        )
        self.status.pack(fill="x", padx=15, pady=(3, 0))

        self.spectator = SpectatorMode(
            self.client,
            self._thread_comment,
            self._thread_status,
            on_observation_start=self._begin_spectator_observation,
            on_observation_speaking=self._spectator_speaking,
            on_observation_end=self._end_spectator_observation,
            can_comment=self._can_auto_comment,
        )
        self.spectator.set_game_role(saved_game_role)
        self.voice_listener = VoiceListener(
            self.client.transcribe_audio,
            self._voice_text,
            self._voice_status,
            on_wake=self._wake_detected,
            on_capture_end=self._voice_capture_ended,
            on_followup_text=self._voice_followup_text,
        )
        saved_microphone_name = str(self.settings.get("microphone_name", "")).casefold()
        default_prefix = f"{self.voice_listener.device}:"
        default_label = next(
            (
                value
                for value in microphone_values
                if value.partition(": ")[2].casefold() == saved_microphone_name
            ),
            next((value for value in microphone_values if value.startswith(default_prefix)), ""),
        )
        if default_label:
            self.microphone_choice.set(default_label)
            self.voice_listener.set_device(int(default_label.partition(":")[0]))
        self._render()
        self._schedule_idle_blink()
        self._enable_dragging()
        self._create_avatar_menu()
        self.tray = CompanionTray(
            ROOT.parent / "Rebecca_Sprites",
            schedule=lambda action: self.root.after(0, action),
            on_show=self.summon_window,
            on_avatar=self.summon_avatar,
            on_hide=self.hide_until_called,
            on_quit=self.close,
        )
        self.tray.start()
        self.instance_control = CompanionControlServer(
            schedule=lambda action: self.root.after(0, action),
            on_show=self.summon_window,
        )
        self.instance_control.start()
        # Rebecca debe quedar atenta desde el arranque, incluso en modo solo avatar.
        self.root.after(250, self._ensure_wake_listener)
        keyboard.add_hotkey("ctrl+alt+o", lambda: self.root.after(0, self.comment_now))
        keyboard.add_hotkey("ctrl+alt+space", lambda: self.root.after(0, self.listen_once))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Unmap>", self._window_unmapped)
        # Aplicar la coordenada absoluta una vez que Windows haya creado el
        # marco real; esto también respeta al iniciar una pantalla a la izquierda.
        self.root.after(100, lambda: self.move_to_monitor(self.monitor_number))
        self.root.after(500, self._show_connection_state)
        if bool(self.settings.get("avatar_only", False)):
            self.root.after(150, self.show_avatar_only)

    def _current_monitor(self) -> dict[str, int]:
        return self.monitors[self.monitor_number - 1]

    def _monitor_label(self, number: int) -> str:
        monitor = self.monitors[number - 1]
        return f"Pantalla {number} ({monitor['width']}×{monitor['height']})"

    def select_monitor(self, _event=None):
        selected = self.monitor_choice.get().partition(" ")[2].partition(" ")[0]
        if selected.isdigit():
            self.move_to_monitor(int(selected))

    def move_to_monitor(self, number: int):
        self.monitor_number = min(max(int(number), 1), len(self.monitors))
        self.settings.set("monitor_number", self.monitor_number)
        self.monitor_choice.set(self._monitor_label(self.monitor_number))
        if self.avatar_only:
            saved = self.settings.get(f"avatar_position_{self.monitor_number}")
            width, height = AVATAR_SIZE, AVATAR_SIZE
            x, y = monitor_position(
                self._current_monitor(),
                width,
                height,
                placement="bottom-right",
                saved_position=saved,
            )
        else:
            width, height = WINDOW_WIDTH, dashboard_height(self._current_monitor())
            x, y = monitor_position(self._current_monitor(), width, height)
        self.root.geometry(f"{width}x{height}")
        move_window_absolute(self.root, x, y)

    def show_avatar_only(self):
        if self.avatar_only:
            return
        self.avatar_only = True
        self.settings.set("avatar_only", True)
        self.dashboard_container.pack_forget()
        self.root.overrideredirect(True)
        self.root.configure(bg=TRANSPARENT_BACKGROUND)
        self.avatar.configure(bg=TRANSPARENT_BACKGROUND)
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_BACKGROUND)
        except tk.TclError:
            pass
        self.move_to_monitor(self.monitor_number)

    def show_dashboard(self):
        if not self.avatar_only:
            return
        self.avatar_only = False
        self.settings.set("avatar_only", False)
        try:
            self.root.wm_attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        self.root.overrideredirect(False)
        self.root.configure(bg=NEUTRAL_BACKGROUND)
        self.avatar.configure(bg=NEUTRAL_BACKGROUND)
        self.dashboard_container.configure(bg=NEUTRAL_BACKGROUND)
        self.dashboard_canvas.configure(bg=NEUTRAL_BACKGROUND)
        self.dashboard_content.configure(bg=NEUTRAL_BACKGROUND)
        self.dashboard_container.pack(fill="both", expand=True)
        self.dashboard_canvas.yview_moveto(0)
        # Al volver a crear el marco, Windows cambia el identificador de la
        # ventana. Reubicarla en el siguiente ciclo evita conservar las
        # coordenadas compactas del avatar y cortar el panel en pantallas bajas.
        self.root.after(80, lambda: self.move_to_monitor(self.monitor_number))

    def _create_avatar_menu(self):
        self.avatar_menu = tk.Menu(self.root, tearoff=False)
        self.avatar_menu.add_command(label="Mostrar controles", command=self.show_dashboard)
        self.avatar_menu.add_command(label="Ocultarme hasta que me llames", command=self.hide_until_called)
        screen_menu = tk.Menu(self.avatar_menu, tearoff=False)
        for number in range(1, len(self.monitors) + 1):
            screen_menu.add_command(
                label=self._monitor_label(number),
                command=lambda selected=number: self.move_to_monitor(selected),
            )
        self.avatar_menu.add_cascade(label="Mover a pantalla", menu=screen_menu)

        def open_menu(event):
            if self.avatar_only:
                self.avatar_menu.tk_popup(event.x_root, event.y_root)

        self.avatar.bind("<Button-3>", open_menu)
        self.avatar.bind("<Double-Button-1>", lambda _event: self.show_dashboard())

    def _ensure_wake_listener(self):
        if not self.voice_listener.running:
            self.voice_listener.start_continuous()
            self.interactions.set_passive_listening(True)
            self.listener_button.configure(text="■ Dejar de escuchar", bg="#8b1d2c")

    def _scroll_dashboard(self, event):
        if self.avatar_only or not self.dashboard_container.winfo_ismapped():
            return
        direction = -1 if event.delta > 0 else 1
        self.dashboard_canvas.yview_scroll(direction * 3, "units")

    def hide_until_called(self):
        self._position_before_hiding = (self.root.winfo_x(), self.root.winfo_y())
        self._hidden = True
        self._ensure_wake_listener()
        self.root.withdraw()

    def _window_unmapped(self, event):
        if event.widget is not self.root:
            return

        def arm_if_hidden():
            if self.root.state() in {"iconic", "withdrawn"}:
                self._hidden = True
                self._ensure_wake_listener()

        self.root.after(50, arm_if_hidden)

    def _wake_detected(self):
        self.spectator.pause_for_conversation(True)
        self.root.after(0, self.summon_window)

    def _can_auto_comment(self) -> bool:
        listener = getattr(self, "voice_listener", None)
        return not (
            self.interactions.busy or self._pending_voice_text
            or (listener and (listener.processing or listener.capturing_speech))
        )

    def _voice_capture_ended(self):
        def finish():
            if not self.interactions.busy and not self._pending_voice_text and not self.voice_listener.processing:
                self.spectator.pause_for_conversation(False)
        self.root.after(0, finish)

    def summon_window(self):
        if not self._hidden and self.root.state() == "normal":
            return
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self._hidden = False
        if self._position_before_hiding is not None:
            x, y = self._position_before_hiding
            self.root.after(60, lambda: move_window_absolute(self.root, x, y))

    def summon_avatar(self):
        self.summon_window()
        if not self.avatar_only:
            self.root.after(90, self.show_avatar_only)

    def _enable_dragging(self):
        state = {"x": 0, "y": 0}

        def start(event):
            state["x"], state["y"] = event.x_root, event.y_root

        def move(event):
            dx, dy = event.x_root - state["x"], event.y_root - state["y"]
            x, y = self.root.winfo_x() + dx, self.root.winfo_y() + dy
            move_window_absolute(self.root, x, y)
            state["x"], state["y"] = event.x_root, event.y_root

        def finish(_event):
            center_x = self.root.winfo_x() + self.root.winfo_width() // 2
            center_y = self.root.winfo_y() + self.root.winfo_height() // 2
            actual_monitor = monitor_number_at(self.monitors, center_x, center_y)
            if actual_monitor is not None:
                self.monitor_number = actual_monitor
                self.monitor_choice.set(self._monitor_label(actual_monitor))
                self.settings.set("monitor_number", actual_monitor)
            if self.avatar_only:
                self.settings.set(
                    f"avatar_position_{self.monitor_number}",
                    [self.root.winfo_x(), self.root.winfo_y()],
                )

        self.avatar.bind("<ButtonPress-1>", start)
        self.avatar.bind("<B1-Motion>", move)
        self.avatar.bind("<ButtonRelease-1>", finish)

    def _render(self, expression: str | None = None, mouth: int = 0):
        if expression is not None:
            self.expression = expression
        frame = self.renderer.frame(self.expression, mouth)
        self.avatar.configure(image=frame)
        self.avatar.image = frame

    def _begin_activity(
        self,
        owner: str,
        phase: InteractionPhase,
        status_text: str,
    ) -> InteractionLease | None:
        lease = self.interactions.try_begin(owner, phase)
        if lease is None:
            return None
        self.busy = True
        if hasattr(self, "voice_listener") and owner != "spectator":
            self.voice_listener.begin_processing()
        self.root.after(0, lambda: self.status.configure(text=status_text))
        return lease

    def _transition_activity(
        self,
        lease: InteractionLease,
        phase: InteractionPhase,
        status_text: str,
    ) -> None:
        if self.interactions.transition(lease, phase):
            self.root.after(0, lambda: self.status.configure(text=status_text))

    def _animate_speaking(self, duration_ms: int):
        self._animation_generation += 1
        generation = self._animation_generation
        sequence = (0, 1, 2, 1)
        started = time.monotonic()

        def step(index: int = 0):
            if generation != self._animation_generation:
                return
            elapsed_ms = (time.monotonic() - started) * 1000
            if elapsed_ms >= duration_ms:
                self._render(mouth=0)
                return
            self._render(mouth=sequence[index % len(sequence)])
            self.root.after(125, lambda: step(index + 1))

        step()

    def _schedule_idle_blink(self):
        generation = self._blink_generation
        delay_ms = random.randint(3200, 6800)

        def blink():
            if generation != self._blink_generation:
                return
            if not self.busy and self.expression != "sleepy":
                previous = self.expression
                self._render("sleepy", mouth=0)

                def restore():
                    if generation == self._blink_generation and not self.busy:
                        self._render(previous, mouth=0)

                self.root.after(140, restore)
            self._schedule_idle_blink()

        self.root.after(delay_ms, blink)

    def _show_connection_state(self):
        if self.client.health():
            self.bubble.configure(text="Rebecca está lista en tu segunda pantalla.")
            self.status.configure(text="Core conectado · Ctrl+Alt+Espacio: hablar · Ctrl+Alt+O: opinar")
        else:
            self.bubble.configure(text="Rebecca Core no pudo iniciar. Cerrá esta ventana y volvé a abrirla.")
            self.status.configure(text="Sin conexión con Rebecca Core")

    def show_comment(self, text: str, speak: bool = True):
        self.bubble.configure(text=text)
        duration_ms = max(1200, min(9000, len(text) * 55))
        self.expression = infer_expression(text)
        self._expression_generation += 1
        expression_generation = self._expression_generation
        if speak:
            self.voice_listener.pause_for((duration_ms / 1000) + 1.0)
            self._animate_speaking(duration_ms)
        else:
            self._render(mouth=0)

        def settle_expression():
            if expression_generation == self._expression_generation and not self.busy:
                self._render("neutral", mouth=0)

        self.root.after(10000, settle_expression)

    def send_message(self):
        text = self.entry.get().strip()
        if not text:
            return
        if self.process_user_text(text, "desktop"):
            self.entry.delete(0, "end")

    def process_user_text(self, text: str, origin: str = "desktop") -> bool:
        self.spectator.pause_for_conversation(True)
        if self.busy:
            if self._pending_voice_text:
                self.status.configure(text="Ya tengo tu siguiente mensaje esperando; no borré el que acabás de escribir")
                return False
            self._pending_voice_text = text
            self._pending_user_origin = origin
            self.status.configure(text="Te doy prioridad: termino esta frase y respondo tu mensaje")
            self._schedule_pending_voice()
            return True
        lease = self._begin_activity(
            f"user:{origin}",
            InteractionPhase.THINKING,
            "Rebecca está pensando...",
        )
        if lease is None:
            return False
        self.client.learn_personal(text)
        role = self.client.game_context.correct_role(text)
        if role:
            self.game_role.set(role)
            self.spectator.set_game_role(role)
        about_game = self.client.game_context.is_game_question(text)
        if about_game:
            self.client.game_context.note("usuario", text)

        def worker():
            try:
                routed = route_command(text)
                wake_free = origin == "desktop_voice_followup"
                if routed and wake_free:
                    answer = (
                        "Te entendí, pero para ejecutar comandos decime ‘Rebecca’ y repetí la orden. "
                        "Así el audio del juego no puede activar cosas por accidente."
                    )
                    status_text = "Comando bloqueado: falta la palabra Rebecca"
                elif routed:
                    result = self.client.command(routed.action, routed.parameter, **routed.extra)
                    answer = str(result.get("message") or "Listo.").strip()
                    self.client.remember_action(text, answer)
                    status_text = "Comando directo completado"
                elif about_game and self.spectator.running:
                    answer = self.spectator.ask_about_game(text)
                    status_text = "Conversación sobre la partida"
                else:
                    answer = self.client.chat(text, origen=origin, allow_actions=not wake_free)
                    status_text = "Conectada a la memoria principal"
                if about_game:
                    self.client.game_context.note("respuesta_de_rebecca", answer)
                self._transition_activity(
                    lease,
                    InteractionPhase.SPEAKING,
                    "Rebecca está hablando...",
                )
                self.root.after(0, lambda: self.show_comment(answer))
                voice_result = self.client.hablar(answer)
                if voice_result.get("status") != "success":
                    voice_message = str(voice_result.get("message") or "salida no disponible")
                    status_text = f"Respuesta lista · voz: {voice_message}"
                self.root.after(0, lambda: self.status.configure(text=status_text))
            except RebeccaConnectionError as exc:
                error = str(exc)
                self.root.after(0, lambda: self.status.configure(text=error))
            finally:
                self.root.after(0, lambda: self._release_busy(lease))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _release_busy(self, lease: InteractionLease | None = None):
        if lease is not None:
            if not self.interactions.finish(lease):
                return  # An old completion must not release a newer user turn.
        self.busy = self.interactions.busy
        if self.busy:
            return
        if self._pending_voice_text:
            # La orden ya fue reconocida: mantener el micrófono pausado hasta
            # entregarla, para que no se pise con otra escucha.
            self.voice_listener.begin_processing()
            self._schedule_pending_voice(50)
        else:
            self.voice_listener.finish_processing()
            self.spectator.pause_for_conversation(False)

    def listen_once(self):
        if self.busy:
            self.status.configure(text="Rebecca está ocupada; termino esto y después podés hablarme")
            return
        if self.voice_listener.running:
            self.status.configure(text='Escucha activa encendida: decí “Rebecca” y la orden')
            return
        self.spectator.pause_for_conversation(True)
        self.voice_listener.listen_once()

    def toggle_listening(self):
        if self.voice_listener.running:
            self.voice_listener.stop()
            self.interactions.set_passive_listening(False)
            self.listener_button.configure(text="🎧 Escucha activa", bg="#26364f")
        else:
            self.voice_listener.start_continuous()
            self.interactions.set_passive_listening(True)
            self.listener_button.configure(text="■ Dejar de escuchar", bg="#8b1d2c")

    def select_microphone(self, _event=None):
        selected = self.microphone_choice.get().partition(":")[0]
        if selected.isdigit():
            self.voice_listener.set_device(int(selected))
            self.settings.set("microphone_name", self.microphone_choice.get().partition(": ")[2])
            self.status.configure(text=f"Micrófono seleccionado: {self.microphone_choice.get().partition(': ')[2]}")

    def select_speaker(self, _event=None):
        selected = self.speaker_choice.get().partition(":")[0]
        if selected.isdigit():
            self.client.output_device = int(selected)
            self.settings.set("speaker_name", self.speaker_choice.get().partition(": ")[2])
            self.status.configure(text=f"Salida seleccionada: {self.speaker_choice.get().partition(': ')[2]}")

    def test_speaker(self):
        if self.busy:
            self.status.configure(text="Esperá a que Rebecca termine la interacción actual")
            return
        lease = self._begin_activity(
            "speaker-test",
            InteractionPhase.SPEAKING,
            "Probando la salida de audio...",
        )
        if lease is None:
            return

        def worker():
            try:
                result = self.client.hablar("Prueba de sonido de Rebecca")
                if result.get("status") == "success":
                    voice = "Fish Audio" if result.get("voice") == "fish" else "voz local"
                    message = f"Prueba enviada por {voice}"
                else:
                    message = f"No pude usar la salida: {result.get('message', 'error desconocido')}"
            except RebeccaConnectionError as exc:
                message = str(exc)
            finally:
                self.root.after(0, lambda: self._release_busy(lease))
            self.root.after(0, lambda: self.status.configure(text=message))

        threading.Thread(target=worker, daemon=True).start()

    def _voice_text(self, text: str):
        self.root.after(0, lambda: self._dispatch_voice_text(text, "desktop_voice"))

    def _voice_followup_text(self, text: str):
        self.root.after(0, lambda: self._dispatch_voice_text(text, "desktop_voice_followup"))

    def _dispatch_voice_text(self, text: str, origin: str = "desktop_voice"):
        """Entrega una orden reconocida sin perderla si la UI aún está ocupada."""
        clean_text = str(text or "").strip()
        if not clean_text:
            self.voice_listener.finish_processing()
            return
        self.spectator.pause_for_conversation(True)
        if self.busy or self._comment_in_progress:
            if not self._pending_voice_text:
                self._pending_voice_text = clean_text
                self._pending_user_origin = origin
            self.status.configure(text="Te entendí; termino lo anterior y ya proceso tu orden")
            self._schedule_pending_voice()
            return
        if not self.process_user_text(clean_text, origin):
            self._pending_voice_text = clean_text
            self._schedule_pending_voice()

    def _schedule_pending_voice(self, delay_ms: int = 200):
        if self._pending_voice_poll_scheduled:
            return
        self._pending_voice_poll_scheduled = True
        self.root.after(delay_ms, self._flush_pending_voice)

    def _flush_pending_voice(self):
        self._pending_voice_poll_scheduled = False
        if not self._pending_voice_text:
            return
        if self.busy or self._comment_in_progress:
            self._schedule_pending_voice()
            return
        text = self._pending_voice_text
        origin = getattr(self, "_pending_user_origin", "desktop_voice")
        self._pending_voice_text = None
        self.voice_listener.begin_processing()
        if not self.process_user_text(text, origin):
            self._pending_voice_text = text
            self._schedule_pending_voice()

    def _voice_status(self, text: str):
        def update():
            if not self.busy and not self._comment_in_progress:
                self.status.configure(text=text)

        self.root.after(0, update)

    def comment_now(self):
        if self._comment_in_progress:
            self.status.configure(text="Rebecca ya está mirando la escena actual...")
            return
        if self.busy:
            self.status.configure(text="Esperá a que termine la respuesta actual")
            return
        if hasattr(self, "spectator") and self.spectator.request_live_comment():
            self.status.configure(text="Le pedí a Rebecca que opine sobre la escena Live...")
            return
        lease = self._begin_activity(
            "manual-observation",
            InteractionPhase.OBSERVING,
            "Mirando la ventana activa...",
        )
        if lease is None:
            return
        self._comment_in_progress = True
        self.comment_button.configure(state="disabled")
        initial_title = active_window_title()
        observed_at = time.monotonic()

        def worker():
            try:
                role_context = self.game_role.get() if hasattr(self, "game_role") else "automatico"
                comment = self.client.opinar_juego(
                    f"Comentario solicitado manualmente por Usuario. Rol confirmado por Usuario: {role_context}."
                )
                scene_changed = active_window_title().strip().casefold() != initial_title.strip().casefold()
                if scene_changed:
                    self.root.after(
                        0,
                        lambda: self.status.configure(
                            text="Cambié de ventana; descarté el comentario anterior"
                        ),
                    )
                else:
                    self._transition_activity(
                        lease,
                        InteractionPhase.SPEAKING,
                        "Rebecca está hablando...",
                    )
                    self.root.after(0, lambda: self.show_comment(comment))
                    self.client.remember_observation(initial_title, comment, observed_at=observed_at)
                    self.client.hablar(comment)
                    self.root.after(0, lambda: self.status.configure(text="Comentario generado"))
            except RebeccaConnectionError as exc:
                error = str(exc)
                self.root.after(0, lambda: self.status.configure(text=error))
            finally:
                self.root.after(0, lambda: self._finish_manual_comment(lease))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_manual_comment(self, lease: InteractionLease):
        self._comment_in_progress = False
        self.comment_button.configure(state="normal")
        self._release_busy(lease)

    def toggle_spectator(self):
        if self.spectator.running:
            self.spectator.stop()
            self.spectator_button.configure(text="👁 Espectadora", bg="#173b77")
        else:
            self.settings.set("mode", self.mode.get())
            self.spectator.set_game_role(self.game_role.get())
            self.spectator.start(self.mode.get())
            self.spectator_button.configure(text="■ Detener", bg="#8b1d2c")

    def select_game_role(self, _event=None):
        role = self.game_role.get().strip().casefold()
        self.settings.set("dbd_role", role)
        if hasattr(self, "spectator"):
            self.spectator.set_game_role(role)
        self.status.configure(text=f"Rol de Dead by Daylight: {role}")

    def _thread_comment(self, text: str):
        if self._spectator_activity_lease is None and (
            self.busy or self._comment_in_progress or self.voice_listener.processing
        ):
            return False
        self.root.after(0, lambda: self.show_comment(text))
        return True

    def _begin_spectator_observation(self) -> InteractionLease | None:
        if not self._can_auto_comment():
            return None
        lease = self._begin_activity(
            "spectator",
            InteractionPhase.OBSERVING,
            "Rebecca está mirando la escena actual...",
        )
        if lease is not None:
            self._spectator_activity_lease = lease
        return lease

    def _spectator_speaking(self, lease: object) -> None:
        if isinstance(lease, InteractionLease):
            if self.voice_listener.capturing_speech or self._pending_voice_text:
                return False
            self.voice_listener.begin_processing()
            self._transition_activity(
                lease,
                InteractionPhase.SPEAKING,
                "Rebecca está comentando la escena...",
            )

    def _end_spectator_observation(self, lease: object) -> None:
        if not isinstance(lease, InteractionLease):
            return

        def finish():
            if self._spectator_activity_lease == lease:
                self._spectator_activity_lease = None
            self._release_busy(lease)

        self.root.after(0, finish)

    def _thread_status(self, text: str):
        def update():
            if not self.busy and not self._pending_voice_text:
                self.status.configure(text=text)
        self.root.after(0, update)

    def close(self):
        self._blink_generation += 1
        self.spectator.stop()
        self.voice_listener.stop()
        self.interactions.set_passive_listening(False)
        keyboard.unhook_all_hotkeys()
        self.instance_control.stop()
        self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    RebeccaCompanionApp().run()


if __name__ == "__main__":
    main()
