from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoutedCommand:
    action: str
    parameter: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def _plain(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", value.lower()).strip(" .!?¡¿")


def route_command(text: str) -> RoutedCommand | None:
    """Routes unambiguous, frequent commands without waiting for an AI round trip."""
    original = str(text or "").strip()
    value = re.sub(r"^(rebecca|rebeca|becca)\s*[,;:]\s*", r"\1 ", _plain(original))
    if not value:
        return None

    commerce = re.sub(r'^(?:rebecca|rebeca|becca)\s+', '', value)
    if re.match(r'^(?:vendi\b|(?:registra\w*|anota\w*|modifica\w*|corrige|corregi|anula\w*|cancela\w*)\s+(?:(?:una|la)\s+)?venta\b|(?:cambia|actualiza|fija)(?: el)? precio\b|(?:las?|los?)\s+.+?\s+(?:(?:tienen? un nuevo precio[,;]?\s*)?(?:ahora\s+)?(?:salen?|cuestan?|vale[n]?)\s+\$?\d)|(?:marca\w*|pone|pon)\s+(?:(?:el|los)\s+)?pedidos?\s+#?\d|(?:mostrame|mostrar|lista|listar|ver|consulta|consultar)(?: las)? ventas\b)', commerce):
        return RoutedCommand('gestionar_comercio', original)

    match = re.match(r"^(?:rebecca |rebeca |becca )?(?:abri|abrir|abre|abrime|inicia|iniciar|ejecuta|ejecutar|lanza|lanzar)\s+(.+)$", value)
    if match:
        target = match.group(1).strip()
        target = re.sub(r"^(?:el|la|los|las|programa|aplicacion|app)\s+", "", target)
        if target:
            return RoutedCommand("abrir_programa", target)

    if re.match(
        r"^(?:rebecca |rebeca |becca )?(?:toma|tomar|saca|sacar|hace|hacer|manda|mandame|envia|enviame|captura|capturar)\b.*\b(?:captura|pantallazo|screenshot|pantallas?)\b",
        value,
    ):
        return RoutedCommand("capturar_pantalla_local", original)

    if re.fullmatch(
        r"(?:rebecca |rebeca |becca )?(?:minimiza|minimizar|mostra|mostrar)\s+(?:todo|el escritorio|(?:todas )?las ventanas)",
        value,
    ):
        return RoutedCommand("sistema", original)

    if re.fullmatch(
        r"(?:rebecca |rebeca |becca )?(?:(?:subi|subir|aumenta|aumentar|baja|bajar|disminui|disminuir)\s+(?:el )?(?:volumen|audio|sonido)|(?:mutea|mutear|silencia|silenciar)(?:\s+(?:el )?(?:volumen|audio|sonido))?)",
        value,
    ):
        return RoutedCommand("sistema", original)

    if re.fullmatch(r"(?:rebecca |becca )?(?:pausa|pausar|pausa la musica|pausa la cancion)", value):
        return RoutedCommand("pausar_musica")
    if re.fullmatch(r"(?:rebecca |becca )?(?:segui|siguiente|proxima|pasa la cancion|siguiente cancion)", value):
        return RoutedCommand("siguiente_cancion")
    if re.fullmatch(r"(?:rebecca |becca )?(?:anterior|volver cancion|cancion anterior)", value):
        return RoutedCommand("anterior_cancion")
    if re.fullmatch(r"(?:rebecca |becca )?(?:continua|continuar|play|reanuda|reproducir)", value):
        return RoutedCommand("continuar_musica")

    match = re.match(r"^(?:rebecca |becca )?(?:pone|pon|reproduce|busca)\s+(?:la cancion |musica |el tema )?(.+)$", value)
    if match and match.group(1).strip():
        return RoutedCommand("buscar_musica", match.group(1).strip())

    if re.search(
        r"\b(?:que suena|que esta sonando|que estamos escuchando|que estoy escuchando|"
        r"que cancion (?:suena|esta sonando|estas reproduciendo)|"
        r"que musica (?:suena|esta sonando|estas reproduciendo)|estado de la musica)\b",
        value,
    ):
        return RoutedCommand("estado_musica")
    if re.search(r"\b(?:estado|como esta)\b.*\b(?:pc|computadora|compu)\b", value):
        return RoutedCommand("estado_pc")
    if re.search(r"\b(?:activa|prende)\b.*\bno molestar\b", value):
        return RoutedCommand("activar_no_molestar")
    if re.search(r"\b(?:desactiva|apaga|quita)\b.*\bno molestar\b", value):
        return RoutedCommand("desactivar_no_molestar")

    match = re.match(r"^(?:rebecca |becca )?(?:segui|sigue|activa el seguimiento de)\s+(?:el partido (?:de )?)?(.+)$", value)
    if match and match.group(1).strip():
        return RoutedCommand("seguir_partido", match.group(1).strip())
    if re.search(r"\b(?:deja|detene|para|apaga)\b.*\b(?:partido|seguimiento)\b", value):
        return RoutedCommand("detener_partido")

    if re.fullmatch(r"(?:rebecca |becca )?(?:mostrame|muestra|lista|ver)\s+(?:los )?pedidos(?: pendientes)?", value):
        return RoutedCommand("reporte_pedidos")
    if re.fullmatch(r"(?:rebecca |becca )?(?:mostrame|muestra|lista|ver|consulta)\s+(?:las )?deudas", value):
        return RoutedCommand("consultar_deudas")
    if re.fullmatch(r"(?:rebecca |becca )?(?:mostrame|muestra|lista|ver|consulta)\s+(?:el )?stock", value):
        return RoutedCommand("consultar_stock")
    if re.fullmatch(r"(?:rebecca |becca )?(?:mostrame|muestra|lista|ver|consulta)\s+(?:los )?recordatorios", value):
        return RoutedCommand("consultar_recordatorios")
    if re.fullmatch(r"(?:rebecca |becca )?(?:mostrame|muestra|lista|ver)\s+(?:los )?precios", value):
        return RoutedCommand("listar_precios")

    return None
