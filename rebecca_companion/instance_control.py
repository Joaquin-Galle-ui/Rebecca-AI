from __future__ import annotations

import socket
import threading
from collections.abc import Callable


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 47653


def request_show_existing(
    host: str = CONTROL_HOST,
    port: int = CONTROL_PORT,
    timeout: float = 0.8,
) -> bool:
    """Ask an already-running Companion process to reveal its window."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(b"show\n")
            return connection.recv(16).strip() == b"ok"
    except OSError:
        return False


class CompanionControlServer:
    """Tiny loopback-only bridge used by a second launcher invocation."""

    def __init__(
        self,
        schedule: Callable[[Callable[[], None]], None],
        on_show: Callable[[], None],
        host: str = CONTROL_HOST,
        port: int = CONTROL_PORT,
    ) -> None:
        self.schedule = schedule
        self.on_show = on_show
        self.host = host
        self.port = port
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="rebecca-instance-control",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=1.0)

    def _run(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                self.port = int(server.getsockname()[1])
                server.listen(2)
                server.settimeout(0.5)
                self._ready.set()
                while not self._stop.is_set():
                    try:
                        connection, _address = server.accept()
                    except TimeoutError:
                        continue
                    with connection:
                        try:
                            command = connection.recv(32).strip().lower()
                            if command == b"show":
                                self.schedule(self.on_show)
                                connection.sendall(b"ok\n")
                            else:
                                connection.sendall(b"error\n")
                        except OSError:
                            continue
        except OSError:
            # The mutex remains the source of truth. If an unexpected process
            # owns the helper port, Companion still runs and the Win32 fallback
            # in the launcher can recover its window.
            self._ready.set()

    def stop(self) -> None:
        self._stop.set()
        try:
            with socket.create_connection((self.host, self.port), timeout=0.2):
                pass
        except OSError:
            pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None
