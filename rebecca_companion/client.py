from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import time
from typing import Any

import requests

from .personal_memory import PersonalMemory
from .game_context import GameContext


ALLOWED_COMPANION_ACTIONS = frozenset(
    {
        "sistema", "abrir_programa", "imprimir_mesa", "imprimir_pdf", "estado_pc",
        "activar_observador", "desactivar_observador", "estado_observador", "buscar_archivo",
        "analizar_imagen", "hablar", "crear_recordatorio", "crear_recordatorio_recurrente",
        "consultar_recordatorios", "borrar_recordatorio", "crear_pedido", "reporte_pedidos",
        "gestion_lote_pedidos", "marcar_entregado", "actualizar_fecha_entrega", "consultar_cliente",
        "fijar_deuda", "registrar_pago", "consultar_deudas", "generar_recibo_sena", "exportar_excel",
        "registrar_venta", "anotar_venta", "actualizar_stock", "consultar_stock", "agregar_producto",
        "cotizar", "listar_precios", "programar_correo", "crear_turno", "consultar_turnos",
        "cancelar_turno", "completar_turno", "seguir_partido", "detener_partido",
        "consultar_estado_partido", "resumen_partido_vivo", "pausar_entretiempo", "buscar_musica",
        "pausar_musica", "continuar_musica", "siguiente_cancion", "anterior_cancion", "estado_musica",
        "perfil_musical", "activar_no_molestar", "desactivar_no_molestar", "estado_no_molestar",
        "activar_panico", "desactivar_panico", "explicar_noticia", "generar_reporte_pdf",
    }
)


def _is_internal_tool_trace(text: str) -> bool:
    value = str(text or "").strip().lower()
    return value.startswith("calling ") and " with input:" in value


def _extract_internal_tool_call(text: str) -> dict[str, Any] | None:
    value = str(text or "").strip()
    if not _is_internal_tool_trace(value):
        return None
    match = re.search(r"\bwith input:\s*", value, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        payload, _end = json.JSONDecoder().raw_decode(value[match.end():].lstrip())
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


class RebeccaConnectionError(RuntimeError):
    pass


@dataclass
class RebeccaClient:
    core_url: str = "http://127.0.0.1:8000"
    companion_webhook: str = "http://127.0.0.1:5678/webhook/rebecca-companion"
    transcription_webhook: str = "http://127.0.0.1:5678/webhook/rebecca-voice-transcribe"
    timeout: int = 75
    output_device: int | None = None
    _recent_actions: list[str] = field(default_factory=list, init=False, repr=False)
    personal_memory: PersonalMemory = field(default_factory=PersonalMemory, repr=False)
    game_context: GameContext = field(default_factory=GameContext, repr=False)

    def health(self) -> bool:
        try:
            response = requests.get(f"{self.core_url}/health", timeout=3)
            response.raise_for_status()
            info = response.json()
            return "observar_juego_contexto" in info.get("capacidades", [])
        except (requests.RequestException, ValueError, AttributeError):
            return False

    def command(
        self,
        accion: str,
        parametro: str = "",
        *,
        request_timeout: float | None = None,
        **extra: str,
    ) -> dict[str, Any]:
        payload = {
            "accion": accion,
            "parametro": parametro,
            "parametro2": extra.get("parametro2", ""),
            "parametro3": extra.get("parametro3", ""),
            "parametro4": extra.get("parametro4", ""),
        }
        try:
            response = requests.post(
                f"{self.core_url}/ejecutar",
                json=payload,
                timeout=request_timeout or self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RebeccaConnectionError(f"El núcleo local de Rebecca no respondió: {exc}") from exc

    def chat(self, texto: str, origen: str = "desktop", *, allow_actions: bool = True) -> str:
        try:
            self.personal_memory.learn(texto)
            contexto_local = "\n".join(self._recent_actions[-5:])
            memoria_personal = self.personal_memory.summary()
            mensaje = str(texto or "").strip()
            contexto_partida = self.game_context.summary()
            if contexto_partida:
                mensaje += "\n\n[" + contexto_partida + "]"
            if contexto_local:
                mensaje += (
                    "\n\n[CONTEXTO DE ACCIONES LOCALES YA EJECUTADAS; "
                    "no vuelvas a ejecutarlas: " + contexto_local + "]"
                )
            if memoria_personal:
                mensaje += (
                    "\n\n[MEMORIA PERSONAL EXPLÍCITA DE Usuario; usala con naturalidad y "
                    "no inventes datos fuera de esta lista:\n" + memoria_personal + "]"
                )

            response = None
            for attempt in range(2):
                response = requests.post(
                    self.companion_webhook,
                    json={
                        "text": mensaje,
                        "source": origen,
                        "local_context": contexto_local,
                        "personal_memory": memoria_personal,
                        "game_context": contexto_partida,
                    },
                    timeout=self.timeout,
                )
                if response.status_code not in {500, 502, 503, 504} or attempt == 1:
                    break
                # Gemini/n8n pueden devolver un fallo transitorio durante picos
                # de demanda. Un unico reintento evita mostrar ese primer error.
                time.sleep(1.0)
            assert response is not None
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                salida = data.get("output") or data.get("message") or data.get("text")
                prepared_call = data.get("toolCall")
                if not isinstance(prepared_call, dict) and salida:
                    prepared_call = _extract_internal_tool_call(str(salida))
                if isinstance(prepared_call, dict):
                    action = str(prepared_call.get("accion") or "").strip()
                    if action not in ALLOWED_COMPANION_ACTIONS:
                        raise RebeccaConnectionError(
                            "n8n preparó una acción no permitida y Rebecca la bloqueó."
                        )
                    if not allow_actions:
                        return (
                            "Te entendí, pero durante la charla sin palabra de activación no ejecuto "
                            "comandos. Decime ‘Rebecca’ y repetí la orden exacta."
                        )
                    result = self.command(
                        action,
                        str(prepared_call.get("parametro") or ""),
                        parametro2=str(prepared_call.get("parametro2") or ""),
                        parametro3=str(prepared_call.get("parametro3") or ""),
                        parametro4=str(prepared_call.get("parametro4") or ""),
                    )
                    message = str(result.get("message") or "").strip()
                    if result.get("status") != "success" or not message:
                        raise RebeccaConnectionError(message or "El comando local no pudo completarse.")
                    self.remember_action(texto, message)
                    return message
                if salida:
                    if _is_internal_tool_trace(str(salida)):
                        raise RebeccaConnectionError(
                            "El comando se ejecutó, pero n8n no devolvió su confirmación final. "
                            "No lo repetí para evitar duplicarlo."
                        )
                    self._recent_actions.clear()
                    return str(salida).strip()
            raise RebeccaConnectionError("n8n respondió sin texto.")
        except requests.RequestException as exc:
            raise RebeccaConnectionError(f"El canal de conversación de n8n no respondió: {exc}") from exc

    def remember_action(self, requested: str, result: str) -> None:
        self._recent_actions.append(f"Usuario pidió: {requested}. Rebecca hizo: {result}")
        del self._recent_actions[:-5]

    def remember_observation(self, window_title: str, comment: str, *, observed_at: float | None = None) -> None:
        title = str(window_title or "ventana activa").strip()
        observation = str(comment or "").strip()
        if not observation:
            return
        # Commentary is not an executed command or a verified visual fact.
        self.game_context.observe_window(title, observed_at)
        self.game_context.note("comentario_de_rebecca", observation)

    def learn_personal(self, text: str) -> None:
        self.personal_memory.learn(text)

    def transcribe_audio(self, wav_data: bytes) -> str:
        try:
            response = requests.post(
                self.transcription_webhook,
                files={"data": ("rebecca_pc.wav", wav_data, "audio/wav")},
                timeout=(3, 15),
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                text = data.get("text") or data.get("transcript")
                if text:
                    return str(text).strip()
            raise RebeccaConnectionError("La transcripción llegó vacía.")
        except requests.RequestException as exc:
            raise RebeccaConnectionError(f"No pude transcribir el micrófono: {exc}") from exc

    def opinar_juego(self, contexto: str = "") -> str:
        # El Core ya devuelve un comentario breve con personalidad. Evitar una
        # segunda vuelta por n8n elimina varios segundos y resultados obsoletos.
        memoria = self.personal_memory.summary(max_items=4, max_chars=260)
        contexto_completo = str(contexto or "").strip()
        if memoria:
            contexto_completo += (
                "\nMemoria personal útil (no la menciones si no viene al caso):\n" + memoria
            )
        resultado = self.command(
            "opinar_juego",
            contexto_completo,
            request_timeout=30.0,
        )
        if resultado.get("status") != "success":
            raise RebeccaConnectionError(resultado.get("message", "No pude analizar el juego."))
        comentario = str(resultado.get("message", "")).strip()
        if comentario.lower().startswith(("error de captura:", "error de visión:")):
            raise RebeccaConnectionError(comentario)
        return comentario

    def hablar(self, texto: str, *, expires_at: float | None = None) -> dict[str, Any]:
        device = "" if self.output_device is None else str(self.output_device)
        if expires_at is not None:
            return self.command("hablar_pc", texto, parametro2=device, parametro3=str(expires_at))
        return self.command("hablar_pc", texto, parametro2=device)

    def detener_voz(self) -> None:
        try:
            self.command("detener_voz_pc")
        except RebeccaConnectionError:
            pass
