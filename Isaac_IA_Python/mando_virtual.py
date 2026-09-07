from __future__ import annotations

import time


class VirtualGamepad:
    """Mando virtual con apagado seguro. El piloto automático queda desactivado."""

    def __init__(self):
        try:
            import vgamepad as vg
        except ImportError as exc:
            raise RuntimeError("Falta vgamepad. Instalalo con: pip install -r requirements.txt") from exc
        self.gamepad = vg.VX360Gamepad()

    def neutral(self) -> None:
        self.gamepad.reset()
        self.gamepad.update()

    def close(self) -> None:
        self.neutral()


def main() -> None:
    print("Creando el mando virtual para el segundo personaje...")
    controller = VirtualGamepad()
    print("Mando conectado. Dejá esta ventana abierta; Ctrl+C lo desconecta.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        controller.close()
        print("Mando virtual desconectado de forma segura.")


if __name__ == "__main__":
    main()
