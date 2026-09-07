from __future__ import annotations

import ctypes
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import requests

from rebecca_companion.core_contract import CORE_VERSION, REQUIRED_CAPABILITY
from rebecca_companion.instance_control import request_show_existing

ROOT = Path(__file__).resolve().parents[1]
CORE_PORT = 8000
CORE_LOG = ROOT / "rebecca_core.log"
INSTANCE_MUTEX_NAME = "Local\\RebeccaCompanionDesktop"
COMPANION_WINDOW_TITLE = "Rebecca Companion"
_instance_mutex = None


def acquire_single_instance() -> bool:
    """Prevents duplicated UI, hotkeys, microphone listeners and spectator loops."""
    global _instance_mutex
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _instance_mutex = (kernel32, handle)
    return True


def release_single_instance() -> None:
    global _instance_mutex
    if _instance_mutex is None:
        return
    kernel32, handle = _instance_mutex
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)
    _instance_mutex = None


def summon_existing_window(title: str = COMPANION_WINDOW_TITLE) -> bool:
    """Show the already-running Companion window after a second launch.

    Companion can intentionally withdraw its Tk window while it keeps listening.
    Previously the single-instance mutex made every new launch exit immediately,
    leaving the user with no visible window when the tray icon was unavailable.
    """
    if sys.platform != "win32":
        return False

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    matching_window: list[int] = []

    user32.EnumWindows.argtypes = [enum_callback_type, ctypes.c_void_p]
    user32.EnumWindows.restype = ctypes.c_bool
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.ShowWindowAsync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindowAsync.restype = ctypes.c_bool
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_bool

    @enum_callback_type
    def find_window(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value == title:
            matching_window.append(hwnd)
            return False
        return True

    user32.EnumWindows(find_window, None)
    if not matching_window:
        return False

    hwnd = matching_window[0]
    user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE también muestra ventanas retiradas.
    user32.SetForegroundWindow(hwnd)
    return True


def port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def core_is_current() -> bool:
    try:
        response = requests.get(f"http://127.0.0.1:{CORE_PORT}/health", timeout=2)
        response.raise_for_status()
        info = response.json()
        return (
            info.get("version") == CORE_VERSION
            and REQUIRED_CAPABILITY in info.get("capacidades", [])
        )
    except (requests.RequestException, ValueError):
        return False


def find_owned_core_process() -> psutil.Process | None:
    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, OSError):
        return None
    for connection in connections:
        if not connection.laddr or connection.laddr.port != CORE_PORT or connection.status != psutil.CONN_LISTEN:
            continue
        if not connection.pid:
            continue
        try:
            process = psutil.Process(connection.pid)
            command = [Path(part).name.lower() for part in process.cmdline()]
            if "servidor_controles_becca.py" in command:
                return process
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return None


def stop_outdated_core() -> None:
    process = find_owned_core_process()
    if process is None:
        raise RuntimeError(
            "Hay otro programa usando el puerto 8000. Cerralo y volvé a iniciar Rebecca Companion."
        )
    process.terminate()
    try:
        process.wait(timeout=6)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def start_core_if_needed() -> subprocess.Popen | None:
    if port_is_open(CORE_PORT):
        if core_is_current():
            return None
        stop_outdated_core()
        for _ in range(20):
            if not port_is_open(CORE_PORT):
                break
            time.sleep(0.1)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    log = CORE_LOG.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "servidor_controles_becca.py")],
            cwd=ROOT,
            creationflags=flags,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    finally:
        log.close()
    # El Core carga audio, visión, PDF, WhatsApp y control multimedia. En PCs
    # con varias integraciones puede tardar más de 15 segundos sólo en importar;
    # no lo mates cuando todavía está arrancando correctamente.
    for _ in range(240):
        if port_is_open(CORE_PORT) and core_is_current():
            return process
        if process.poll() is not None:
            detalle = ""
            try:
                lineas = CORE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
                detalle = "\n".join(lineas[-8:])
            except OSError:
                pass
            mensaje = "Rebecca Core se cerró durante el arranque."
            if detalle:
                mensaje += f"\n\nÚltimo registro:\n{detalle}"
            raise RuntimeError(mensaje)
        time.sleep(0.25)
    process.terminate()
    try:
        process.wait(timeout=5)
    except psutil.TimeoutExpired:
        process.kill()
    raise RuntimeError("Rebecca Core tardó demasiado en iniciar.")


def main() -> None:
    if not acquire_single_instance():
        if not request_show_existing():
            summon_existing_window()
        return
    try:
        try:
            start_core_if_needed()
        except Exception as exc:
            import tkinter as tk
            from tkinter import messagebox

            dialog = tk.Tk()
            dialog.withdraw()
            messagebox.showerror(
                "Rebecca no pudo iniciar",
                f"No pude preparar Rebecca Core.\n\n{exc}",
                parent=dialog,
            )
            dialog.destroy()
            return
        from rebecca_companion.app import main as run_companion

        run_companion()
    finally:
        release_single_instance()


if __name__ == "__main__":
    main()
