import asyncio
import base64
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import servidor_controles_becca as rebecca


class RebeccaCoreTests(unittest.TestCase):
    def test_captura_local_no_usa_telegram(self):
        captura = Mock()
        captura.title = "Pantalla 2"
        captura.backend = "mss"
        captura.image = Mock()
        with tempfile.TemporaryDirectory() as carpeta, patch.object(
            rebecca, "BASE_DIR", carpeta
        ), patch.object(rebecca, "capture_monitor", return_value=captura) as capturar, patch.object(
            rebecca.requests, "post"
        ) as enviar:
            respuesta = rebecca.capturar_pantalla_local("captura pantalla 2")
        self.assertEqual(respuesta["status"], "success")
        capturar.assert_called_once_with(2, combined=False, max_size=None)
        captura.image.save.assert_called_once()
        enviar.assert_not_called()

    def test_sistema_cancela_antes_de_interpretar_apagado(self):
        with patch.object(rebecca.os, "system", return_value=0) as ejecutar:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="sistema", parametro="abortar apagado")
            ))
        self.assertEqual(respuesta["status"], "success")
        ejecutar.assert_called_once_with("shutdown /a")

    def test_sistema_no_apaga_por_una_frase_ambigua(self):
        with patch.object(rebecca.os, "system") as ejecutar:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="sistema", parametro="apagar pantalla")
            ))
        self.assertEqual(respuesta["status"], "error")
        ejecutar.assert_not_called()

    def test_sistema_desconocido_responde_sin_ejecutar(self):
        with patch.object(rebecca.os, "system") as ejecutar, patch.object(rebecca.pyautogui, "press") as tecla:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="sistema", parametro="hacer magia")
            ))
        self.assertEqual(respuesta, {
            "status": "error",
            "message": "No reconocí ese control del sistema. No ejecuté nada.",
        })
        ejecutar.assert_not_called()
        tecla.assert_not_called()

    def test_workflow_antiguo_no_puede_abrir_la_api_directa(self):
        guard = Mock()
        guard.status.return_value = {"status": "success", "activo": True, "team_id": 451}
        with patch.object(rebecca, "FOOTBALL_WATCH", guard):
            result = asyncio.run(rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="consultar_estado_partido")))
        self.assertFalse(result["activo"])
        guard.request.assert_not_called()

    def test_workflow_protegido_usa_guardia_y_no_toca_inactividad(self):
        guard = Mock()
        guard.status.return_value = {"status": "success", "activo": False}
        guard.poll.return_value = {"response": []}
        with patch.object(rebecca, "FOOTBALL_WATCH", guard), patch.object(rebecca, "ULTIMA_INTERACCION", 123):
            for action in ("consultar_estado_partido_protegido", "consultar_partido_protegido"):
                asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(accion=action)))
            self.assertEqual(rebecca.ULTIMA_INTERACCION, 123)
        guard.status.assert_called_once()
        guard.poll.assert_called_once()

    def test_agenda_no_reactiva_ni_envia_mensajes(self):
        guard = Mock()
        guard.daily_fixtures.return_value = [{"fixture": {"status": {"short": "1H"}}}]
        with patch.object(rebecca, "FOOTBALL_WATCH", guard), patch.object(
            rebecca, "obtener_id_equipo", return_value=451
        ), patch.object(rebecca.requests, "post") as send:
            result = asyncio.run(rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="chequear_partidos_dia")))
        self.assertIn("no se activa solo", result["message"])
        guard.start.assert_not_called()
        send.assert_not_called()

    def test_cuota_futbol_se_informa_sin_reintentar_activacion(self):
        with patch.object(rebecca, "obtener_id_equipo", side_effect=rebecca.FootballUnavailable("Cuota agotada")) as lookup:
            result = asyncio.run(rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="seguir_partido", parametro="boca")))
        self.assertEqual(result, {"status": "error", "message": "Cuota agotada"})
        lookup.assert_called_once()

    def test_gemini_visual_usa_un_deadline_admitido(self):
        self.assertGreaterEqual(rebecca.GEMINI_HTTP_TIMEOUT_MS, 10000)

    def test_estado_observador_es_un_comando_real(self):
        solicitud = rebecca.ComandoMaster(accion="estado_observador")
        respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(solicitud))
        self.assertEqual(respuesta["status"], "success")
        self.assertIn("observador", respuesta["message"].lower())

    def test_ampliar_noticia_hace_una_sola_consulta_acotada(self):
        respuesta_http = Mock(status_code=200)
        respuesta_http.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Resumen verificado de la noticia."}]}}]
        }

        with patch.object(rebecca.requests, "post", return_value=respuesta_http) as consultar:
            respuesta = asyncio.run(
                rebecca.ejecutar_comando_maestro(
                    rebecca.ComandoMaster(
                        accion="explicar_noticia",
                        parametro="Boca volvió a la Bombonera ante Lanús",
                    )
                )
            )

        self.assertEqual(respuesta, {"status": "success", "message": "Resumen verificado de la noticia."})
        consultar.assert_called_once()
        _, argumentos = consultar.call_args
        self.assertEqual(argumentos["timeout"], 8)
        self.assertIn("gemini-3.5-flash:generateContent", consultar.call_args.args[0])
        self.assertIn("Boca volvió a la Bombonera", argumentos["json"]["contents"][0]["parts"][0]["text"])

    def test_ampliar_noticia_cambia_de_modelo_si_el_primero_agota_cuota(self):
        sin_cuota = Mock(status_code=429, text="quota exceeded")
        alternativa = Mock(status_code=200)
        alternativa.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Respuesta desde el modelo alternativo."}]}}]
        }

        with patch.object(rebecca.requests, "post", side_effect=[sin_cuota, alternativa]) as consultar:
            respuesta = asyncio.run(
                rebecca.ejecutar_comando_maestro(
                    rebecca.ComandoMaster(
                        accion="explicar_noticia",
                        parametro="Boca ante Lanús en la Bombonera",
                    )
                )
            )

        self.assertEqual(respuesta["status"], "success")
        self.assertEqual(consultar.call_count, 2)
        self.assertIn("gemini-3.5-flash-lite:generateContent", consultar.call_args_list[1].args[0])

    def test_ampliar_noticia_usa_rss_si_todos_los_modelos_agotan_cuota(self):
        sin_cuota = Mock(status_code=429, text="quota exceeded")
        rss = Mock(status_code=200)
        rss.content = b"""<?xml version='1.0' encoding='UTF-8'?>
        <rss><channel><item><title>Boca vencio a Lanus - Diario</title>
        <link>https://news.google.com/articles/ejemplo</link>
        <pubDate>Sat, 29 Aug 2026 12:00:00 GMT</pubDate></item></channel></rss>"""
        rss.raise_for_status.return_value = None

        with patch.object(rebecca.requests, "post", side_effect=[sin_cuota, sin_cuota]), patch.object(
            rebecca.requests, "get", return_value=rss
        ) as consultar_rss:
            respuesta = asyncio.run(
                rebecca.ejecutar_comando_maestro(
                    rebecca.ComandoMaster(
                        accion="explicar_noticia",
                        parametro="triunfazo afónico de Boca ante Lanús",
                    )
                )
            )

        self.assertEqual(respuesta["status"], "success")
        self.assertIn("Boca vencio a Lanus", respuesta["message"])
        self.assertEqual(consultar_rss.call_args.kwargs["params"]["q"], "triunfazo agónico de Boca ante Lanús")
        self.assertEqual(consultar_rss.call_args.kwargs["timeout"], 6)

    def test_gestion_lote_reconoce_accion_e_ids_en_parametros_separados(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "pedidos.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                conn = sqlite3.connect(db)
                conn.executemany(
                    "INSERT INTO pedidos (id, producto, estado, seguimiento_token) VALUES (?, ?, 'pendiente', ?)",
                    [(10, "Remera", "token-10"), (11, "Taza", "token-11")],
                )
                conn.commit()
                conn.close()

                respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(
                    rebecca.ComandoMaster(
                        accion="gestion_lote_pedidos",
                        parametro="eliminar",
                        parametro2="10",
                        parametro3="11",
                    )
                ))

                conn = sqlite3.connect(db)
                restantes = conn.execute("SELECT id FROM pedidos ORDER BY id").fetchall()
                conn.close()

        self.assertEqual(respuesta["status"], "success", respuesta)
        self.assertIn("#10", respuesta["message"])
        self.assertIn("#11", respuesta["message"])
        self.assertEqual(restantes, [])

    def test_gestion_lote_reconoce_id_antes_de_la_accion(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "pedidos.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                conn = sqlite3.connect(db)
                conn.execute("INSERT INTO pedidos (id, producto, estado, seguimiento_token) VALUES (10, 'Remera', 'pendiente', 'token-10')")
                conn.commit()
                conn.close()

                respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(
                    rebecca.ComandoMaster(
                        accion="gestion_lote_pedidos",
                        parametro="10",
                        parametro2="borrar",
                    )
                ))

        self.assertEqual(respuesta["status"], "success", respuesta)
        self.assertIn("#10", respuesta["message"])

    def test_captura_telegram_no_sombrea_el_cliente_http_global(self):
        captura = Mock()
        captura.backend = "mss"
        respuesta_telegram = Mock(status_code=200, text="ok")

        with patch.object(rebecca, "capture_monitor", return_value=captura) as capturar, patch.object(
            rebecca.requests, "post", return_value=respuesta_telegram
        ) as enviar, patch("builtins.open", mock_open(read_data=b"jpeg")):
            respuesta = asyncio.run(
                rebecca.ejecutar_comando_maestro(
                    rebecca.ComandoMaster(accion="sistema", parametro="captura pantalla 1")
                )
            )

        self.assertEqual(respuesta["status"], "success", respuesta)
        capturar.assert_called_once_with(1, combined=False, max_size=None)
        captura.image.save.assert_called_once()
        enviar.assert_called_once()

    def test_captura_lenta_no_bloquea_otros_comandos(self):
        liberar_captura = threading.Event()

        def captura_lenta(_parametro):
            liberar_captura.wait(timeout=1)
            return {"status": "success", "message": "captura lista"}

        async def ejecutar_en_paralelo():
            inicio = time.perf_counter()
            tarea_captura = asyncio.create_task(
                rebecca.ejecutar_comando_maestro(
                    rebecca.ComandoMaster(accion="sistema", parametro="captura pantalla 1")
                )
            )
            await asyncio.sleep(0.02)
            respuesta_rapida = await rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="tiempo_inactivo")
            )
            demora_comando_rapido = time.perf_counter() - inicio
            liberar_captura.set()
            await tarea_captura
            return respuesta_rapida, demora_comando_rapido

        temporizador = threading.Timer(0.35, liberar_captura.set)
        temporizador.start()
        try:
            with patch.object(rebecca, "capturar_y_enviar_telegram", side_effect=captura_lenta):
                respuesta, demora = asyncio.run(ejecutar_en_paralelo())
        finally:
            liberar_captura.set()
            temporizador.cancel()

        self.assertEqual(respuesta["status"], "success")
        self.assertLess(demora, 0.15, f"La captura bloqueó el loop durante {demora:.3f}s")

    def test_busqueda_de_apps_windows_se_cachea(self):
        resultado = Mock(
            returncode=0,
            stdout='[{"Name":"Discord","AppID":"Discord.App"}]',
            stderr="",
        )
        cache_anterior = rebecca._START_APPS_CACHE
        cache_at_anterior = rebecca._START_APPS_CACHE_AT
        rebecca._START_APPS_CACHE = []
        rebecca._START_APPS_CACHE_AT = 0.0
        try:
            with patch.object(rebecca.subprocess, "run", return_value=resultado) as ejecutar:
                primera = rebecca.buscar_programa_powershell("discord")
                segunda = rebecca.buscar_programa_powershell("discord")
        finally:
            rebecca._START_APPS_CACHE = cache_anterior
            rebecca._START_APPS_CACHE_AT = cache_at_anterior

        self.assertEqual(primera, ({"Name": "Discord", "AppID": "Discord.App"}, 1.0))
        self.assertEqual(segunda, primera)
        ejecutar.assert_called_once()

    def test_buscar_musica_abre_el_mejor_resultado(self):
        buscador = Mock()
        buscador.search.return_value = [
            {"title": "Otra canción", "artists": [{"name": "Otro"}], "videoId": "otro"},
            {"title": "Persiana Americana", "artists": [{"name": "Soda Stereo"}], "videoId": "soda"},
        ]
        with tempfile.TemporaryDirectory() as directorio:
            base_prueba = os.path.join(directorio, "musica.sqlite3")
            with patch.object(rebecca, "DB_LOCAL", base_prueba), patch.object(
                rebecca, "ytmusic", buscador
            ), patch.object(rebecca.webbrowser, "open") as abrir:
                respuesta = asyncio.run(
                    rebecca.ejecutar_comando_maestro(
                        rebecca.ComandoMaster(
                            accion="buscar_musica",
                            parametro="Persiana Americana Soda Stereo",
                        )
                    )
                )

        self.assertEqual(respuesta["status"], "success")
        buscador.search.assert_called_once_with(
            "Persiana Americana Soda Stereo", filter="songs", limit=5
        )
        abrir.assert_called_once_with("https://music.youtube.com/watch?v=soda")

    def test_musica_aleatoria_usa_el_perfil_aprendido(self):
        buscador = Mock()
        buscador.search.return_value = [
            {"title": "Cuando pase el temblor", "artists": [{"name": "Soda Stereo"}], "videoId": "temblor"},
            {"title": "Persiana Americana", "artists": [{"name": "Soda Stereo"}], "videoId": "soda"},
        ]
        with tempfile.TemporaryDirectory() as directorio:
            base_prueba = os.path.join(directorio, "musica.sqlite3")
            with patch.object(rebecca, "DB_LOCAL", base_prueba), patch.object(rebecca, "ytmusic", buscador), patch.object(
                rebecca.webbrowser, "open"
            ) as abrir:
                rebecca.registrar_preferencia_musical(
                    "Persiana Americana", "Soda Stereo", "soda", origen="escucha"
                )
                respuesta = rebecca.buscar_y_reproducir_musica("poneme algo de música aleatoria")

        self.assertEqual(respuesta["status"], "success")
        self.assertIn("basándome en lo que más escuchás", respuesta["message"])
        buscador.search.assert_called_once_with("Soda Stereo", filter="songs", limit=20)
        abrir.assert_called_once_with("https://music.youtube.com/watch?v=temblor")

    def test_busqueda_generica_abre_el_mejor_acceso_directo(self):
        with tempfile.TemporaryDirectory() as raiz:
            menu = os.path.join(raiz, "Microsoft", "Windows", "Start Menu", "Programs")
            os.makedirs(menu)
            acceso = os.path.join(menu, "Discord.lnk")
            with open(acceso, "wb") as archivo:
                archivo.write(b"prueba")

            entorno = {
                "ProgramData": raiz,
                "APPDATA": os.path.join(raiz, "sin_appdata"),
                "USERPROFILE": os.path.join(raiz, "usuario"),
                "PUBLIC": os.path.join(raiz, "publico"),
            }
            with patch.dict(os.environ, entorno), patch.object(rebecca.os, "startfile") as iniciar:
                respuesta = asyncio.run(
                    rebecca.ejecutar_comando_maestro(
                        rebecca.ComandoMaster(accion="abrir_programa", parametro="abrime discord")
                    )
                )

            self.assertEqual(respuesta["status"], "success")
            iniciar.assert_called_once_with(acceso)

    def test_pdf_telegram_solo_acepta_chat_autorizado(self):
        solicitud = rebecca.ComandoMaster(
            accion="imprimir_pdf_telegram",
            parametro="file-id",
            parametro2="pedido.pdf",
            parametro3="simple",
            parametro4="otro-chat",
        )
        with patch.object(rebecca, "CHAT_ID", "chat-autorizado"), patch.object(
            rebecca, "descargar_pdf_telegram"
        ) as descargar:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(solicitud))

        self.assertEqual(respuesta["status"], "error")
        descargar.assert_not_called()

    def test_pdf_telegram_autorizado_se_envia_a_impresion(self):
        solicitud = rebecca.ComandoMaster(
            accion="imprimir_pdf_telegram",
            parametro="file-id",
            parametro2="pedido.pdf",
            parametro3="simple",
            parametro4="chat-autorizado",
        )
        with patch.object(rebecca, "CHAT_ID", "chat-autorizado"), patch.object(
            rebecca, "descargar_pdf_telegram", return_value=r"C:\temporal\pedido.pdf"
        ) as descargar, patch.object(rebecca, "enviar_pdf_a_impresora") as imprimir:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(solicitud))

        self.assertEqual(respuesta["status"], "success")
        descargar.assert_called_once_with("file-id", "pedido.pdf")
        imprimir.assert_called_once_with(r"C:\temporal\pedido.pdf")

    def test_descarga_rechaza_un_adjunto_que_no_es_pdf_real(self):
        meta = Mock()
        meta.raise_for_status.return_value = None
        meta.json.return_value = {
            "ok": True,
            "result": {"file_path": "documents/falso.pdf", "file_size": 12},
        }
        descarga = Mock()
        descarga.raise_for_status.return_value = None
        descarga.iter_content.return_value = [b"esto no es pdf"]

        with tempfile.TemporaryDirectory() as carpeta, patch.object(
            rebecca, "TELEGRAM_BOT_TOKEN", "token-de-prueba"
        ), patch.object(rebecca, "ARCHIVOS_RECIBIDOS_DIR", carpeta), patch.object(
            rebecca.requests, "get", side_effect=[meta, descarga]
        ):
            with self.assertRaisesRegex(ValueError, "contenido no es un PDF"):
                rebecca.descargar_pdf_telegram("file-id", "falso.pdf")

    def test_voz_local_cae_a_sapi_si_fish_no_responde(self):
        with patch.object(
            rebecca, "solicitar_audio_fish", side_effect=RuntimeError("sin saldo")
        ), patch.object(rebecca, "_hablar_sapi", return_value=None) as sapi:
            respuesta = rebecca.generar_y_reproducir_audio_pc("Hola Usuario")

        self.assertEqual(respuesta["status"], "success")
        self.assertEqual(respuesta["voice"], "sapi")
        sapi.assert_called_once_with("Hola Usuario")

    def test_comentario_vencido_no_consume_fish_ni_habla(self):
        with patch.object(rebecca.time, "time", return_value=200), patch.object(
            rebecca, "solicitar_audio_fish"
        ) as fish, patch.object(rebecca, "_hablar_sapi") as sapi:
            respuesta = rebecca.generar_y_reproducir_audio_pc("Ya pasó", expires_at=100)
        self.assertEqual(respuesta["status"], "skipped")
        self.assertEqual(respuesta["reason"], "expired")
        fish.assert_not_called()
        sapi.assert_not_called()

    def test_comentario_que_vence_durante_sintesis_no_se_reproduce(self):
        clock = [100]

        def fish_slow(*args, **kwargs):
            clock[0] = 130
            return b"wav simulado"

        with patch.object(rebecca.time, "time", side_effect=lambda: clock[0]), patch.object(
            rebecca, "solicitar_audio_fish", side_effect=fish_slow
        ), patch.object(rebecca, "_reproducir_wav_y_limpiar") as play, patch.object(
            rebecca, "_hablar_sapi"
        ) as sapi:
            respuesta = rebecca.generar_y_reproducir_audio_pc("Ya pasó", expires_at=118)
        self.assertEqual(respuesta["status"], "skipped")
        play.assert_not_called()
        sapi.assert_not_called()

    def test_deadline_se_verifica_al_adquirir_la_salida_de_audio(self):
        with patch.object(rebecca.time, "time", return_value=200), patch.object(
            rebecca.sf, "read"
        ) as leer, patch.object(rebecca.sd, "play") as reproducir, patch.object(
            rebecca.os.path, "exists", return_value=False
        ):
            with self.assertRaises(rebecca.AudioExpiredError):
                rebecca._reproducir_wav_y_limpiar("falso.wav", expires_at=100)
        leer.assert_not_called()
        reproducir.assert_not_called()

    def test_respaldo_sapi_tampoco_habla_si_fish_agoto_el_plazo(self):
        with patch.object(rebecca.time, "time", side_effect=[100, 140]), patch.object(
            rebecca, "solicitar_audio_fish", side_effect=RuntimeError("fish caído")
        ), patch.object(rebecca.win32com.client, "Dispatch") as voz:
            respuesta = rebecca.generar_y_reproducir_audio_pc("Ya pasó", expires_at=118)
        self.assertEqual(respuesta["status"], "skipped")
        voz.assert_not_called()

    def test_comando_voz_transmite_deadline_y_rechaza_nan(self):
        with patch.object(rebecca, "generar_y_reproducir_audio_pc", return_value={"status": "success"}) as voz:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="hablar_pc", parametro="Buen pallet", parametro3="2000000000")
            ))
            self.assertEqual(respuesta["status"], "success")
            voz.assert_called_once_with("Buen pallet", None, expires_at=2000000000.0)
            voz.reset_mock()
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(
                rebecca.ComandoMaster(accion="hablar_pc", parametro="Buen pallet", parametro3="nan")
            ))
            self.assertEqual(respuesta["status"], "error")
            voz.assert_not_called()

    def test_voz_local_informa_si_fallan_fish_y_el_respaldo(self):
        with patch.object(
            rebecca, "solicitar_audio_fish", side_effect=RuntimeError("sin saldo")
        ), patch.object(rebecca, "_hablar_sapi", return_value=RuntimeError("sin salida")):
            respuesta = rebecca.generar_y_reproducir_audio_pc("Hola Usuario")

        self.assertEqual(respuesta["status"], "error")
        self.assertIn("sin salida", respuesta["message"])

    def test_voz_local_respeta_dispositivo_de_salida(self):
        solicitud = rebecca.ComandoMaster(
            accion="hablar_pc",
            parametro="Hola Usuario",
            parametro2="9",
        )
        with patch.object(
            rebecca,
            "generar_y_reproducir_audio_pc",
            return_value={"status": "success", "voice": "fish"},
        ) as hablar:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(solicitud))

        self.assertEqual(respuesta["status"], "success")
        hablar.assert_called_once_with("Hola Usuario", 9)

    def test_salida_de_voz_valida_el_dispositivo_elegido(self):
        with patch.object(rebecca.sd, "check_output_settings") as check:
            dispositivo = rebecca.resolver_dispositivo_salida_audio(5, 44100, 1)

        self.assertEqual(dispositivo, 5)
        check.assert_called_once_with(
            device=5, channels=1, dtype="float32", samplerate=44100
        )

    def test_salida_de_voz_cae_a_la_predeterminada_si_el_indice_cambio(self):
        with patch.object(
            rebecca.sd,
            "check_output_settings",
            side_effect=[ValueError("salida obsoleta"), None],
        ) as check:
            dispositivo = rebecca.resolver_dispositivo_salida_audio(25, 48000, 1)

        self.assertIsNone(dispositivo)
        self.assertEqual(check.call_count, 2)
        self.assertEqual(check.call_args_list[1].kwargs["device"], None)

    def test_modo_juego_devuelve_el_comentario_visual(self):
        solicitud = rebecca.ComandoMaster(accion="opinar_juego", parametro="Isaac")
        with patch.object(rebecca, "analizar_pantallas_rebecca", return_value="Cuidado con ese jefe") as analizar:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(solicitud))

        self.assertEqual(respuesta, {"status": "success", "message": "Cuidado con ese jefe"})
        analizar.assert_called_once_with(para_audio=True, contexto="Isaac", modo_juego=True)

    def test_modo_juego_no_cae_al_flash_sin_cuota_y_recibe_contexto_de_dbd(self):
        cliente = Mock()
        cliente.models.generate_content.return_value = Mock(
            text="El asesino te persigue junto al generador."
        )
        captura = rebecca.Image.new("RGB", (640, 360), "black")

        with patch.object(
            rebecca,
            "capturar_monitor_activo",
            return_value=("DeadByDaylight", captura),
        ), patch.object(rebecca, "GEMINI_CLIENT", cliente), patch.object(
            rebecca, "GEMINI_VISION_FALLBACK_CLIENT", None
        ):
            respuesta = rebecca._analizar_pantallas_rebecca(
                para_audio=True,
                contexto="Juego o ventana: DeadByDaylight. Rol confirmado por Usuario: asesino.",
                modo_juego=True,
            )

        self.assertIn("asesino", respuesta)
        llamada = cliente.models.generate_content.call_args
        self.assertEqual(llamada.kwargs["model"], rebecca.GEMINI_VISION_FAST_MODEL)
        self.assertIn("El juego es Dead by Daylight", llamada.kwargs["contents"][0])
        self.assertIn("Usuario juega como ASESINO", llamada.kwargs["contents"][0])
        self.assertIn("Nunca digas que Usuario está colgado", llamada.kwargs["contents"][0])
        self.assertIn("No digas frases vagas", llamada.kwargs["contents"][0])

    def test_vision_rechaza_otra_consulta_mientras_la_anterior_sigue_viva(self):
        tomado = rebecca.VISION_ANALYSIS_LOCK.acquire(blocking=False)
        self.assertTrue(tomado)
        try:
            respuesta = rebecca.analizar_pantallas_rebecca(modo_juego=True)
        finally:
            rebecca.VISION_ANALYSIS_LOCK.release()

        self.assertIn("escena anterior", respuesta)

    def test_modo_juego_usa_una_sola_clave_por_escena_y_rota_si_falla(self):
        principal = Mock()
        principal.models.generate_content.side_effect = TimeoutError("demora")
        respaldo = Mock()
        respaldo.models.generate_content.side_effect = TimeoutError("demora")
        captura = rebecca.Image.new("RGB", (640, 360), "black")

        with patch.object(
            rebecca,
            "capturar_monitor_activo",
            return_value=("DeadByDaylight", captura),
        ), patch.object(rebecca, "GEMINI_CLIENT", principal), patch.object(
            rebecca, "GEMINI_VISION_FALLBACK_CLIENT", respaldo
        ), patch.object(rebecca, "VISION_CLIENT_CURSOR", 0):
            respuesta = rebecca._analizar_pantallas_rebecca(modo_juego=True)
            cursor_final = rebecca.VISION_CLIENT_CURSOR

        self.assertIn("Error de visión", respuesta)
        principal.models.generate_content.assert_called_once()
        respaldo.models.generate_content.assert_not_called()
        self.assertEqual(cursor_final, 1)

    def test_contexto_visual_devuelve_hechos_para_la_memoria(self):
        solicitud = rebecca.ComandoMaster(accion="observar_juego_contexto", parametro="Isaac")
        with patch.object(rebecca, "analizar_pantallas_rebecca", return_value="Entró a una sala de jefe") as analizar:
            respuesta = asyncio.run(rebecca.ejecutar_comando_maestro(solicitud))

        self.assertEqual(respuesta["message"], "Entró a una sala de jefe")
        analizar.assert_called_once_with(
            para_audio=False,
            contexto="Isaac",
            modo_juego=True,
            solo_hechos=True,
        )

    def test_importes_argentinos_no_se_convierten_en_centavos(self):
        self.assertEqual(rebecca.extraer_numero("$35.000"), 35000)
        self.assertEqual(rebecca.extraer_numero("35.000,50"), 35000.50)
        self.assertEqual(rebecca.extraer_numero("1250,75"), 1250.75)

    def test_chat_de_clientes_no_puede_usar_comandos_personales(self):
        solicitud = rebecca.ComandoMaster(accion="abrir_programa", parametro="cmd")
        with self.assertRaisesRegex(Exception, "no está habilitada para clientes"):
            asyncio.run(rebecca.ejecutar_comando_sublizen(solicitud))

    def test_flujo_cliente_exige_sena_exacta_y_comprobante(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                creado = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="confirmar_compra_cliente", parametro="Taza personalizada",
                    parametro2="Cliente Prueba", parametro3="$20.000", parametro4="5491100000000",
                )))
                pedido_id = creado["pedido_id"]
                invalida = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="registrar_sena_cliente", parametro=str(pedido_id),
                    parametro2="9000", parametro3="5491100000000",
                )))
                valida = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="registrar_sena_cliente", parametro=str(pedido_id),
                    parametro2="10000", parametro3="5491100000000",
                )))

            self.assertEqual(creado["sena_requerida"], 10000)
            self.assertEqual(invalida["status"], "invalid_deposit")
            self.assertEqual(valida["status"], "needs_payment_proof")

    def test_email_no_emite_hasta_que_el_comprobante_se_aprueba(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            base = Path(carpeta)
            entrada = base / "puente-whatsapp" / "comprobantes_pago_entrantes"
            entrada.mkdir(parents=True)
            png = entrada / "transferencia.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"prueba-segura")
            with patch.object(rebecca, "DB_LOCAL", db), patch.object(
                rebecca, "BASE_DIR", str(base)
            ), patch.object(rebecca, "COMPROBANTES_PAGO_DIR", str(base / "comprobantes_pago")):
                rebecca.init_db()
                creado = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="confirmar_compra_cliente", parametro="Remera",
                    parametro2="Cliente Correo", parametro3="30000", parametro4="5491100000001",
                )))
                email = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="guardar_email_y_enviar_comprobante", parametro=str(creado["pedido_id"]),
                    parametro2="cliente@example.com", parametro3="5491100000001",
                )))
                prueba = rebecca.registrar_comprobante_pago(
                    creado["pedido_id"], str(png), 15000, "5491100000001"
                )
                with patch.object(
                    rebecca, "emitir_y_enviar_comprobante", return_value={"status": "success", "message": "enviado"}
                ) as emitir:
                    respuesta = rebecca.revisar_comprobante_pago(creado["pedido_id"], True)

            self.assertEqual(email["status"], "awaiting_payment_verification")
            self.assertEqual(prueba["status"], "payment_proof_received")
            self.assertEqual(respuesta["status"], "success")
            emitir.assert_called_once_with(creado["pedido_id"])

    def test_comprobante_invalido_no_se_guarda(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            base = Path(carpeta)
            entrada = base / "puente-whatsapp" / "comprobantes_pago_entrantes"
            entrada.mkdir(parents=True)
            falso = entrada / "captura.png"
            falso.write_bytes(b"no-es-una-imagen")
            with patch.object(rebecca, "DB_LOCAL", db), patch.object(rebecca, "BASE_DIR", str(base)), patch.object(
                rebecca, "COMPROBANTES_PAGO_DIR", str(base / "comprobantes_pago")
            ):
                rebecca.init_db()
                creado = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="confirmar_compra_cliente", parametro="Taza", parametro2="Cliente",
                    parametro3="20000", parametro4="5491100000099",
                )))
                respuesta = rebecca.registrar_comprobante_pago(
                    creado["pedido_id"], str(falso), 10000, "5491100000099"
                )
            self.assertEqual(respuesta["status"], "error")
            self.assertIn("JPG", respuesta["message"])

    def test_seguimiento_oculta_apellido_y_usa_token(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                creado = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="confirmar_compra_cliente", parametro="Gorra",
                    parametro2="María Privada", parametro3="12000", parametro4="5491100000002",
                )))
                import sqlite3
                con = sqlite3.connect(db)
                token = con.execute("SELECT seguimiento_token FROM pedidos WHERE id=?", (creado["pedido_id"],)).fetchone()[0]
                con.close()
                datos = rebecca.datos_seguimiento_publico(token)

            self.assertGreater(len(token), 24)
            self.assertEqual(datos["cliente"], "María P.")
            self.assertEqual(datos["pedido_id"], creado["pedido_id"])
            self.assertEqual(datos["estado_etiqueta"], "Esperando la seña del 50%")
            self.assertEqual(datos["pago_etiqueta"], "Seña pendiente")
            self.assertEqual(datos["precio_total"], 12000)
            self.assertEqual(datos["saldo_pendiente"], 12000)

    def test_comprobante_se_envia_por_whatsapp_aunque_falte_email(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                creado = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="confirmar_compra_cliente", parametro="Remera",
                    parametro2="Cliente WhatsApp", parametro3="30000", parametro4="5491100000010",
                )))
                import sqlite3
                con = sqlite3.connect(db)
                con.execute(
                    "UPDATE pedidos SET sena_pagada=15000, sena_confirmada=1, estado='en_proceso' WHERE id=?",
                    (creado["pedido_id"],),
                )
                con.execute(
                    """INSERT INTO comprobantes_pago
                    (pedido_id,ruta_archivo,nombre_original,mime,sha256,tamano,estado,recibido_en,verificado_en)
                    VALUES (?,?,?,?,?,?, 'aprobado', datetime('now'), datetime('now'))""",
                    (creado["pedido_id"], "prueba.pdf", "prueba.pdf", "application/pdf", "hash-sin-email", 10),
                )
                con.commit(); con.close()
                with patch.object(
                    rebecca, "generar_recibo_pdf", return_value=str(Path(carpeta) / "comprobante.pdf")
                ), patch.object(rebecca, "enviar_correo_real") as correo, patch.object(
                    rebecca, "enviar_pdf_whatsapp", return_value=(True, "")
                ) as whatsapp:
                    respuesta = rebecca.emitir_y_enviar_comprobante(creado["pedido_id"])

            self.assertEqual(respuesta["status"], "success")
            self.assertEqual(respuesta["channels"], {"email": "sin_destino", "whatsapp": "enviado"})
            self.assertIn("WhatsApp", respuesta["message"])
            correo.assert_not_called()
            whatsapp.assert_called_once()

    def test_reintento_no_duplica_canales_ya_entregados(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                creado = asyncio.run(rebecca.ejecutar_comando_maestro(rebecca.ComandoMaster(
                    accion="confirmar_compra_cliente", parametro="Buzo",
                    parametro2="Cliente Idempotente", parametro3="40000", parametro4="5491100000003",
                )))
                import sqlite3
                con = sqlite3.connect(db)
                cliente_id = con.execute("SELECT cliente_id FROM pedidos WHERE id=?", (creado["pedido_id"],)).fetchone()[0]
                con.execute("UPDATE clientes SET email=? WHERE id=?", ("cliente@example.com", cliente_id))
                con.execute("UPDATE pedidos SET sena_pagada=20000, sena_confirmada=1, estado='en_proceso' WHERE id=?", (creado["pedido_id"],))
                con.execute(
                    """INSERT INTO comprobantes_pago
                    (pedido_id,ruta_archivo,nombre_original,mime,sha256,tamano,estado,recibido_en,verificado_en)
                    VALUES (?,?,?,?,?,?, 'aprobado', datetime('now'), datetime('now'))""",
                    (creado["pedido_id"], "prueba.pdf", "prueba.pdf", "application/pdf", "hash-prueba", 10),
                )
                con.commit(); con.close()
                with patch.object(rebecca, "generar_recibo_pdf", return_value=str(Path(carpeta) / "comprobante.pdf")), patch.object(
                    rebecca, "enviar_correo_real", return_value=True
                ) as correo, patch.object(
                    rebecca, "enviar_pdf_whatsapp", return_value=(True, "")
                ) as whatsapp:
                    primera = rebecca.emitir_y_enviar_comprobante(creado["pedido_id"])
                    segunda = rebecca.emitir_y_enviar_comprobante(creado["pedido_id"])

            self.assertEqual(primera["status"], "success")
            self.assertEqual(segunda["channels"], {"email": "ya_enviado", "whatsapp": "ya_enviado"})
            correo.assert_called_once()
            whatsapp.assert_called_once()

    def test_presupuesto_seguro_calcula_el_total_y_confirma_una_sola_vez(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                con = rebecca.sqlite3.connect(db)
                con.execute("INSERT INTO productos (nombre,precio_unitario,costo_unitario) VALUES ('Remera algodón peinado 24.1',10000,6000)")
                con.commit(); con.close()
                presupuesto = asyncio.run(rebecca.ejecutar_comando_sublizen(rebecca.ComandoMaster(
                    accion="crear_presupuesto_cliente", parametro="remera algodon 24.1", parametro2="2",
                    parametro3="Cliente Seguro", parametro4="5491100000100",
                )))
                confirmar = rebecca.ComandoMaster(
                    accion="confirmar_presupuesto_cliente", parametro=str(presupuesto["presupuesto_id"]),
                    parametro2="5491100000100",
                )
                primero = asyncio.run(rebecca.ejecutar_comando_sublizen(confirmar))
                segundo = asyncio.run(rebecca.ejecutar_comando_sublizen(confirmar))
                con = rebecca.sqlite3.connect(db)
                pedidos = con.execute("SELECT id,precio_total,automatizaciones_habilitadas FROM pedidos").fetchall()
                historial = con.execute("SELECT evento FROM historial_pedidos WHERE pedido_id=?", (primero["pedido_id"],)).fetchall()
                con.close()

            self.assertEqual(presupuesto["total"], 20000)
            self.assertEqual(presupuesto["sena_requerida"], 10000)
            self.assertEqual(primero["pedido_id"], segundo["pedido_id"])
            self.assertEqual(pedidos, [(primero["pedido_id"], 20000.0, 1)])
            self.assertIn(("pedido_confirmado",), historial)

    def test_pedido_diseno_y_entrega_solo_responden_al_whatsapp_del_cliente(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                con = rebecca.sqlite3.connect(db)
                con.execute("INSERT INTO productos (nombre,precio_unitario,costo_unitario) VALUES ('Taza',5000,2000)")
                con.commit(); con.close()
                presupuesto = asyncio.run(rebecca.ejecutar_comando_sublizen(rebecca.ComandoMaster(
                    accion="crear_presupuesto_cliente", parametro="Taza", parametro2="1",
                    parametro3="Cliente Diseño", parametro4="5491100000200",
                )))
                pedido = asyncio.run(rebecca.ejecutar_comando_sublizen(rebecca.ComandoMaster(
                    accion="confirmar_presupuesto_cliente", parametro=str(presupuesto["presupuesto_id"]), parametro2="5491100000200",
                )))
                ajeno = asyncio.run(rebecca.ejecutar_comando_sublizen(rebecca.ComandoMaster(
                    accion="consultar_pedido_cliente", parametro=str(pedido["pedido_id"]), parametro2="5491199999999",
                )))
                diseno = asyncio.run(rebecca.ejecutar_comando_sublizen(rebecca.ComandoMaster(
                    accion="responder_diseno_cliente", parametro=str(pedido["pedido_id"]), parametro2="aprobar",
                    parametro3="Está perfecto", parametro4="5491100000200",
                )))
                entrega = asyncio.run(rebecca.ejecutar_comando_sublizen(rebecca.ComandoMaster(
                    accion="configurar_entrega_cliente", parametro=str(pedido["pedido_id"]), parametro2="envío",
                    parametro3="Calle 123 | viernes de 15 a 18", parametro4="5491100000200",
                )))
                con = rebecca.sqlite3.connect(db)
                guardado = con.execute("SELECT diseno_estado,modalidad_entrega,direccion_entrega,turno_entrega FROM pedidos WHERE id=?", (pedido["pedido_id"],)).fetchone()
                eventos = con.execute("SELECT evento FROM historial_pedidos WHERE pedido_id=?", (pedido["pedido_id"],)).fetchall()
                con.close()

            self.assertEqual(ajeno["status"], "error")
            self.assertEqual(diseno["status"], "design_approved")
            self.assertEqual(entrega["status"], "delivery_configured")
            self.assertEqual(guardado, ("aprobado", "envio", "Calle 123", "viernes de 15 a 18"))
            self.assertIn(("diseno_aprobado",), eventos)
            self.assertIn(("entrega_configurada",), eventos)

    def test_atencion_humana_silencia_respuestas_hasta_devolverla_a_rebecca(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            with patch.object(rebecca, "DB_LOCAL", db):
                rebecca.init_db()
                inicial = asyncio.run(rebecca.estado_atencion_sublizen(rebecca.EstadoAtencionModel(numero="5491100000300", nombre="Cliente Humano")))
                asyncio.run(rebecca.cambiar_atencion_dashboard(rebecca.CambiarAtencionModel(cliente_id=inicial["cliente_id"], modo_humano=True)))
                pausado = asyncio.run(rebecca.estado_atencion_sublizen(rebecca.EstadoAtencionModel(numero="5491100000300", nombre="Cliente Humano")))
                asyncio.run(rebecca.cambiar_atencion_dashboard(rebecca.CambiarAtencionModel(cliente_id=inicial["cliente_id"], modo_humano=False)))
                automatico = asyncio.run(rebecca.estado_atencion_sublizen(rebecca.EstadoAtencionModel(numero="5491100000300", nombre="Cliente Humano")))

            self.assertTrue(inicial["responder_automaticamente"])
            self.assertFalse(pausado["responder_automaticamente"])
            self.assertTrue(automatico["responder_automaticamente"])

    def test_notificaciones_de_estado_son_idempotentes(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            with patch.object(rebecca, "DB_LOCAL", db), patch.object(rebecca, "enviar_texto_whatsapp", return_value=(True, "")) as enviar:
                rebecca.init_db()
                cliente = rebecca.obtener_o_crear_cliente("Cliente Avisos")
                con = rebecca.sqlite3.connect(db)
                con.execute("UPDATE clientes SET whatsapp='5491100000400' WHERE id=?", (cliente,))
                pedido = con.execute("INSERT INTO pedidos (producto,cliente_id,estado) VALUES ('Remera',?,'listo')", (cliente,)).lastrowid
                con.commit(); con.close()
                primera = rebecca.notificar_evento_pedido(pedido, "estado_listo", "Tu pedido está listo")
                segunda = rebecca.notificar_evento_pedido(pedido, "estado_listo", "Tu pedido está listo")

            self.assertEqual(primera["status"], "enviado")
            self.assertEqual(segunda["status"], "ya_procesada")
            enviar.assert_called_once()

    def test_panel_envia_muestra_y_espera_aprobacion_del_diseno(self):
        with tempfile.TemporaryDirectory() as carpeta:
            db = str(Path(carpeta) / "ventas.db")
            respuesta_puente = Mock(status_code=200, text="ok")
            with patch.object(rebecca, "DB_LOCAL", db), patch.object(rebecca, "DISENOS_CLIENTES_DIR", str(Path(carpeta) / "disenos")), patch.object(rebecca.requests, "post", return_value=respuesta_puente) as enviar:
                rebecca.init_db()
                cliente = rebecca.obtener_o_crear_cliente("Cliente Muestra")
                con = rebecca.sqlite3.connect(db)
                con.execute("UPDATE clientes SET whatsapp='5491100000500' WHERE id=?", (cliente,))
                pedido = con.execute("INSERT INTO pedidos (producto,cliente_id,estado) VALUES ('Taza',?,'diseno_pendiente')", (cliente,)).lastrowid
                con.commit(); con.close()
                png = b"\x89PNG\r\n\x1a\n" + b"muestra-segura"
                resultado = asyncio.run(rebecca.enviar_diseno_dashboard(rebecca.EnviarDisenoModel(
                    pedido_id=pedido, nombre="muestra.png", mime="image/png",
                    contenido_base64=base64.b64encode(png).decode("ascii"),
                )))
                con = rebecca.sqlite3.connect(db)
                guardado = con.execute("SELECT diseno_estado,diseno_referencia FROM pedidos WHERE id=?", (pedido,)).fetchone()
                evento = con.execute("SELECT evento FROM historial_pedidos WHERE pedido_id=? ORDER BY id DESC LIMIT 1", (pedido,)).fetchone()
                con.close()

            self.assertEqual(resultado["status"], "success")
            self.assertEqual(guardado[0], "enviado")
            self.assertTrue(Path(guardado[1]).is_file())
            self.assertEqual(evento, ("diseno_enviado",))
            self.assertTrue(enviar.call_args.args[0].endswith("/enviar_archivo"))


if __name__ == "__main__":
    unittest.main()
