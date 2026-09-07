from __future__ import annotations

import asyncio
import io
import random
import re
import time
import unicodedata
from dataclasses import dataclass

from PIL import Image, ImageEnhance, ImageOps

from .capture import capture_active_monitor

try:
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
except ImportError:  # pragma: no cover - sólo ocurre fuera de Windows
    BitmapDecoder = OcrEngine = DataWriter = InMemoryRandomAccessStream = None


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9 ]+", " ", value.upper()).strip()


EVENT_PATTERNS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("survivor_found", ("SURVIVOR FOUND", "SUPERVIVIENTE ENCONTRADO"), ("asesino",)),
    ("chase", ("CHASE", "PERSECUCION"), ("asesino",)),
    ("hit", ("HIT", "GOLPE"), ("asesino",)),
    ("hook", ("HOOKED", "HOOK", "COLGADO", "GANCHO"), ("asesino",)),
    ("sacrifice", ("SACRIFICE", "SACRIFICIO"), ("asesino",)),
    ("generator_damage", ("GENERATOR DAMAGE", "DANO DE GENERADOR"), ("asesino",)),
    ("destruction", ("DESTRUCTION", "DESTRUCCION"), ("asesino",)),
    ("skill_check", ("GREAT SKILL CHECK", "GOOD SKILL CHECK", "PRUEBA DE HABILIDAD"), ("superviviente",)),
    ("repair", ("REPAIRS", "REPARACION"), ("superviviente",)),
    ("rescue", ("SAFE HOOK RESCUE", "RESCATE SEGURO", "RESCATE"), ("superviviente",)),
    ("heal", ("HEAL", "CURACION"), ("superviviente",)),
)


COMMENTS: dict[str, tuple[str, ...]] = {
    "survivor_found": (
        "Ahí está, encontraste a un superviviente. Que no te corte la persecución.",
        "Superviviente localizado; ahora no le regales distancia.",
    ),
    "chase": (
        "Entraste en persecución; cerrale el camino y obligalo a gastar el pallet.",
        "Persecución iniciada. Mirá hacia dónde prepara el próximo loop.",
    ),
    "hit": (
        "Buen golpe, ya está herido. No le pierdas el rastro ahora.",
        "Golpe conectado; metele presión antes de que llegue a una zona segura.",
    ),
    "hook": (
        "Uno al gancho. Aprovechá para controlar los generadores cercanos.",
        "Gancho conseguido; ahora buscá quién viene al rescate.",
    ),
    "sacrifice": (
        "Uno menos: la Entidad ya se llevó a ese superviviente.",
        "Sacrificio confirmado. Ahora podés concentrar la presión en los que quedan.",
    ),
    "generator_damage": (
        "Generador dañado; vigilá que no vuelvan apenas te alejes.",
        "Bien, pateaste el generador. Ahora hacé valer esa regresión.",
    ),
    "destruction": (
        "Obstáculo destruido; ese loop ya es bastante menos seguro.",
        "Rompiste el recurso. La próxima vuelta por ahí juega a tu favor.",
    ),
    "skill_check": (
        "Prueba de habilidad limpia, seguí así con ese generador.",
        "Buen skill check; reparación firme y sin regalar ruido.",
    ),
    "repair": (
        "Ese generador está avanzando; mantené un ojo en las rutas de escape.",
        "Buena reparación. Tené preparada la salida si aparece el asesino.",
    ),
    "rescue": (
        "Rescate seguro. Ahora separense antes de regalar dos objetivos juntos.",
        "Buen rescate; salgan de la zona antes de que vuelva el asesino.",
    ),
    "heal": (
        "Curación hecha; recuperaste margen para la próxima persecución.",
        "Ya estás curado. Ahora sí podés volver a presionar objetivos.",
    ),
}


@dataclass(frozen=True)
class DbdEvent:
    name: str
    inferred_role: str
    comment: str


def detect_dbd_event(text: str, selected_role: str = "automatico") -> DbdEvent | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    role = _normalize(selected_role).lower()
    for event_name, patterns, roles in EVENT_PATTERNS:
        if role not in {"", "automatico", "auto"} and role not in roles:
            continue
        padded = f" {normalized} "
        if any(f" {pattern} " in padded for pattern in patterns):
            inferred_role = roles[0]
            return DbdEvent(event_name, inferred_role, random.choice(COMMENTS[event_name]))
    return None


async def _recognize_text_async(image: Image.Image) -> str:
    if OcrEngine is None:
        return ""
    payload = io.BytesIO()
    image.convert("RGB").save(payload, format="PNG")
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(payload.getvalue())
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""
    result = await engine.recognize_async(bitmap)
    return str(result.text or "")


def recognize_text(image: Image.Image) -> str:
    return asyncio.run(_recognize_text_async(image))


class DbdLocalObserver:
    POLL_SECONDS = 2.0
    EVENT_TTL_SECONDS = 12.0

    def __init__(self) -> None:
        self.last_poll = 0.0
        self.recent_events: dict[str, float] = {}

    def poll(self, selected_role: str = "automatico") -> DbdEvent | None:
        now = time.monotonic()
        if now - self.last_poll < self.POLL_SECONDS:
            return None
        self.last_poll = now
        captured = capture_active_monitor(max_size=(1280, 720))
        image = captured.image.convert("RGB")
        width, height = image.size
        # Las notificaciones de puntuación de DBD aparecen a la derecha. Acotar
        # el OCR evita confundir nombres, chat y texto permanente del HUD.
        region = image.crop((int(width * 0.52), 0, width, int(height * 0.72)))
        region = ImageOps.autocontrast(ImageOps.grayscale(region))
        region = ImageEnhance.Contrast(region).enhance(1.8)
        event = detect_dbd_event(recognize_text(region), selected_role)
        if event is None:
            return None
        previous = self.recent_events.get(event.name, 0.0)
        if now - previous < self.EVENT_TTL_SECONDS:
            return None
        self.recent_events[event.name] = now
        return event
