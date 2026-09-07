import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import mock

from PIL import Image

from rebecca_companion.avatar import AvatarRenderer, infer_expression
from rebecca_companion.app import (
    RebeccaCompanionApp,
    dashboard_height,
    monitor_geometry,
    monitor_number_at,
    monitor_position,
)
from rebecca_companion.capture import capture_active_monitor, capture_monitor
from rebecca_companion.client import RebeccaClient, RebeccaConnectionError
from rebecca_companion.command_router import route_command
from rebecca_companion.core_contract import CORE_VERSION
from rebecca_companion.dbd_watch import detect_dbd_event
from rebecca_companion.interaction import InteractionCoordinator, InteractionPhase
from rebecca_companion.instance_control import CompanionControlServer, request_show_existing
from rebecca_companion.live_watch import (
    build_analysis_tick,
    build_live_system_prompt,
    configured_api_keys,
    normalize_live_comment,
)
from rebecca_companion.launcher import core_is_current, port_is_open, summon_existing_window
from rebecca_companion.personal_memory import PersonalMemory, extract_explicit_memories
from rebecca_companion.screen_watch import SceneChangeDetector, SpectatorMode, image_difference_score
from rebecca_companion.settings import CompanionSettings
from rebecca_companion.tray import build_tray_image
from rebecca_companion.voice_listener import (
    VoiceListener,
    compatible_input_sample_rate,
    encode_wav,
    extract_wake_command,
)
import numpy as np


ROOT = Path(__file__).resolve().parents[1]


class CompanionTests(unittest.TestCase):
    def test_live_filtra_silencio_y_acota_comentarios(self):
        self.assertEqual(normalize_live_comment("[SILENCIO]"), "")
        self.assertEqual(normalize_live_comment("Rebecca: Ese Dwight cayó junto al pallet."), "Ese Dwight cayó junto al pallet.")
        self.assertLessEqual(len(normalize_live_comment("palabra " * 80)), 241)

    def test_live_fija_la_perspectiva_del_asesino(self):
        prompt = build_live_system_prompt("asesino")
        tick = build_analysis_tick("DeadByDaylight", "asesino", manual=True)
        self.assertIn("Usuario juega como ASESINO", prompt)
        self.assertIn("Nunca digas que Usuario está herido", prompt)
        self.assertIn("ANALIZA AHORA", tick)
        self.assertIn("Usuario pidió una opinión ahora", tick)

    def test_claves_live_se_cargan_sin_duplicados(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text(
                "GEMINI_API_KEY=principal\nGEMINI_API_KEY_BOCA=respaldo\n",
                encoding="utf-8",
            )
            keys = configured_api_keys(path, {"GEMINI_API_KEY": "principal"})
        self.assertEqual(keys, ["principal", "respaldo"])

    def test_eventos_dbd_respetan_el_rol_confirmado(self):
        evento = detect_dbd_event("SURVIVOR FOUND   CHASE", "asesino")
        self.assertIsNotNone(evento)
        self.assertEqual(evento.inferred_role, "asesino")
        self.assertIsNone(detect_dbd_event("SURVIVOR FOUND", "superviviente"))

        rescate = detect_dbd_event("SAFE HOOK RESCUE", "superviviente")
        self.assertIsNotNone(rescate)
        self.assertEqual(rescate.name, "rescue")
        self.assertIsNone(detect_dbd_event("WHITE GLYPH", "asesino"))

    def test_panel_respeta_el_area_libre_sobre_la_barra_de_tareas(self):
        monitor = {
            "left": 0,
            "top": 0,
            "width": 1366,
            "height": 768,
            "work_left": 0,
            "work_top": 0,
            "work_width": 1366,
            "work_height": 720,
        }
        self.assertEqual(dashboard_height(monitor), 678)

    def test_icono_de_bandeja_se_construye_desde_el_avatar_limpio(self):
        icon = build_tray_image(ROOT / "Rebecca_Sprites")
        self.assertEqual(icon.size, (64, 64))
        self.assertEqual(icon.mode, "RGBA")
        self.assertIsNotNone(icon.getchannel("A").getbbox())

    def test_geometria_centra_la_ventana_en_monitores_con_coordenadas_negativas(self):
        monitor = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
        self.assertEqual(monitor_geometry(monitor, 390, 740), "390x740-1155+170")

    def test_geometria_avatar_recupera_una_posicion_guardada(self):
        monitor = {"left": 1920, "top": 0, "width": 1920, "height": 1080}
        self.assertEqual(
            monitor_geometry(monitor, 360, 360, placement="bottom-right", saved_position=[2100, 80]),
            "360x360+2100+80",
        )

    def test_geometria_avatar_descarta_posiciones_fuera_de_la_pantalla(self):
        monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        self.assertEqual(
            monitor_geometry(monitor, 360, 360, placement="bottom-right", saved_position=[3000, 80]),
            "360x360+1525+685",
        )

    def test_posicion_de_monitor_conserva_coordenadas_negativas_reales(self):
        monitor = {"left": -1360, "top": -3, "width": 1360, "height": 768}
        self.assertEqual(monitor_position(monitor, 360, 360, placement="bottom-right"), (-395, 370))

    def test_detecta_la_pantalla_a_la_que_se_arrastro_el_avatar(self):
        monitors = [
            {"left": -1360, "top": -3, "width": 1360, "height": 768},
            {"left": 0, "top": 0, "width": 1366, "height": 768},
        ]
        self.assertEqual(monitor_number_at(monitors, -300, 400), 1)
        self.assertEqual(monitor_number_at(monitors, 900, 400), 2)

    @mock.patch("rebecca_companion.app.move_window_absolute")
    def test_selector_mueve_realmente_a_la_pantalla_izquierda(self, move_absolute):
        app = object.__new__(RebeccaCompanionApp)
        app.monitors = [
            {"left": -1360, "top": -3, "width": 1360, "height": 768},
            {"left": 0, "top": 0, "width": 1366, "height": 768},
        ]
        app.monitor_number = 2
        app.avatar_only = False
        app.settings = mock.Mock()
        app.monitor_choice = mock.Mock()
        app.root = mock.Mock()

        app.move_to_monitor(1)

        app.settings.set.assert_called_with("monitor_number", 1)
        move_absolute.assert_called_once_with(app.root, -875, 18)

    def test_ocultar_deja_la_escucha_activa_y_retira_la_ventana(self):
        app = object.__new__(RebeccaCompanionApp)
        app.root = mock.Mock()
        app.root.winfo_x.return_value = -500
        app.root.winfo_y.return_value = 120
        app.voice_listener = mock.Mock()
        app.voice_listener.running = False
        app.interactions = mock.Mock()
        app.listener_button = mock.Mock()

        app.hide_until_called()

        app.voice_listener.start_continuous.assert_called_once_with()
        app.interactions.set_passive_listening.assert_called_once_with(True)
        app.root.withdraw.assert_called_once_with()
        self.assertEqual(app._position_before_hiding, (-500, 120))

    @mock.patch("rebecca_companion.app.move_window_absolute")
    def test_llamada_recupera_la_ventana_en_la_posicion_anterior(self, move_absolute):
        app = object.__new__(RebeccaCompanionApp)
        app.root = mock.Mock()
        app.root.state.return_value = "withdrawn"
        app.root.after.side_effect = lambda _delay, callback: callback()
        app._hidden = True
        app._position_before_hiding = (-500, 120)

        app.summon_window()

        app.root.deiconify.assert_called_once_with()
        app.root.lift.assert_called_once_with()
        move_absolute.assert_called_once_with(app.root, -500, 120)
        self.assertFalse(app._hidden)

    def test_detector_solo_dispara_con_un_cambio_visible(self):
        detector = SceneChangeDetector(threshold=7.5)
        negro = Image.new("L", (20, 20), 0)
        blanco = Image.new("L", (20, 20), 255)
        self.assertFalse(detector.changed(negro))
        self.assertFalse(detector.changed(negro))
        self.assertTrue(detector.changed(blanco))

    def test_retrato_permanece_neutral(self):
        self.assertEqual(infer_expression("¡Vamos, ganamos!"), "happy")
        self.assertEqual(infer_expression("Uy no, moriste"), "sad")
        self.assertEqual(infer_expression("Cuidado, boludo"), "angry")

    def test_retrato_estatico_se_redimensiona_sin_recortes(self):
        sprites = ROOT / "Rebecca_Sprites"
        renderer = AvatarRenderer(sprites, width=170)
        self.assertEqual(renderer.base_path.name, "base_limpia.png")
        neutral = renderer._compose("neutral", 0)
        supuesto_gesto = renderer._compose("angry", 0)
        self.assertEqual(neutral.size, (170, 170))
        self.assertNotEqual(neutral.tobytes(), supuesto_gesto.tobytes())

    def test_boca_animada_cambia_sin_mover_el_lienzo(self):
        renderer = AvatarRenderer(ROOT / "Rebecca_Sprites", width=170)
        cerrada = renderer._compose("happy", 0)
        abierta = renderer._compose("happy", 2)
        self.assertEqual(cerrada.size, abierta.size)
        self.assertNotEqual(cerrada.tobytes(), abierta.tobytes())

    @mock.patch("rebecca_companion.client.requests.post")
    def test_chat_acepta_respuesta_de_n8n(self, post):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"output": "Acá estoy."}
        post.return_value = response
        self.assertEqual(RebeccaClient().chat("hola"), "Acá estoy.")
        self.assertEqual(post.call_args.kwargs["json"]["source"], "desktop")
        self.assertIn("local_context", post.call_args.kwargs["json"])

    def test_launcher_detecta_un_puerto_cerrado(self):
        self.assertFalse(port_is_open(1))

    @mock.patch("rebecca_companion.launcher.requests.get")
    def test_launcher_reconoce_core_actual(self, get):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status": "ok",
            "version": CORE_VERSION,
            "capacidades": ["observar_juego_contexto"],
        }
        get.return_value = response
        self.assertTrue(core_is_current())

    @mock.patch("rebecca_companion.launcher.sys.platform", "linux")
    def test_recuperar_instancia_es_seguro_fuera_de_windows(self):
        self.assertFalse(summon_existing_window())

    def test_segundo_inicio_pide_mostrar_la_instancia_existente(self):
        scheduled: list[object] = []
        shown: list[bool] = []
        server = CompanionControlServer(
            schedule=lambda action: scheduled.append(action),
            on_show=lambda: shown.append(True),
            port=0,
        )
        server.start()
        try:
            self.assertTrue(request_show_existing(port=server.port))
            self.assertEqual(len(scheduled), 1)
            scheduled[0]()
            self.assertEqual(shown, [True])
        finally:
            server.stop()

    @mock.patch("rebecca_companion.client.requests.get")
    def test_companion_rechaza_core_viejo(self, get):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"status": "ok", "servicio": "rebecca-core"}
        get.return_value = response
        self.assertFalse(RebeccaClient().health())

    def test_palabra_de_llamada_filtra_el_audio_ambiente(self):
        self.assertEqual(extract_wake_command("Rebecca, abrí Steam"), "abrí Steam")
        self.assertEqual(extract_wake_command("Becca decime la hora"), "decime la hora")
        self.assertIsNone(extract_wake_command("gol de boca juniors"))

    def test_comentario_visual_evitar_segunda_vuelta_por_n8n(self):
        with TemporaryDirectory() as temporary:
            client = RebeccaClient(
                personal_memory=PersonalMemory(Path(temporary) / "memory.json")
            )
            with mock.patch.object(
                client,
                "command",
                return_value={"status": "success", "message": "Uh, preparate: apareció un jefe."},
            ) as command, mock.patch.object(client, "chat") as chat:
                result = client.opinar_juego("Isaac")

        self.assertEqual(result, "Uh, preparate: apareció un jefe.")
        command.assert_called_once_with("opinar_juego", "Isaac", request_timeout=30.0)
        chat.assert_not_called()

    def test_error_de_captura_no_contamina_la_memoria(self):
        client = RebeccaClient()
        with mock.patch.object(
            client,
            "command",
            return_value={"status": "success", "message": "Error de captura: acceso denegado"},
        ), mock.patch.object(client, "chat") as chat:
            with self.assertRaisesRegex(RebeccaConnectionError, "acceso denegado"):
                client.opinar_juego("Isaac")
        chat.assert_not_called()

    @mock.patch("rebecca_companion.capture._capture_with_dxcam")
    @mock.patch("rebecca_companion.capture.mss.mss")
    def test_captura_directx_es_respaldo_de_bitblt(self, mss_factory, directx):
        classic = mock.Mock()
        classic.monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        classic.grab.side_effect = OSError("BitBlt: acceso denegado")
        mss_factory.return_value.__enter__.return_value = classic
        directx.return_value = Image.new("RGB", (1920, 1080), "navy")

        captured = capture_active_monitor(max_size=(1600, 900))

        self.assertEqual(captured.backend, "directx")
        self.assertEqual(captured.image.size, (1600, 900))

    @mock.patch("rebecca_companion.capture._capture_with_dxcam")
    @mock.patch("rebecca_companion.capture.mss.mss")
    def test_captura_telegram_usa_directx_en_monitor_elegido(self, mss_factory, directx):
        classic = mock.Mock()
        classic.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]
        classic.grab.side_effect = OSError("BitBlt: acceso denegado")
        mss_factory.return_value.__enter__.return_value = classic
        directx.return_value = Image.new("RGB", (1920, 1080), "navy")

        captured = capture_monitor(2)

        self.assertEqual(captured.backend, "directx")
        self.assertEqual(captured.title, "Pantalla 2")
        directx.assert_called_once_with(1, False)

    def test_audio_se_codifica_como_wav_valido(self):
        wav = encode_wav([np.zeros(1600, dtype=np.int16), np.ones(1600, dtype=np.int16)])
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav[:16])

    @mock.patch("rebecca_companion.voice_listener.sd.check_input_settings")
    @mock.patch("rebecca_companion.voice_listener.sd.query_devices")
    def test_microfono_usb_usa_su_frecuencia_compatible(self, query_devices, check_settings):
        query_devices.return_value = {
            "name": "USB PnP Audio Device",
            "default_samplerate": 48000.0,
        }

        self.assertEqual(compatible_input_sample_rate(25), 48000)
        check_settings.assert_called_once_with(
            device=25, channels=1, dtype="int16", samplerate=48000
        )

    @mock.patch("rebecca_companion.voice_listener.sd.check_input_settings")
    @mock.patch("rebecca_companion.voice_listener.sd.query_devices")
    def test_microfono_prueba_frecuencias_de_respaldo(self, query_devices, check_settings):
        query_devices.return_value = {
            "name": "Micrófono especial",
            "default_samplerate": 44100.0,
        }
        check_settings.side_effect = [ValueError("no admite 44.1"), None]

        self.assertEqual(compatible_input_sample_rate(7), 48000)
        self.assertEqual(check_settings.call_count, 2)

    def test_voz_usa_la_salida_elegida(self):
        client = RebeccaClient(output_device=9)
        with mock.patch.object(client, "command", return_value={"status": "success"}) as command:
            client.hablar("Dale Boca")
        command.assert_called_once_with("hablar_pc", "Dale Boca", parametro2="9")

    @mock.patch("rebecca_companion.client.requests.post")
    def test_transcripcion_envia_wav_al_webhook(self, post):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"text": "abrí Steam"}
        post.return_value = response
        self.assertEqual(RebeccaClient().transcribe_audio(b"RIFFprueba"), "abrí Steam")
        self.assertEqual(post.call_args.kwargs["files"]["data"][2], "audio/wav")

    def test_escucha_toma_el_microfono_predeterminado(self):
        listener = VoiceListener(lambda _audio: "hola", lambda _text: None, lambda _status: None)
        self.assertIsInstance(listener.device, int)

    @mock.patch("rebecca_companion.voice_listener.record_utterance")
    def test_escucha_activa_acepta_orden_despues_de_decir_solo_rebecca(self, record):
        record.side_effect = [b"wake", b"orden"]
        transcribe = mock.Mock(side_effect=["Rebecca", "abrí Steam"])
        on_text = mock.Mock()
        on_wake = mock.Mock()
        listener = VoiceListener(transcribe, on_text, lambda _status: None, on_wake=on_wake)

        listener._listen_once(True)

        on_text.assert_called_once_with("abrí Steam")
        on_wake.assert_called_once_with()
        self.assertTrue(listener._processing.is_set())
        self.assertTrue(listener.conversation_active)
        self.assertEqual(record.call_args_list[0].kwargs["silence_seconds"], 1.8)
        self.assertEqual(record.call_args_list[1].kwargs["silence_seconds"], 1.5)

        listener.finish_processing()
        self.assertFalse(listener._processing.is_set())

    @mock.patch("rebecca_companion.voice_listener.record_utterance", return_value=b"audio")
    def test_escucha_activa_no_procesa_audio_sin_palabra_de_activacion(self, _record):
        on_text = mock.Mock()
        listener = VoiceListener(lambda _audio: "Gracias", on_text, lambda _status: None)

        listener._listen_once(True)

        on_text.assert_not_called()
        self.assertFalse(listener._processing.is_set())

    @mock.patch("rebecca_companion.voice_listener.record_utterance", return_value=b"audio")
    def test_escucha_activa_no_anuncia_audio_ambiente_antes_de_validar_rebecca(self, record):
        statuses: list[str] = []
        listener = VoiceListener(lambda _audio: "ruido del juego", mock.Mock(), statuses.append)

        listener._listen_once(True)

        self.assertFalse(record.call_args.kwargs["announce_detected_speech"])
        self.assertNotIn("Entendiendo lo que dijiste...", statuses)

    def test_coordinador_no_deja_pisarse_observar_pensar_y_hablar(self):
        coordinator = InteractionCoordinator()
        coordinator.set_passive_listening(True)
        self.assertEqual(coordinator.snapshot().phase, InteractionPhase.LISTENING)

        observation = coordinator.try_begin("spectator", InteractionPhase.OBSERVING)
        self.assertIsNotNone(observation)
        self.assertIsNone(coordinator.try_begin("voice", InteractionPhase.THINKING))
        self.assertTrue(coordinator.transition(observation, InteractionPhase.SPEAKING))
        self.assertEqual(coordinator.snapshot().phase, InteractionPhase.SPEAKING)
        self.assertTrue(coordinator.finish(observation))
        self.assertEqual(coordinator.snapshot().phase, InteractionPhase.LISTENING)

    def test_memoria_personal_solo_aprende_datos_explicitos(self):
        facts = extract_explicit_memories(
            "Recordá que mi cumpleaños es el 12 de mayo y me gusta el power metal."
        )
        self.assertTrue(any("cumpleaños" in fact.text.lower() or "12 de mayo" in fact.text.lower() for fact in facts))
        self.assertTrue(any("power metal" in fact.text.lower() for fact in facts))
        self.assertEqual(
            extract_explicit_memories(
                "Siento que los celos me superan y busco opiniones para entender qué hacer."
            ),
            [],
        )
        dislikes = extract_explicit_memories("No me gusta el reguetón")
        self.assertEqual([fact.kind for fact in dislikes], ["rechazo"])

    def test_memoria_personal_persiste_y_llega_al_chat(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "personal.json"
            memory = PersonalMemory(path)
            memory.learn("Me gusta escuchar Soda Stereo")
            restored = PersonalMemory(path)
            self.assertIn("Soda Stereo", restored.summary())

            client = RebeccaClient(personal_memory=restored)
            response = mock.Mock(status_code=200)
            response.raise_for_status.return_value = None
            response.json.return_value = {"output": "Lo tengo presente."}
            with mock.patch("rebecca_companion.client.requests.post", return_value=response) as post:
                client.chat("¿Qué música podríamos poner?")
            self.assertIn("Soda Stereo", post.call_args.kwargs["json"]["personal_memory"])
            self.assertIn("MEMORIA PERSONAL", post.call_args.kwargs["json"]["text"])

    def test_preferencias_de_audio_se_restauran(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            settings = CompanionSettings(path)
            settings.set("microphone_name", "USB PnP Audio Device")
            settings.set("speaker_name", "Altavoces Realtek")
            restored = CompanionSettings(path)
            self.assertEqual(restored.get("microphone_name"), "USB PnP Audio Device")
            self.assertEqual(restored.get("speaker_name"), "Altavoces Realtek")

    def test_observador_reserva_el_descarte_para_transiciones_bruscas_y_evita_repetirse(self):
        dark = Image.new("L", (20, 20), 0)
        light = Image.new("L", (20, 20), 255)
        gameplay_movement = Image.new("L", (20, 20), 18)
        self.assertLess(
            image_difference_score(dark, gameplay_movement),
            SpectatorMode.TRANSITION_THRESHOLD,
        )
        self.assertGreater(
            image_difference_score(dark, light),
            SpectatorMode.TRANSITION_THRESHOLD,
        )

        spectator = SpectatorMode(mock.Mock(), mock.Mock(), mock.Mock())
        self.assertFalse(spectator._is_repeated_comment("Apareció un jefe", 100.0))
        self.assertTrue(spectator._is_repeated_comment("¡Apareció un jefe!", 101.0))

    def test_orden_de_voz_ocupada_se_conserva_hasta_poder_procesarla(self):
        app = RebeccaCompanionApp.__new__(RebeccaCompanionApp)
        app.busy = True
        app._comment_in_progress = False
        app._pending_voice_text = None
        app.spectator = mock.Mock()
        app._pending_voice_poll_scheduled = False
        app.status = mock.Mock()
        app.root = mock.Mock()
        app.voice_listener = mock.Mock()
        app.process_user_text = mock.Mock(return_value=True)

        app._dispatch_voice_text("abrí Steam")

        self.assertEqual(app._pending_voice_text, "abrí Steam")
        app.process_user_text.assert_not_called()
        app.root.after.assert_called_once()

        app.busy = False
        app._flush_pending_voice()

        app.process_user_text.assert_called_once_with("abrí Steam", "desktop_voice")
        self.assertIsNone(app._pending_voice_text)
        app.voice_listener.begin_processing.assert_called_once()

    def test_comandos_frecuentes_evitan_la_vuelta_por_la_ia(self):
        abrir = route_command("Rebecca, abrime Discord")
        ejecutar = route_command("Rebeca, ejecutá Notepad")
        captura = route_command("Rebecca, tomá una captura de la pantalla 2")
        volumen = route_command("subí el volumen")
        musica = route_command("pausa la música")
        musica_actual = route_command("¿Qué estamos escuchando?")
        pedidos = route_command("mostrame los pedidos pendientes")
        self.assertEqual((abrir.action, abrir.parameter), ("abrir_programa", "discord"))
        self.assertEqual((ejecutar.action, ejecutar.parameter), ("abrir_programa", "notepad"))
        self.assertEqual(captura.action, "capturar_pantalla_local")
        self.assertIn("pantalla 2", captura.parameter.lower())
        self.assertEqual(volumen.action, "sistema")
        self.assertEqual(musica.action, "pausar_musica")
        self.assertEqual(musica_actual.action, "estado_musica")
        self.assertEqual(pedidos.action, "reporte_pedidos")

    def test_observar_pantalla_no_es_un_comando_manual(self):
        self.assertIsNone(route_command("Rebecca, observá la pantalla"))

    def test_conversacion_normal_sigue_en_la_memoria(self):
        self.assertIsNone(route_command("¿Cómo estás hoy, Rebecca?"))

    @mock.patch("rebecca_companion.client.requests.post")
    def test_accion_directa_se_inyecta_en_la_siguiente_conversacion(self, post):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"output": "Sí, ya quedó abierto."}
        post.return_value = response
        client = RebeccaClient()
        client.remember_action("abrí Steam", "Steam abierto")
        client.chat("¿lo abriste?")
        self.assertIn("Steam abierto", post.call_args.kwargs["json"]["local_context"])
        self.assertIn("Steam abierto", post.call_args.kwargs["json"]["text"])
        self.assertEqual(client._recent_actions, [])

    @mock.patch("rebecca_companion.client.time.sleep")
    @mock.patch("rebecca_companion.client.requests.post")
    def test_chat_reintenta_un_error_transitorio_de_n8n(self, post, sleep):
        fallo = mock.Mock(status_code=500)
        exito = mock.Mock(status_code=200)
        exito.raise_for_status.return_value = None
        exito.json.return_value = {"output": "Ahora sí."}
        post.side_effect = [fallo, exito]

        self.assertEqual(RebeccaClient().chat("hola"), "Ahora sí.")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1.0)

    @mock.patch("rebecca_companion.client.requests.post")
    def test_chat_recupera_una_llamada_interna_sin_duplicarla(self, post):
        agent_response = mock.Mock(status_code=200)
        agent_response.raise_for_status.return_value = None
        agent_response.json.return_value = {
            "output": "Ejecutando el comando local.",
            "toolCall": {
                "accion": "estado_musica",
                "parametro": "",
                "parametro2": "",
                "parametro3": "",
                "parametro4": "",
                "id": "call_4905221",
            },
        }
        core_response = mock.Mock(status_code=200)
        core_response.raise_for_status.return_value = None
        core_response.json.return_value = {
            "status": "success",
            "message": "Está sonando Megamix de Vengaboys.",
        }
        post.side_effect = [agent_response, core_response]

        client = RebeccaClient()
        self.assertEqual(
            client.chat("¿Qué estamos escuchando?"),
            "Está sonando Megamix de Vengaboys.",
        )
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["accion"], "estado_musica")
        self.assertNotIn("id", post.call_args_list[1].kwargs["json"])


if __name__ == "__main__":
    unittest.main()
