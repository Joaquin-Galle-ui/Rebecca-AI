from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rebecca_companion.client import RebeccaClient, RebeccaConnectionError


PREFIX = "BOTCOOP_IA_DATOS:"


@dataclass
class IsaacState:
    hp: int = -1
    room: int = -1
    enemies: int = 0
    boss: bool = False
    items: tuple[int, ...] = field(default_factory=tuple)
    players: int = 1

    @classmethod
    def from_payload(cls, payload: dict) -> "IsaacState":
        raw_items = payload.get("items_visibles") or []
        if not isinstance(raw_items, list):
            raw_items = []
        return cls(
            hp=int(payload.get("jugador_hp", -1)),
            room=int(payload.get("sala_actual", -1)),
            enemies=int(payload.get("enemigos_vivos", 0)),
            boss=bool(payload.get("hay_jefe", False)),
            items=tuple(sorted(int(item) for item in raw_items if str(item).isdigit())),
            players=int(payload.get("jugadores", 1)),
        )


def discover_log() -> Path | None:
    configured = os.getenv("ISAAC_LOG_PATH")
    if configured and Path(configured).is_file():
        return Path(configured)
    home = Path.home()
    candidates = [
        home / "Documents/My Games/Binding of Isaac Repentance+/log.txt",
        home / "Documents/My Games/Binding of Isaac Repentance/log.txt",
    ]
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        candidates.extend([
            Path(f"{drive}:/SteamLibrary/steamapps/common/The Binding of Isaac Rebirth/Repentogon/Documents/My Games/Binding of Isaac Repentance+/log.txt"),
            Path(f"{drive}:/Program Files (x86)/Steam/steamapps/common/The Binding of Isaac Rebirth/Repentogon/Documents/My Games/Binding of Isaac Repentance+/log.txt"),
        ])
    return next((path for path in candidates if path.is_file()), None)


def follow_log(path: Path) -> Iterator[str]:
    while True:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as log:
                log.seek(0, 2)
                while True:
                    line = log.readline()
                    if line:
                        yield line
                        continue
                    if not path.exists() or path.stat().st_size < log.tell():
                        break
                    time.sleep(0.08)
        except (FileNotFoundError, PermissionError):
            time.sleep(1)


def parse_line(line: str) -> IsaacState | None:
    if PREFIX not in line:
        return None
    try:
        return IsaacState.from_payload(json.loads(line.split(PREFIX, 1)[1].strip()))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


class RebeccaIsaacBridge:
    def __init__(self, client: RebeccaClient, speak: bool = True, cooldown: int = 25):
        self.client = client
        self.speak = speak
        self.cooldown = cooldown
        self.previous: IsaacState | None = None
        self.last_comment = 0.0

    def event_for(self, current: IsaacState) -> str | None:
        previous = self.previous
        self.previous = current
        if previous is None:
            return "La partida empezó"
        if current.boss and not previous.boss:
            return f"Entramos a un jefe con {current.hp} medios corazones"
        if current.hp >= 0 and previous.hp >= 0 and current.hp < previous.hp:
            return f"Nos pegaron. Quedan {current.hp} medios corazones y {current.enemies} enemigos"
        new_items = sorted(set(current.items) - set(previous.items))
        if new_items:
            return f"Apareció un pedestal de objeto, identificador {new_items[0]}"
        if current.room != previous.room and current.enemies >= 5:
            return f"Sala nueva cargada con {current.enemies} enemigos"
        return None

    def react(self, current: IsaacState) -> str | None:
        event = self.event_for(current)
        now = time.monotonic()
        if not event or now - self.last_comment < self.cooldown:
            return None
        prompt = (
            "Estamos jugando The Binding of Isaac. "
            f"Evento: {event}. Vida: {current.hp}; enemigos: {current.enemies}; jugadores: {current.players}. "
            "Reaccioná naturalmente en una sola frase corta, máximo 18 palabras. No inventes objetos por su ID."
        )
        answer = self.client.chat(prompt, origen="isaac")
        if self.speak:
            self.client.hablar(answer)
        self.last_comment = now
        return answer


def main() -> int:
    parser = argparse.ArgumentParser(description="Puente seguro entre Isaac y Rebecca")
    parser.add_argument("--log", type=Path, help="Ruta manual al log.txt de Isaac")
    parser.add_argument("--sin-voz", action="store_true", help="Muestra comentarios sin reproducirlos")
    parser.add_argument("--cooldown", type=int, default=25, help="Segundos mínimos entre comentarios")
    args = parser.parse_args()
    log_path = args.log or discover_log()
    if not log_path:
        print("No encontré log.txt. Abrí Isaac una vez o indicá la ruta con --log.")
        return 2

    bridge = RebeccaIsaacBridge(RebeccaClient(), speak=not args.sin_voz, cooldown=max(10, args.cooldown))
    print(f"Rebecca está mirando Isaac desde: {log_path}")
    print("K agrega el segundo personaje · F8 pausa/reanuda la telemetría · Ctrl+C cierra el puente")
    try:
        for line in follow_log(log_path):
            state = parse_line(line)
            if state is None:
                continue
            try:
                comment = bridge.react(state)
                if comment:
                    print(f"Rebecca: {comment}")
            except RebeccaConnectionError as exc:
                print(f"Rebecca no pudo responder: {exc}")
    except KeyboardInterrupt:
        print("Puente de Isaac cerrado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
