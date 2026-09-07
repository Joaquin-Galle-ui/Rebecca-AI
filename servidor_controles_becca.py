from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import os
import sys
import webbrowser
import pyautogui
import time
import requests
import urllib.parse
import psutil
import sqlite3
import dateparser
from datetime import datetime
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
import difflib
import threading
import base64
import io
import mss
import sounddevice as sd
import soundfile as sf
import random
from PIL import Image
from rebecca_companion.capture import ScreenCaptureError, capture_active_monitor, capture_monitor
from rebecca_companion.core_contract import CORE_VERSION
from rebecca_companion.football_watch import FootballWatch, FootballUnavailable, TERMINAL_STATUSES
import edge_tts
import asyncio
import re
import unicodedata
from google import genai
from google.genai import types as genai_types
import json
from fastapi.middleware.cors import CORSMiddleware
from fpdf import FPDF
from datetime import datetime
import subprocess
import shutil
import hashlib
from datetime import timedelta
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
from ytmusicapi import YTMusic
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import subprocess
import qrcode
import secrets
from contextlib import asynccontextmanager
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from html import escape, unescape
from email.utils import formataddr

try:
    import winsound
except ImportError:
    winsound = None

for stream in (sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# Cargar variables de entorno
load_dotenv()

# --- CONFIGURACIÓN DE APIS Y TOKENS ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY_BOCA = os.getenv("GEMINI_API_KEY_BOCA")
FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY")
REBECCA_MODEL_ID = os.getenv("REBECCA_MODEL_ID")
GEMINI_HTTP_TIMEOUT_MS = 12000
GEMINI_CLIENT = (
    genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=genai_types.HttpOptions(
            timeout=GEMINI_HTTP_TIMEOUT_MS,
            retryOptions=genai_types.HttpRetryOptions(attempts=1),
        ),
    )
    if GEMINI_API_KEY
    else None
)
GEMINI_VISION_FALLBACK_CLIENT = (
    genai.Client(
        api_key=GEMINI_API_KEY_BOCA,
        http_options=genai_types.HttpOptions(
            timeout=GEMINI_HTTP_TIMEOUT_MS,
            retryOptions=genai_types.HttpRetryOptions(attempts=1),
        ),
    )
    if GEMINI_API_KEY_BOCA
    else None
)
GEMINI_VISION_MODEL = "gemini-3.5-flash"
GEMINI_VISION_FAST_MODEL = "gemini-3.5-flash-lite"
GAME_VISION_TIMEOUT_SECONDS = 15.0
VISION_ANALYSIS_LOCK = threading.Lock()
VISION_CLIENT_CURSOR = 0
YTM_DESKTOP_URL = "http://127.0.0.1:9863/query"
EMAIL_USUARIO = os.getenv("EMAIL_USUARIO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "SubliZen")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://127.0.0.1:8000",
).rstrip("/")
WHATSAPP_BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3000").rstrip("/")
DASHBOARD_ACCESS_TOKEN = os.getenv("DASHBOARD_ACCESS_TOKEN", "").strip()


# --- ESTADO GLOBAL ---
BECCA_OJO_ACTIVO = False
MODO_NO_MOLESTAR = False
ULTIMA_INTERACCION = time.time()
VOZ_REBECCA = "es-AR-ElenaNeural"
ytmusic = None
try:
    ytmusic = YTMusic()
except Exception as e:
    print(f"[WARNING] No se pudo inicializar YTMusic: {e}")

DB_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordatorios.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVOS_RECIBIDOS_DIR = os.path.join(BASE_DIR, "archivos_recibidos")
MAX_PDF_TELEGRAM_BYTES = int(os.getenv("MAX_PDF_TELEGRAM_MB", "20")) * 1024 * 1024
AUDIO_COMPANION_DIR = os.path.join(BASE_DIR, "rebecca_companion", "audio_temporal")
COMPROBANTES_PAGO_DIR = os.path.join(BASE_DIR, "comprobantes_pago")
DISENOS_CLIENTES_DIR = os.path.join(BASE_DIR, "disenos_clientes")
MAX_COMPROBANTE_BYTES = int(os.getenv("MAX_COMPROBANTE_MB", "12")) * 1024 * 1024
AUDIO_PC_LOCK = threading.Lock()
PROGRAM_CACHE_TTL_SECONDS = 300.0
PROGRAM_LOOKUP_TIMEOUT_SECONDS = 8
_START_APPS_CACHE = []
_START_APPS_CACHE_AT = 0.0
_START_APPS_CACHE_LOCK = threading.Lock()
_SHORTCUTS_CACHE = {}
_SHORTCUTS_CACHE_LOCK = threading.Lock()


def normalizar_nombre_programa(nombre):
    """Limpia la forma coloquial en la que Usuario pide abrir una aplicación."""
    texto = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"^(?:abri|abre|abrime|inicia|iniciar|ejecuta|ejecutar|lanza|lanzar)\s+", "", texto)
    texto = re.sub(r"^(?:el|la|los|las|programa|aplicacion|app)\s+", "", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" .-_\"")
    aliases = {
        "bloc de notas": "notepad",
        "notas": "notepad",
        "calculadora de windows": "calculadora",
        "explorador de archivos": "explorador",
        "archivos": "explorador",
        "visual studio code": "visual studio code",
        "vscode": "visual studio code",
        "whats app": "whatsapp",
    }
    return aliases.get(texto, texto)


def puntuar_programa(consulta, candidato):
    consulta = normalizar_nombre_programa(consulta)
    candidato = normalizar_nombre_programa(candidato)
    if not consulta or not candidato:
        return 0.0
    if consulta == candidato:
        return 1.0
    if consulta in candidato:
        return 0.94
    if candidato in consulta:
        return 0.88
    return difflib.SequenceMatcher(None, consulta, candidato).ratio()


def _obtener_apps_inicio_windows():
    """Carga Get-StartApps una vez por intervalo para no pagar varios segundos por comando."""
    global _START_APPS_CACHE, _START_APPS_CACHE_AT
    ahora = time.monotonic()
    with _START_APPS_CACHE_LOCK:
        if _START_APPS_CACHE_AT and ahora - _START_APPS_CACHE_AT < PROGRAM_CACHE_TTL_SECONDS:
            return list(_START_APPS_CACHE)

        apps = []
        try:
            resultado = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Get-StartApps | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                timeout=PROGRAM_LOOKUP_TIMEOUT_SECONDS,
            )
            salida = resultado.stdout.strip()
            if resultado.returncode == 0 and salida:
                apps = json.loads(salida)
                if isinstance(apps, dict):
                    apps = [apps]
                elif not isinstance(apps, list):
                    apps = []
            elif resultado.returncode != 0:
                detalle = resultado.stderr.strip() or f"código {resultado.returncode}"
                print(f"[ABRIR PROGRAMA] Get-StartApps falló: {detalle}")
        except Exception as e:
            print(f"[ABRIR PROGRAMA] Error PowerShell: {e}")

        # También cacheamos un fallo: repetir un PowerShell roto en cada pedido
        # hacía que cada comando desconocido demorara hasta 15 segundos.
        _START_APPS_CACHE = apps
        _START_APPS_CACHE_AT = time.monotonic()
        return list(apps)


def buscar_programa_powershell(nombre_buscado):
    """Busca aplicaciones UWP y clásicas conocidas por el menú Inicio."""
    apps = _obtener_apps_inicio_windows()
    nombre_normalizado = normalizar_nombre_programa(nombre_buscado)
    mejor, mejor_score = None, 0.0
    for app in apps:
        app_nombre = app.get("Name", "")
        if not app_nombre:
            continue
        score = puntuar_programa(nombre_normalizado, app_nombre)
        if score > mejor_score:
            mejor_score, mejor = score, app

    return (mejor, mejor_score) if mejor and mejor_score >= 0.55 else (None, 0)


def _listar_accesos_programas(carpetas):
    """Indexa accesos directos por cinco minutos para acelerar aperturas repetidas."""
    clave = tuple(os.path.normcase(os.path.abspath(carpeta)) for carpeta in carpetas if carpeta)
    ahora = time.monotonic()
    with _SHORTCUTS_CACHE_LOCK:
        cache = _SHORTCUTS_CACHE.get(clave)
        if cache and ahora - cache[0] < PROGRAM_CACHE_TTL_SECONDS:
            return list(cache[1])

    accesos = []
    for carpeta in carpetas:
        if not carpeta or not os.path.exists(carpeta):
            continue
        for root, _, files in os.walk(carpeta):
            for file in files:
                if file.lower().endswith((".lnk", ".url")):
                    accesos.append((os.path.join(root, file), os.path.splitext(file)[0]))

    with _SHORTCUTS_CACHE_LOCK:
        _SHORTCUTS_CACHE[clave] = (time.monotonic(), accesos)
        # Evita acumular claves de carpetas temporales usadas por pruebas o sesiones viejas.
        for cache_key, value in list(_SHORTCUTS_CACHE.items()):
            if time.monotonic() - value[0] >= PROGRAM_CACHE_TTL_SECONDS:
                _SHORTCUTS_CACHE.pop(cache_key, None)
    return list(accesos)
 
 
def lanzar_por_appid(app_id):
    """Lanza cualquier app (UWP o clásica) por su AppID, vía el truco de shell:AppsFolder."""
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])


def abrir_programa_local(parametro):
    """Resuelve y abre un programa sin bloquear el loop principal de FastAPI."""
    consulta = normalizar_nombre_programa(parametro)
    if not consulta:
        return {"status": "error", "message": "Decime qué programa querés abrir."}

    if consulta == "steam":
        os.startfile("steam://open/main")
        return {"status": "success", "message": "Steam abierto."}
    if "photoshop" in consulta:
        for version in ["2026", "2025", "2024"]:
            ruta_photoshop = rf"C:\Program Files\Adobe\Adobe Photoshop {version}\Photoshop.exe"
            if os.path.exists(ruta_photoshop):
                os.startfile(ruta_photoshop)
                return {"status": "success", "message": "Photoshop abierto."}
    if consulta in ["navegador", "internet", "google chrome", "chrome"]:
        webbrowser.open("https://google.com")
        return {"status": "success", "message": "Navegador abierto."}

    comandos_windows = {
        "notepad": "notepad.exe",
        "calculadora": "calc.exe",
        "explorador": "explorer.exe",
        "paint": "mspaint.exe",
        "administrador de tareas": "taskmgr.exe",
        "panel de control": "control.exe",
        "terminal": "wt.exe",
    }
    ejecutable = comandos_windows.get(consulta)
    if ejecutable:
        try:
            subprocess.Popen([ejecutable])
            return {"status": "success", "message": f"Abriendo {parametro}."}
        except OSError:
            pass

    carpetas = [
        os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
        os.path.join(os.environ.get("PUBLIC", ""), "Desktop"),
    ]
    candidatos = []
    for ruta, nombre in _listar_accesos_programas(carpetas):
        score = puntuar_programa(consulta, nombre)
        if score >= 0.55:
            candidatos.append((score, ruta, nombre))

    if candidatos:
        candidatos.sort(key=lambda item: item[0], reverse=True)
        mejor_score, mejor_ruta, mejor_nombre = candidatos[0]
        os.startfile(mejor_ruta)
        if mejor_score < 0.85:
            return {
                "status": "success",
                "message": f"No encontré '{parametro}' exacto; abrí lo más parecido: '{mejor_nombre}'.",
            }
        return {"status": "success", "message": f"Abriendo {mejor_nombre}."}

    app_ps, score_ps = buscar_programa_powershell(consulta)
    if app_ps:
        lanzar_por_appid(app_ps["AppID"])
        if score_ps < 0.85:
            return {
                "status": "success",
                "message": f"No encontré '{parametro}' exacto; abrí lo más parecido: '{app_ps['Name']}'.",
            }
        return {"status": "success", "message": f"Abriendo {app_ps['Name']}."}

    ejecutable_path = shutil.which(consulta)
    if ejecutable_path:
        subprocess.Popen([ejecutable_path])
        return {"status": "success", "message": f"Abriendo {parametro}."}

    try:
        os.startfile(parametro)
        return {"status": "success", "message": f"Intenté abrir '{parametro}' directamente."}
    except Exception:
        return {
            "status": "error",
            "message": f"No encontré '{parametro}'. Probá con el nombre que aparece en el menú Inicio.",
        }


def _normalizar_clave_musical(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def _es_pedido_musical_generico(parametro):
    normal = _normalizar_clave_musical(parametro)
    if not normal:
        return True
    palabras_concretas = [palabra for palabra in normal.split() if palabra not in {
        "algo", "aleatorio", "aleatoria", "azar", "buena", "buen", "cancion", "canciones",
        "cualquier", "cosa", "guste", "gustos", "musica", "musical", "playlist", "pone",
        "poneme", "quieras", "random", "recomendada", "recomendame", "sorpresa", "tema", "temas",
        "de", "del", "el", "la", "me", "por", "que", "un", "una",
    }]
    return not palabras_concretas or normal in {
        "musica", "algo de musica", "musica aleatoria", "musica al azar", "algo que me guste",
        "pone algo", "poneme algo", "cualquier cosa", "sorprendeme", "una playlist",
    }


def _crear_tabla_preferencias_musicales(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS preferencias_musicales (
            clave TEXT PRIMARY KEY,
            consulta TEXT NOT NULL DEFAULT '',
            titulo TEXT NOT NULL,
            artista TEXT NOT NULL DEFAULT '',
            video_id TEXT NOT NULL DEFAULT '',
            reproducciones INTEGER NOT NULL DEFAULT 1,
            origen TEXT NOT NULL DEFAULT 'pedido',
            ultima_reproduccion TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_preferencias_musica_uso "
        "ON preferencias_musicales (reproducciones DESC, ultima_reproduccion DESC)"
    )


def registrar_preferencia_musical(titulo, artista="", video_id="", consulta="", origen="pedido"):
    titulo_limpio = " ".join(str(titulo or "").split())[:240]
    artista_limpio = " ".join(str(artista or "").split())[:180]
    if not titulo_limpio:
        return False
    clave = _normalizar_clave_musical(f"{titulo_limpio}|{artista_limpio}")
    if not clave:
        return False
    conn = sqlite3.connect(DB_LOCAL, timeout=5)
    try:
        _crear_tabla_preferencias_musicales(conn)
        conn.execute(
            """
            INSERT INTO preferencias_musicales(
                clave,consulta,titulo,artista,video_id,reproducciones,origen,ultima_reproduccion
            ) VALUES(?,?,?,?,?,1,?,?)
            ON CONFLICT(clave) DO UPDATE SET
                consulta=CASE WHEN excluded.consulta<>'' THEN excluded.consulta ELSE preferencias_musicales.consulta END,
                video_id=CASE WHEN excluded.video_id<>'' THEN excluded.video_id ELSE preferencias_musicales.video_id END,
                reproducciones=preferencias_musicales.reproducciones+1,
                origen=excluded.origen,
                ultima_reproduccion=excluded.ultima_reproduccion
            """,
            (
                clave, str(consulta or "")[:300], titulo_limpio, artista_limpio,
                str(video_id or "")[:80], str(origen or "pedido")[:40],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def obtener_preferencias_musicales(limite=30):
    conn = sqlite3.connect(DB_LOCAL, timeout=5)
    try:
        _crear_tabla_preferencias_musicales(conn)
        filas = conn.execute(
            """
            SELECT titulo,artista,video_id,reproducciones,ultima_reproduccion
            FROM preferencias_musicales
            ORDER BY reproducciones DESC, ultima_reproduccion DESC
            LIMIT ?
            """,
            (max(1, min(int(limite), 100)),),
        ).fetchall()
        conn.commit()
    finally:
        conn.close()
    return [
        {
            "titulo": fila[0], "artista": fila[1], "video_id": fila[2],
            "reproducciones": int(fila[3] or 1), "ultima_reproduccion": fila[4],
        }
        for fila in filas
    ]


def resumen_preferencias_musicales():
    preferencias = obtener_preferencias_musicales(8)
    if not preferencias:
        return {"status": "success", "message": "Todavía no aprendí tus gustos. Dejá música sonando o pedime artistas y canciones; voy armando el perfil local sola."}
    lineas = []
    for preferencia in preferencias:
        nombre = preferencia["titulo"]
        if preferencia["artista"]:
            nombre += f" — {preferencia['artista']}"
        lineas.append(f"• {nombre} ({preferencia['reproducciones']} veces)")
    return {"status": "success", "message": "Lo que más aprendí de tus gustos hasta ahora:\n" + "\n".join(lineas)}


def _artistas_resultado(resultado):
    return " ".join(
        str(artista.get("name", ""))
        for artista in (resultado.get("artists") or [])
        if isinstance(artista, dict)
    ).strip()


def _elegir_musica_personalizada():
    preferencias = obtener_preferencias_musicales(30)
    if not preferencias:
        return None, "Todavía no conozco lo suficiente tus gustos. Pedime algunas canciones, artistas o estilos y Rebecca los va a aprender; también aprende de lo que dejás sonar."
    pesos = [max(1, min(preferencia["reproducciones"], 12)) for preferencia in preferencias]
    semilla = random.choices(preferencias, weights=pesos, k=1)[0]
    consulta = semilla["artista"] or semilla["titulo"]
    try:
        candidatos = ytmusic.search(consulta, filter="songs", limit=20)
    except Exception as e:
        return None, f"Falló la recomendación musical: {str(e)}"
    candidatos = [item for item in candidatos if item.get("videoId")]
    if not candidatos:
        return None, "No encontré canciones para armar una recomendación con tus gustos."
    artista_semilla = _normalizar_clave_musical(semilla["artista"])
    if artista_semilla:
        afines = [
            item for item in candidatos
            if artista_semilla in _normalizar_clave_musical(_artistas_resultado(item))
            or _normalizar_clave_musical(_artistas_resultado(item)) in artista_semilla
        ]
        if afines:
            candidatos = afines
    recientes = {preferencia["video_id"] for preferencia in preferencias[:8] if preferencia["video_id"]}
    nuevos = [item for item in candidatos if item.get("videoId") not in recientes]
    elegido = random.choice((nuevos or candidatos)[:10])
    return elegido, ""


def buscar_y_reproducir_musica(parametro):
    """Busca una canción o elige una recomendación aprendida localmente."""
    if ytmusic is None:
        return {"status": "error", "message": "El buscador de YouTube Music no pudo iniciarse."}
    personalizada = _es_pedido_musical_generico(parametro)
    if personalizada:
        mejor, error = _elegir_musica_personalizada()
        if not mejor:
            return {"status": "error", "message": error}
    else:
        try:
            resultados = ytmusic.search(parametro, filter="songs", limit=5)
        except Exception as e:
            return {"status": "error", "message": f"Falló la búsqueda: {str(e)}"}
        if not resultados:
            return {"status": "error", "message": f"No encontré nada para '{parametro}'."}
        query_lower = parametro.lower()
        mejor, mejor_score = None, 0.0
        for resultado in resultados:
            texto = f"{resultado.get('title', '')} {_artistas_resultado(resultado)}".lower()
            score = difflib.SequenceMatcher(None, query_lower, texto).ratio()
            if score > mejor_score:
                mejor_score, mejor = score, resultado

    if not mejor or not mejor.get("videoId"):
        return {"status": "error", "message": f"No encontré una coincidencia clara para '{parametro}'."}
    titulo = mejor.get("title", "desconocido")
    artista = _artistas_resultado(mejor)
    registrar_preferencia_musical(
        titulo, artista, mejor.get("videoId", ""),
        consulta=str(parametro or ""), origen="recomendacion" if personalizada else "pedido",
    )
    webbrowser.open(f"https://music.youtube.com/watch?v={mejor['videoId']}")
    if personalizada:
        detalle = f" de {artista}" if artista else ""
        return {"status": "success", "message": f"Elegí '{titulo}'{detalle} basándome en lo que más escuchás."}
    return {"status": "success", "message": f"Abrí '{titulo}' en el navegador, debería arrancar sola."}


def capturar_y_enviar_telegram(parametro):
    """Captura la pantalla solicitada y la envía sin ocupar el loop de FastAPI."""
    try:
        carpeta_img = os.path.join(BASE_DIR, "imagenes")
        os.makedirs(carpeta_img, exist_ok=True)
        ruta_captura = os.path.join(carpeta_img, "captura_actual.jpg")
        p_lower = parametro.lower()
        ambas = "ambas" in p_lower or "todas" in p_lower
        if "2" in p_lower or "secundaria" in p_lower:
            numero_monitor = 2
            texto_caption = "la pantalla secundaria"
        elif "1" in p_lower or "principal" in p_lower:
            numero_monitor = 1
            texto_caption = "la pantalla principal"
        else:
            numero_monitor = 1
            texto_caption = "la pantalla principal"
        if ambas:
            texto_caption = "ambas pantallas"

        captura = capture_monitor(
            numero_monitor,
            combined=ambas,
            max_size=(2560, 1440) if ambas else None,
        )
        captura.image.save(ruta_captura, format="JPEG", quality=85)

        url_tel = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(ruta_captura, "rb") as foto:
            respuesta = requests.post(
                url_tel,
                data={
                    "chat_id": CHAT_ID,
                    "caption": f"📸 Captura de {texto_caption} ({captura.backend})",
                },
                files={"photo": foto},
                timeout=15,
            )

        if respuesta.status_code == 200:
            return {"status": "success", "message": f"Captura de {texto_caption} enviada."}
        return {"status": "error", "message": f"Error Telegram: {respuesta.text}"}
    except Exception as e:
        return {"status": "error", "message": f"Error interno al capturar: {str(e)}"}


def capturar_pantalla_local(parametro):
    """Guarda una captura local sin usar Telegram ni ningún modelo."""
    try:
        p_lower = str(parametro or "").lower()
        ambas = "ambas" in p_lower or "todas" in p_lower
        numero_monitor = 2 if "2" in p_lower or "secundaria" in p_lower else 1
        captura = capture_monitor(
            numero_monitor,
            combined=ambas,
            max_size=(2560, 1440) if ambas else None,
        )
        carpeta = os.path.join(BASE_DIR, "imagenes", "capturas_locales")
        os.makedirs(carpeta, exist_ok=True)
        nombre = datetime.now().strftime("captura_%Y%m%d_%H%M%S.jpg")
        ruta = os.path.join(carpeta, nombre)
        captura.image.save(ruta, format="JPEG", quality=88)
        return {
            "status": "success",
            "message": f"{captura.title} guardada localmente ({captura.backend}).",
            "path": ruta,
        }
    except Exception as exc:
        return {"status": "error", "message": f"No pude capturar la pantalla: {exc}"}


def limpiar_pdfs_recibidos(dias=7):
    """Evita que los adjuntos impresos se acumulen para siempre."""
    if not os.path.isdir(ARCHIVOS_RECIBIDOS_DIR):
        return
    limite = time.time() - (dias * 86400)
    for nombre in os.listdir(ARCHIVOS_RECIBIDOS_DIR):
        ruta = os.path.join(ARCHIVOS_RECIBIDOS_DIR, nombre)
        try:
            if os.path.isfile(ruta) and os.path.getmtime(ruta) < limite:
                os.remove(ruta)
        except OSError:
            pass


def descargar_pdf_telegram(file_id, nombre_original):
    """Descarga un PDF de Telegram validando extensión, tamaño y firma real."""
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Falta configurar TELEGRAM_BOT_TOKEN.")
    if not file_id:
        raise ValueError("Telegram no entregó el identificador del PDF.")

    nombre_seguro = os.path.basename(str(nombre_original or "documento.pdf")).strip()
    nombre_seguro = re.sub(r"[^\w.() -]+", "_", nombre_seguro, flags=re.UNICODE)
    if not nombre_seguro.lower().endswith(".pdf"):
        raise ValueError("El archivo recibido no tiene extensión PDF.")

    meta = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile",
        params={"file_id": file_id},
        timeout=15,
    )
    meta.raise_for_status()
    datos_meta = meta.json()
    if not datos_meta.get("ok"):
        raise ValueError(datos_meta.get("description", "Telegram rechazó el archivo."))

    resultado = datos_meta.get("result", {})
    tamano_declarado = int(resultado.get("file_size") or 0)
    if tamano_declarado > MAX_PDF_TELEGRAM_BYTES:
        raise ValueError(f"El PDF supera el límite de {MAX_PDF_TELEGRAM_BYTES // (1024 * 1024)} MB.")

    os.makedirs(ARCHIVOS_RECIBIDOS_DIR, exist_ok=True)
    limpiar_pdfs_recibidos()
    nombre_destino = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{nombre_seguro}"
    ruta_destino = os.path.join(ARCHIVOS_RECIBIDOS_DIR, nombre_destino)
    ruta_temporal = ruta_destino + ".part"

    try:
        descarga = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{resultado['file_path']}",
            stream=True,
            timeout=45,
        )
        descarga.raise_for_status()
        total = 0
        with open(ruta_temporal, "wb") as archivo:
            for bloque in descarga.iter_content(chunk_size=1024 * 256):
                if not bloque:
                    continue
                total += len(bloque)
                if total > MAX_PDF_TELEGRAM_BYTES:
                    raise ValueError(f"El PDF supera el límite de {MAX_PDF_TELEGRAM_BYTES // (1024 * 1024)} MB.")
                archivo.write(bloque)

        with open(ruta_temporal, "rb") as archivo:
            if archivo.read(5) != b"%PDF-":
                raise ValueError("El adjunto dice ser PDF, pero su contenido no es un PDF válido.")
        os.replace(ruta_temporal, ruta_destino)
        return ruta_destino
    except Exception:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)
        raise


def enviar_pdf_a_impresora(ruta_pdf):
    if not os.path.isfile(ruta_pdf):
        raise FileNotFoundError("El PDF ya no existe en la computadora.")
    os.startfile(ruta_pdf, "print")

def init_db():
    conn = sqlite3.connect(DB_LOCAL)
    conn.execute("CREATE TABLE IF NOT EXISTS recordatorios (id INTEGER PRIMARY KEY AUTOINCREMENT, mensaje TEXT NOT NULL, fecha_hora TEXT NOT NULL, enviado INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS recordatorios_recurrentes (id INTEGER PRIMARY KEY AUTOINCREMENT, mensaje TEXT NOT NULL, dia_semana INTEGER NOT NULL, hora TEXT NOT NULL, ultimo_envio TEXT DEFAULT '')")
    conn.execute("CREATE TABLE IF NOT EXISTS clientes (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE, telefono TEXT DEFAULT '', notas TEXT DEFAULT '')")
    conn.execute("CREATE TABLE IF NOT EXISTS pedidos (id INTEGER PRIMARY KEY AUTOINCREMENT, producto TEXT NOT NULL, detalles TEXT DEFAULT '', estado TEXT DEFAULT 'pendiente')")
    conn.execute("CREATE TABLE IF NOT EXISTS control_ejecuciones (tarea TEXT PRIMARY KEY, ultima_fecha TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS productos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE, precio_unitario REAL NOT NULL, costo_unitario REAL DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS insumos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE, cantidad REAL NOT NULL DEFAULT 0, unidad TEXT DEFAULT 'unidades', alerta_minima REAL DEFAULT 5)")
    _crear_tabla_preferencias_musicales(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS turnos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            motivo TEXT DEFAULT '',
            fecha_hora TEXT NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            alerta_enviada INTEGER DEFAULT 0
        )
    """)
    columnas_nuevas = {
        "cliente_id": "INTEGER", "tareas_pendientes": "TEXT DEFAULT ''", "fecha_creacion": "TEXT DEFAULT ''",
        "fecha_entrega": "TEXT DEFAULT ''", "alerta_enviada": "INTEGER DEFAULT 0",
        "precio_total": "REAL DEFAULT 0", "sena_pagada": "REAL DEFAULT 0",
        "confirmado": "INTEGER DEFAULT 0", "sena_confirmada": "INTEGER DEFAULT 0",
        "seguimiento_token": "TEXT DEFAULT ''", "actualizado_en": "TEXT DEFAULT ''",
        "cantidad": "REAL DEFAULT 1", "presupuesto_id": "INTEGER",
        "diseno_estado": "TEXT DEFAULT 'pendiente'", "diseno_referencia": "TEXT DEFAULT ''",
        "modalidad_entrega": "TEXT DEFAULT 'a_confirmar'", "direccion_entrega": "TEXT DEFAULT ''",
        "turno_entrega": "TEXT DEFAULT ''", "postventa_enviada": "INTEGER DEFAULT 0",
        "automatizaciones_habilitadas": "INTEGER DEFAULT 0"
    }
    for col, tipo in columnas_nuevas.items():
        try: conn.execute(f"ALTER TABLE pedidos ADD COLUMN {col} {tipo}")
        except sqlite3.OperationalError: pass

    for col, tipo in {
        "email": "TEXT DEFAULT ''", "whatsapp": "TEXT DEFAULT ''",
        "modo_humano": "INTEGER DEFAULT 0", "modo_humano_desde": "TEXT DEFAULT ''",
        "ultima_interaccion": "TEXT DEFAULT ''",
    }.items():
        try: conn.execute(f"ALTER TABLE clientes ADD COLUMN {col} {tipo}")
        except sqlite3.OperationalError: pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS envios_documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            canal TEXT NOT NULL,
            destinatario TEXT DEFAULT '',
            estado TEXT NOT NULL DEFAULT 'pendiente',
            intentos INTEGER NOT NULL DEFAULT 0,
            ultimo_error TEXT DEFAULT '',
            enviado_en TEXT DEFAULT '',
            actualizado_en TEXT DEFAULT '',
            UNIQUE (pedido_id, tipo, canal)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comprobantes_pago (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            ruta_archivo TEXT NOT NULL,
            nombre_original TEXT DEFAULT '',
            mime TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            tamano INTEGER NOT NULL DEFAULT 0,
            monto_declarado REAL DEFAULT 0,
            remitente TEXT DEFAULT '',
            estado TEXT NOT NULL DEFAULT 'pendiente',
            recibido_en TEXT NOT NULL,
            verificado_en TEXT DEFAULT '',
            observaciones TEXT DEFAULT '',
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS presupuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            producto TEXT NOT NULL,
            cantidad REAL NOT NULL DEFAULT 1,
            precio_unitario REAL NOT NULL,
            total REAL NOT NULL,
            detalles TEXT DEFAULT '',
            estado TEXT NOT NULL DEFAULT 'pendiente',
            creado_en TEXT NOT NULL,
            vence_en TEXT NOT NULL,
            aceptado_en TEXT DEFAULT '',
            pedido_id INTEGER,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historial_pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            evento TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'sistema',
            creado_en TEXT NOT NULL,
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notificaciones_clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            evento_clave TEXT NOT NULL,
            canal TEXT NOT NULL DEFAULT 'whatsapp',
            estado TEXT NOT NULL DEFAULT 'pendiente',
            intentos INTEGER NOT NULL DEFAULT 0,
            ultimo_error TEXT DEFAULT '',
            enviado_en TEXT DEFAULT '',
            creado_en TEXT NOT NULL,
            UNIQUE (pedido_id, evento_clave, canal),
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS opiniones_clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL UNIQUE,
            puntuacion INTEGER NOT NULL,
            comentario TEXT DEFAULT '',
            creado_en TEXT NOT NULL,
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id)
        )
    """)

    # El seguimiento público usa un identificador imposible de adivinar. Los QR
    # antiguos continúan funcionando, pero todos los comprobantes nuevos usan token.
    pedidos_sin_token = conn.execute(
        "SELECT id FROM pedidos WHERE seguimiento_token IS NULL OR seguimiento_token = ''"
    ).fetchall()
    for (pedido_id,) in pedidos_sin_token:
        conn.execute(
            "UPDATE pedidos SET seguimiento_token = ?, actualizado_en = COALESCE(NULLIF(actualizado_en, ''), ?) WHERE id = ?",
            (secrets.token_urlsafe(24), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pedido_id),
        )

    # --- LA TABLA NUEVA VA ACÁ ADENTRO ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ventas_dia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto TEXT NOT NULL,
            diseno TEXT DEFAULT '',
            fecha TEXT NOT NULL,
            hora TEXT NOT NULL
        )
    """)
    
    from rebecca_companion.commerce import schema as commerce_schema
    commerce_schema(conn)

    # Correos pa mandar
    conn.execute("""
        CREATE TABLE IF NOT EXISTS correos_programados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            destinatario TEXT NOT NULL,
            asunto TEXT NOT NULL,
            cuerpo TEXT NOT NULL,
            archivo TEXT DEFAULT '',
            fecha_hora TEXT NOT NULL,
            enviado INTEGER DEFAULT 0
        )
    """)

    # Índices basados en las consultas que usa el dashboard y los bucles de alertas.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_estado_entrega ON pedidos (estado, fecha_entrega)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_cliente_estado ON pedidos (cliente_id, estado)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pedidos_seguimiento_token ON pedidos (seguimiento_token)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_envios_pedido_estado ON envios_documentos (pedido_id, estado)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_comprobantes_pedido_estado ON comprobantes_pago (pedido_id, estado, recibido_en)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recordatorios_envio_fecha ON recordatorios (enviado, fecha_hora)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_estado_fecha ON turnos (estado, fecha_hora)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_presupuestos_cliente_estado ON presupuestos (cliente_id, estado, creado_en)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_historial_pedido_fecha ON historial_pedidos (pedido_id, creado_en)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notificaciones_estado ON notificaciones_clientes (estado, creado_en)")
    conn.execute("PRAGMA optimize")
    
    # --- Y RECIÉN AHORA CERRAMOS ---
    conn.commit()
    conn.close()
    
def controlar_ytm_desktop(comando: str, valor: str = ""):
    """
    Envía comandos a la API de YTM Desktop. Si está cerrado, lo abre automáticamente.
    """
    headers = {"Content-Type": "application/json"}
    payload = {"command": comando}
    if valor:
        payload["value"] = valor

    try:
        # Intento 1: Mandar el comando normalmente
        res = requests.post(YTM_DESKTOP_URL, json=payload, headers=headers, timeout=3)
        if res.status_code == 200:
            return {"status": "success", "message": f"Comando '{comando}' enviado a YouTube Music."}
        else:
            return {"status": "error", "message": f"YTM respondió con código {res.status_code}."}
            
    except requests.exceptions.ConnectionError:
        # ¡El puerto está cerrado! Significa que la app no está abierta.
        # Ruta por defecto donde se instalan estas apps de Electron:
        ruta_ytm = os.path.join(os.environ.get("LOCALAPPDATA", "C:\\"), "Programs", "youtube-music-desktop-app", "YouTube Music Desktop App.exe")
        
        if os.path.exists(ruta_ytm):
            # La abre
            os.startfile(ruta_ytm)
            # Le da 6 segundos de changüí para que inicie la ventana y prenda el Companion Server
            time.sleep(6) 
            
            try:
                # Intento 2: Vuelve a mandar el comando ahora que está abierto
                res2 = requests.post(YTM_DESKTOP_URL, json=payload, headers=headers, timeout=3)
                if res2.status_code == 200:
                    return {"status": "success", "message": f"Tuve que abrir YouTube Music yo misma, pero ya mandé el comando '{comando}'."}
            except:
                return {"status": "error", "message": "Abrí YouTube Music, pero el servidor tardó mucho en responder. Probá mandando el comando de nuevo."}
        
        return {
            "status": "error", 
            "message": "YouTube Music está cerrado y no pude encontrar el archivo .exe para abrirlo sola."
        }
    except Exception as e:
        return {"status": "error", "message": f"Error al comunicar con YTM: {str(e)}"}


def validar_email(email):
    """Validación conservadora para evitar guardar direcciones rotas."""
    valor = str(email or "").strip().lower()
    if len(valor) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", valor):
        return ""
    return valor


def formatear_pesos(valor):
    numero = float(valor or 0)
    return f"$ {numero:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def obtener_token_seguimiento(pedido_id, conn=None):
    propia = conn is None
    conn = conn or sqlite3.connect(DB_LOCAL, timeout=10)
    row = conn.execute("SELECT seguimiento_token FROM pedidos WHERE id = ?", (int(pedido_id),)).fetchone()
    if not row:
        if propia:
            conn.close()
        return ""
    token = str(row[0] or "").strip()
    if not token:
        token = secrets.token_urlsafe(24)
        conn.execute(
            "UPDATE pedidos SET seguimiento_token = ?, actualizado_en = ? WHERE id = ?",
            (token, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(pedido_id)),
        )
        conn.commit()
    if propia:
        conn.close()
    return token


def generar_recibo_pdf(cliente, producto, monto, pedido_id):
    """Genera un comprobante comercial de seña; no suplanta una factura fiscal de ARCA."""
    try:
        monto_num = max(0.0, float(monto or 0))
    except (TypeError, ValueError):
        monto_num = 0.0

    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    datos = conn.execute(
        "SELECT precio_total, seguimiento_token FROM pedidos WHERE id = ?",
        (int(pedido_id),),
    ).fetchone()
    total = max(0.0, float(datos[0] or 0)) if datos else 0.0
    token = obtener_token_seguimiento(pedido_id, conn=conn) if datos else ""
    conn.close()
    saldo = max(0.0, total - monto_num)
    url_seguimiento = f"{PUBLIC_BASE_URL}/seguimiento/{token}" if token else f"{PUBLIC_BASE_URL}/estado/{pedido_id}"

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_fill_color(17, 24, 39)
    pdf.rect(0, 0, 210, 38, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", "B", 19)
    pdf.set_xy(14, 10)
    pdf.cell(150, 10, txt="SUBLIZEN", ln=True)
    pdf.set_font("Arial", "", 10)
    pdf.set_x(14)
    pdf.cell(150, 6, txt="COMPROBANTE DE SEÑA", ln=True)
    logo = os.path.join(BASE_DIR, "logo_sublizen.png")
    if os.path.exists(logo):
        try:
            pdf.image(logo, x=171, y=6, w=27)
        except Exception:
            pass

    pdf.set_xy(14, 48)
    pdf.set_text_color(31, 41, 55)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(42, 8, txt="Comprobante:")
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 8, txt=f"SZ-{int(pedido_id):06d}", ln=True)
    for etiqueta, valor in (
        ("Fecha:", datetime.now().strftime("%d/%m/%Y %H:%M")),
        ("Cliente:", str(cliente or "Cliente")),
        ("Producto:", str(producto or "Pedido personalizado")),
    ):
        pdf.set_x(14)
        pdf.set_font("Arial", "B", 11)
        pdf.cell(42, 8, txt=etiqueta)
        pdf.set_font("Arial", "", 11)
        pdf.cell(0, 8, txt=valor, ln=True)

    pdf.ln(5)
    pdf.set_x(14)
    pdf.set_fill_color(236, 253, 245)
    pdf.set_draw_color(16, 185, 129)
    pdf.set_text_color(6, 95, 70)
    pdf.set_font("Arial", "B", 13)
    pdf.cell(182, 13, txt=f"Seña recibida: {formatear_pesos(monto_num)}", border=1, ln=True, align="C", fill=True)
    pdf.set_x(14)
    pdf.set_font("Arial", "", 10)
    pdf.cell(91, 9, txt=f"Total del pedido: {formatear_pesos(total)}", border="LRB", align="C")
    pdf.cell(91, 9, txt=f"Saldo pendiente: {formatear_pesos(saldo)}", border="LRB", ln=True, align="C")

    tmp_pdf_dir = os.path.join(BASE_DIR, "tmp", "pdfs")
    os.makedirs(tmp_pdf_dir, exist_ok=True)
    ruta_qr = os.path.join(tmp_pdf_dir, f"seguimiento_{int(pedido_id)}.png")
    qrcode.make(url_seguimiento).save(ruta_qr)
    try:
        pdf.image(ruta_qr, x=82, y=114, w=46)
        pdf.set_xy(14, 163)
        pdf.set_text_color(55, 65, 81)
        pdf.set_font("Arial", "B", 10)
        pdf.cell(182, 6, txt="SEGUIMIENTO EN VIVO", ln=True, align="C")
        pdf.set_x(14)
        pdf.set_font("Arial", "", 9)
        pdf.multi_cell(182, 5, txt="Escanea el QR para consultar el avance. El enlace no cambia y la pagina se actualiza automaticamente.", align="C")
        pdf.ln(8)
        pdf.set_x(14)
        pdf.set_text_color(107, 114, 128)
        pdf.set_font("Arial", "I", 8)
        pdf.multi_cell(182, 5, txt="Este documento acredita la seña recibida y no reemplaza una factura fiscal electrónica autorizada por ARCA.", align="C")
    finally:
        if os.path.exists(ruta_qr):
            os.remove(ruta_qr)

    carpeta_recibos = os.path.join(BASE_DIR, "recibos")
    os.makedirs(carpeta_recibos, exist_ok=True)
    ruta_pdf = os.path.join(carpeta_recibos, f"Comprobante_Sena_Pedido_{int(pedido_id):06d}.pdf")
    pdf.output(ruta_pdf)
    return ruta_pdf
 

 
def generar_pdf_deudores():
    conn = sqlite3.connect(DB_LOCAL)
    deudores = conn.execute("""
        SELECT c.nombre, p.id, p.producto, (p.precio_total - p.sena_pagada) as deuda
        FROM pedidos p JOIN clientes c ON p.cliente_id = c.id
        WHERE p.precio_total > p.sena_pagada
        ORDER BY deuda DESC
    """).fetchall()
    conn.close()
    
    pdf = FPDF()
    pdf.add_page()
    
    # 1. ENCABEZADO
    pdf.set_fill_color(33, 37, 41)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", 'B', 18)
    pdf.cell(0, 15, txt="SUBLIZEN - REPORTE DE DEUDORES", ln=True, align='C', fill=True)
    
    try:
        pdf.image("logo_sublizen.png", x=165, y=5, w=25)
    except: pass
    pdf.ln(5)
    
    # 2. FECHA
    pdf.set_text_color(120, 120, 120)
    pdf.set_font("Arial", 'I', 10)
    pdf.cell(0, 8, txt=f"Al {datetime.now().strftime('%d/%m/%Y')}", ln=True, align='R')
    pdf.ln(2)
    
    # 3. CABECERA TABLA (Rojo Peligro)
    pdf.set_fill_color(220, 53, 69)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(140, 10, txt=" Cliente y Pedido", border=1, fill=True)
    pdf.cell(50, 10, txt=" Deuda", border=1, ln=True, align='C', fill=True)
    
    # 4. FILAS ESTILO CEBRA
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", '', 11)
    fondo_gris = False
    total = 0
    
    for nombre, pid, producto, deuda in deudores:
        if fondo_gris: 
            pdf.set_fill_color(240, 240, 240)
        else: 
            pdf.set_fill_color(255, 255, 255)
            
        pdf.cell(140, 10, txt=f"  {nombre} (#{pid} {producto})", border=1, fill=fondo_gris)
        pdf.cell(50, 10, txt=f"${deuda:,.0f}", border=1, ln=True, align='C', fill=fondo_gris)
        
        fondo_gris = not fondo_gris
        total += deuda
        
    # TOTAL FINAL
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(140, 10, txt="TOTAL ADEUDADO EN LA CALLE:", border=0, align='R')
    pdf.set_text_color(220, 53, 69)
    pdf.cell(50, 10, txt=f"${total:,.0f}", border=0, ln=True, align='C')
    
    nombre_archivo = f"Deudores_{datetime.now().strftime('%Y%m%d')}.pdf"
    pdf.output(nombre_archivo)
    return nombre_archivo
 
 
def generar_pdf_pendientes():
    conn = sqlite3.connect(DB_LOCAL)
    pedidos = conn.execute("""
        SELECT p.id, p.producto, c.nombre, p.tareas_pendientes, p.fecha_entrega
        FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id
        WHERE p.estado != 'entregado'
        ORDER BY p.id
    """).fetchall()
    conn.close()
    
    pdf = FPDF()
    pdf.add_page()
    
    pdf.set_fill_color(33, 37, 41)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", 'B', 18)
    pdf.cell(0, 15, txt="SUBLIZEN - TRABAJOS PENDIENTES", ln=True, align='C', fill=True)
    
    try: 
        pdf.image("logo_sublizen.png", x=165, y=5, w=25)
    except: pass
    pdf.ln(5)
    
    pdf.set_text_color(120, 120, 120)
    pdf.set_font("Arial", 'I', 10)
    pdf.cell(0, 8, txt=f"Al {datetime.now().strftime('%d/%m/%Y')}", ln=True, align='R')
    pdf.ln(2)
    
    # BLOQUES DE PEDIDOS (Mejor que una tabla porque las tareas son largas)
    for pid, producto, cliente, falta, fecha in pedidos:
        cliente = cliente or "Sin cliente"
        
        # Titulo del pedido (Azulito copado)
        pdf.set_fill_color(0, 120, 215)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Arial", 'B', 11)
        pdf.cell(0, 8, txt=f"  Pedido #{pid} | {producto} - {cliente}", ln=True, fill=True)
        
        # Detalles (Gris clarito)
        pdf.set_fill_color(245, 245, 245)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Arial", '', 10)
        
        detalles = ""
        if fecha: detalles += f"Entrega: {fecha}  |  "
        if falta: detalles += f"Falta: {falta}"
        if not detalles: detalles = "Sin tareas pendientes registradas."
            
        pdf.cell(0, 8, txt=f"  {detalles}", ln=True, fill=True)
        pdf.ln(4) # Espacio entre pedidos
        
    nombre_archivo = f"Pendientes_{datetime.now().strftime('%Y%m%d')}.pdf"
    pdf.output(nombre_archivo)
    return nombre_archivo
 
def generar_pdf_ventas_dia(fecha):
    from rebecca_companion.sales_pdf import generate_sales_pdf
    return generate_sales_pdf(DB_LOCAL, fecha, f"Ventas_{fecha}.pdf")



def buscar_cliente_exacto(nombre):
    conn = sqlite3.connect(DB_LOCAL)
    row = conn.execute("SELECT id FROM clientes WHERE LOWER(nombre) = LOWER(?)", (nombre,)).fetchone()
    conn.close(); return row[0] if row else None

def buscar_producto_exacto(nombre):
    conn = sqlite3.connect(DB_LOCAL)
    row = conn.execute("SELECT id FROM productos WHERE LOWER(nombre) = LOWER(?)", (nombre,)).fetchone()
    conn.close()
    return row[0] if row else None

def buscar_cliente_difuso(nombre):
    conn = sqlite3.connect(DB_LOCAL)
    todos = conn.execute("SELECT id, nombre FROM clientes").fetchall()
    conn.close()
    if not todos: return None
    nombre_lower = nombre.lower().strip()
    mejor_id, mejor_score = None, 0.0
    for cliente_id, nombre_db in todos:
        if nombre_lower == nombre_db.lower(): return cliente_id
        score = difflib.SequenceMatcher(None, nombre_lower, nombre_db.lower()).ratio()
        if score > mejor_score: mejor_score, mejor_id = score, cliente_id
    return mejor_id if mejor_score >= 0.6 else None

def obtener_o_crear_cliente(nombre):
    nombre = nombre.strip()
    if not nombre: return None
    existente = buscar_cliente_exacto(nombre)
    if existente: return existente
    conn = sqlite3.connect(DB_LOCAL)
    cursor = conn.execute("INSERT INTO clientes (nombre) VALUES (?)", (nombre,))
    conn.commit(); nuevo_id = cursor.lastrowid; conn.close()
    return nuevo_id

def buscar_producto_difuso(nombre):
    conn = sqlite3.connect(DB_LOCAL)
    todos = conn.execute("SELECT id, nombre, precio_unitario, costo_unitario FROM productos").fetchall()
    conn.close()
    if not todos: return None
    mejor, mejor_score = None, 0.0
    for pid, nombre_db, precio, costo in todos:
        if nombre.lower().strip() == nombre_db.lower(): return (pid, nombre_db, precio, costo)
        score = difflib.SequenceMatcher(None, nombre.lower().strip(), nombre_db.lower()).ratio()
        if score > mejor_score: mejor_score, mejor = score, (pid, nombre_db, precio, costo)
    return mejor if mejor_score >= 0.5 else None

def extraer_numero(texto):
    match = re.search(r'-?\d[\d.,]*', str(texto or "").replace(" ", ""))
    if not match:
        return None
    valor = match.group()
    negativo = valor.startswith("-")
    valor = valor.lstrip("-")
    if "." in valor and "," in valor:
        separador_decimal = "." if valor.rfind(".") > valor.rfind(",") else ","
        separador_miles = "," if separador_decimal == "." else "."
        valor = valor.replace(separador_miles, "").replace(separador_decimal, ".")
    elif "." in valor or "," in valor:
        separador = "." if "." in valor else ","
        partes = valor.split(separador)
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3 and len(partes[0]) >= 1):
            valor = "".join(partes)
        else:
            valor = ".".join(partes)
    try:
        numero = float(valor)
        return -numero if negativo else numero
    except ValueError:
        return None

def normalizar_accion(texto):
    """Acepta variantes como 'Crear recordatorio', 'crear-recordatorio' o 'acción: crear_recordatorio'."""
    accion = str(texto or "").strip().strip("`'\"")
    accion = re.sub(r"^acci[oó]n\s*[:=]\s*", "", accion, flags=re.IGNORECASE)
    accion = unicodedata.normalize("NFKD", accion).encode("ascii", "ignore").decode("ascii")
    accion = re.sub(r"[^a-zA-Z0-9]+", "_", accion.lower()).strip("_")
    aliases = {
        "listar_pedidos": "reporte_pedidos",
        "ver_pedidos": "reporte_pedidos",
        "actualizar_pedido": "gestion_lote_pedidos",
        "editar_pedido": "gestion_lote_pedidos",
        "completar_pedido": "marcar_entregado",
        "marcar_pedido_entregado": "marcar_entregado",
        "listar_recordatorios": "consultar_recordatorios",
        "recordame": "crear_recordatorio",
        "presupuestar": "cotizar",
    }
    return aliases.get(accion, accion)

def enviar_mensaje_telegram(mensaje):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        return {"status": "error", "message": "Falta configurar Telegram."}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    respuesta = requests.post(url, data={"chat_id": CHAT_ID, "text": mensaje}, timeout=15)
    respuesta.raise_for_status()
    return {"status": "success", "message": "Mensaje enviado."}

def enviar_pdf_telegram(ruta_pdf):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        return {"status": "error", "message": "Falta configurar Telegram."}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(ruta_pdf, "rb") as archivo:
            respuesta = requests.post(
                url,
                data={"chat_id": CHAT_ID},
                files={"document": archivo},
                timeout=30,
            )
        respuesta.raise_for_status()
        return {"status": "success", "message": "PDF enviado."}
    finally:
        if os.path.exists(ruta_pdf):
            os.remove(ruta_pdf)

try: import win32com.client; WIN32_DISPONIBLE = True
except ImportError: WIN32_DISPONIBLE = False

@asynccontextmanager
async def lifespan(_: FastAPI):
    tareas = [
        bucle_reporte_diario,
        bucle_recordatorios,
        bucle_reporte_ventas,
        bucle_alertas_entrega,
        bucle_observador,
        bucle_aburrimiento,
        bucle_nocturno,
        bucle_alertas_turnos,
        bucle_correos,
        bucle_reintento_comprobantes,
        bucle_automatizaciones_sublizen,
        bucle_aprendizaje_musical,
    ]
    for tarea in tareas:
        threading.Thread(target=tarea, daemon=True).start()
    # Precalienta el índice del menú Inicio sin demorar el arranque. Así la
    # primera orden de abrir una aplicación tampoco paga varios segundos de
    # PowerShell.
    threading.Thread(target=_obtener_apps_inicio_windows, daemon=True).start()
    yield


app = FastAPI(
    title="Project M.A.Y.A. / Rebecca Core - MASTER NODE",
    version="16.0",
    lifespan=lifespan,
)
init_db()


@app.middleware("http")
async def proteger_dashboard_publico(request: Request, call_next):
    """El dashboard es libre en esta PC; por un túnel público exige un token."""
    if request.url.path.startswith("/dashboard"):
        host = (request.url.hostname or "").lower()
        es_local = host in {"127.0.0.1", "localhost", "testserver", "::1"}
        token = request.headers.get("X-Dashboard-Token", "") or request.query_params.get("token", "")
        if not es_local and (not DASHBOARD_ACCESS_TOKEN or not secrets.compare_digest(token, DASHBOARD_ACCESS_TOKEN)):
            return JSONResponse(status_code=401, content={"detail": "Dashboard privado: acceso no autorizado."})
    if request.url.path.startswith("/ejecutar") or request.url.path.startswith("/sublizen/estado-atencion"):
        host = (request.url.hostname or "").lower()
        if host not in {"127.0.0.1", "localhost", "testserver", "::1"}:
            return JSONResponse(status_code=403, content={"detail": "Los comandos de Rebecca sólo aceptan conexiones locales."})
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000", "http://localhost:8000",
        "http://127.0.0.1:5678", "http://localhost:5678",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    
)

MESA_TRABAJO = os.getenv("MESA_TRABAJO", "")

class ComandoMaster(BaseModel):
    accion: str; parametro: str = ""; parametro2: str = ""; parametro3: str = ""; parametro4: str = ""

class EditarPedidoModel(BaseModel):
    id: int
    estado: str | None = None
    tareas_pendientes: str | None = None
    fecha_entrega: str | None = None
    precio_total: float | None = None
    sena_pagada: float | None = None
    diseno_estado: str | None = None
    modalidad_entrega: str | None = None
    direccion_entrega: str | None = None
    turno_entrega: str | None = None

class EditarProductoModel(BaseModel):
    id: int
    precio_unitario: float | None = None
    costo_unitario: float | None = None

class EliminarModel(BaseModel):
    id: int

class RevisarComprobanteModel(BaseModel):
    pedido_id: int
    comprobante_id: int | None = None
    observaciones: str = ""

class EditarRutinaModel(BaseModel):
    id: int
    dia_semana: int
    hora: str
    mensaje: str

class InsumoModel(BaseModel):
    id: int = 0
    nombre: str
    cantidad: float
    unidad: str
    alerta_minima: float


class EstadoAtencionModel(BaseModel):
    numero: str
    nombre: str = "Cliente"
    modo_humano: bool | None = None


class CambiarAtencionModel(BaseModel):
    cliente_id: int
    modo_humano: bool


class EnviarDisenoModel(BaseModel):
    pedido_id: int
    nombre: str
    mime: str
    contenido_base64: str
    mensaje: str = ""


def limpiar_texto_para_tts(texto: str) -> str:
    return re.sub(r'[\*\_\#\`\~]', '', texto).strip()


def solicitar_audio_fish(texto: str, formato: str = "mp3") -> bytes:
    if not FISH_AUDIO_API_KEY or not REBECCA_MODEL_ID:
        raise ValueError("Falta configurar Fish Audio o el modelo de voz de Rebecca.")
    texto_procesado = limpiar_texto_para_tts(texto)
    if not texto_procesado:
        raise ValueError("No hay texto para reproducir.")

    respuesta = requests.post(
        "https://api.fish.audio/v1/tts",
        headers={
            "Authorization": f"Bearer {FISH_AUDIO_API_KEY}",
            "Content-Type": "application/json",
            "model": "s2.1-pro-free",
        },
        json={
            "text": texto_procesado[:1800],
            "reference_id": REBECCA_MODEL_ID,
            "format": formato,
            "latency": "normal",
        },
        timeout=30,
    )
    if respuesta.status_code != 200:
        raise RuntimeError(f"Fish Audio respondió {respuesta.status_code}.")
    return respuesta.content


def resolver_dispositivo_salida_audio(
    dispositivo: int | None,
    sample_rate: int,
    channels: int,
) -> int | None:
    """Valida la salida elegida y cae a la predeterminada si el índice quedó obsoleto."""
    try:
        sd.check_output_settings(
            device=dispositivo,
            channels=max(1, channels),
            dtype="float32",
            samplerate=sample_rate,
        )
        return dispositivo
    except Exception as selected_error:
        if dispositivo is None:
            raise selected_error
        print(
            f"[COMPANION VOZ] La salida {dispositivo} no acepta "
            f"{sample_rate} Hz/{channels} canal(es); probando la predeterminada."
        )
        sd.check_output_settings(
            device=None,
            channels=max(1, channels),
            dtype="float32",
            samplerate=sample_rate,
        )
        return None


class AudioExpiredError(RuntimeError):
    pass


def _check_audio_deadline(expires_at: float | None) -> None:
    if expires_at is not None and time.time() >= expires_at:
        raise AudioExpiredError("El comentario venció antes de reproducirse.")


def _expired_audio_result():
    return {"status": "skipped", "reason": "expired", "message": "Omití un comentario que ya llegaba tarde."}


def _reproducir_wav_y_limpiar(ruta_audio: str, dispositivo: int | None = None, texto_respaldo: str = "", *, expires_at: float | None = None):
    playback_error = None
    try:
        with AUDIO_PC_LOCK:
            _check_audio_deadline(expires_at)
            audio, sample_rate = sf.read(ruta_audio, dtype="float32", always_2d=False)
            _check_audio_deadline(expires_at)
            sd.play(audio, sample_rate, device=dispositivo)
            estado = sd.wait()
            if estado:
                raise RuntimeError(f"PortAudio informó un problema durante la reproducción: {estado}")
    except AudioExpiredError:
        raise
    except Exception as e:
        playback_error = e
        print(f"[COMPANION VOZ] Error reproduciendo audio: {e}")
    finally:
        try:
            if os.path.exists(ruta_audio):
                os.remove(ruta_audio)
        except OSError:
            pass
    sapi_error = None
    if playback_error and texto_respaldo:
        sapi_error = _hablar_sapi(texto_respaldo, **({"expires_at": expires_at} if expires_at is not None else {}))
        if isinstance(sapi_error, AudioExpiredError):
            raise sapi_error
    return playback_error, sapi_error


def _hablar_sapi(texto: str, *, expires_at: float | None = None):
    try:
        with AUDIO_PC_LOCK:
            _check_audio_deadline(expires_at)
            if not WIN32_DISPONIBLE:
                raise RuntimeError("SAPI no está disponible.")
            voz = win32com.client.Dispatch("SAPI.SpVoice")
            voz.Rate = 1
            voz.Speak(limpiar_texto_para_tts(texto))
        return None
    except Exception as e:
        print(f"[COMPANION VOZ] Falló también la voz de respaldo: {e}")
        return e


def generar_y_reproducir_audio_pc(texto: str, dispositivo: int | None = None, *, expires_at: float | None = None):
    """Genera la voz clonada y la reproduce en la PC sin abrir un reproductor."""
    try:
        _check_audio_deadline(expires_at)
        audio = solicitar_audio_fish(texto, formato="wav")
        _check_audio_deadline(expires_at)
        os.makedirs(AUDIO_COMPANION_DIR, exist_ok=True)
        ruta_audio = os.path.join(AUDIO_COMPANION_DIR, f"rebecca_{int(time.time() * 1000)}.wav")
        with open(ruta_audio, "wb") as archivo:
            archivo.write(audio)
        info_audio = sf.info(ruta_audio)
        dispositivo = resolver_dispositivo_salida_audio(
            dispositivo,
            int(info_audio.samplerate),
            int(info_audio.channels),
        )
        # Esperar a que termine la reproducción mantiene bloqueada la escucha
        # activa. Si se liberaba antes, Rebecca podía oír su propia voz y
        # activarse/interrumpirse sin que Usuario la llamara.
        playback_error, sapi_error = _reproducir_wav_y_limpiar(
            ruta_audio,
            dispositivo,
            texto,
            expires_at=expires_at,
        )
        if playback_error:
            if sapi_error:
                return {
                    "status": "error",
                    "message": f"No pude reproducir la voz: {playback_error}; respaldo: {sapi_error}",
                }
            return {
                "status": "success",
                "message": f"Usé la voz local porque falló la salida de Fish Audio. ({playback_error})",
                "voice": "sapi",
            }
        return {
            "status": "success",
            "message": "Rebecca está hablando por la PC.",
            "voice": "fish",
            "output_device": dispositivo,
        }
    except AudioExpiredError:
        return _expired_audio_result()
    except Exception as e:
        # SAPI mantiene operativa la compañera si Fish está caído o sin saldo.
        sapi_error = _hablar_sapi(texto, **({"expires_at": expires_at} if expires_at is not None else {}))
        if isinstance(sapi_error, AudioExpiredError):
            return _expired_audio_result()
        if sapi_error:
            return {
                "status": "error",
                "message": f"No pude generar ni reproducir la voz: {e}; respaldo: {sapi_error}",
            }
        return {
            "status": "success",
            "message": f"Fish Audio no respondió; usé la voz local de respaldo. ({str(e)})",
            "voice": "sapi",
        }


async def generar_y_enviar_audio(texto: str):
    try:
        audio = solicitar_audio_fish(texto, formato="mp3")
        os.makedirs(AUDIO_COMPANION_DIR, exist_ok=True)
        audio_path = os.path.join(AUDIO_COMPANION_DIR, f"telegram_{int(time.time() * 1000)}.mp3")
        with open(audio_path, "wb") as archivo:
            archivo.write(audio)
        with open(audio_path, "rb") as voice_file:
            respuesta = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice",
                data={"chat_id": CHAT_ID},
                files={"voice": voice_file},
                timeout=15,
            )
        respuesta.raise_for_status()
        if os.path.exists(audio_path):
            os.remove(audio_path)
        return {"status": "success", "message": "Nota de voz enviada."}
    except Exception as e: return {"status": "error", "message": f"Fallo al generar voz: {str(e)}"}

def capturar_dos_monitores():
    with mss.MSS() as sct:
        monitor_completo = sct.monitors[0]
        sct_img = sct.grab(monitor_completo)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        img.thumbnail((2560, 1440))
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="JPEG", quality=80)
        return img_byte_arr.getvalue()

def obtener_ventana_activa():
    """Devuelve título y rectángulo de la ventana activa usando APIs nativas de Windows."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        longitud = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(longitud + 1)
        user32.GetWindowTextW(hwnd, buffer, longitud + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return buffer.value or "Aplicación desconocida", (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return "Aplicación desconocida", None


def capturar_monitor_activo():
    # Para comentarios en vivo no hace falta enviar una captura Full HD. Una
    # imagen más chica conserva HUD/eventos y reduce mucho la latencia de visión.
    captured = capture_active_monitor(max_size=(512, 288))
    return captured.title, captured.image


def _analizar_pantallas_rebecca(para_audio=False, contexto="", modo_juego=False, solo_hechos=False):
    global VISION_CLIENT_CURSOR
    try:
        titulo_ventana, img = capturar_monitor_activo()
        if solo_hechos:
            prompt = (
                "Describí únicamente el hecho relevante que ocurre en la captura de la ventana activa. "
                "No adoptes personalidad, no saludes y no hables como asistente: devolvé una observación factual "
                "breve que otra IA pueda usar como contexto. Si no hay nada destacable, indicá 'sin evento claro'. "
                f"Ventana activa: {titulo_ventana}. Contexto opcional: {contexto or 'sin contexto adicional'}. "
            )
        elif modo_juego:
            contexto_juego = f"{titulo_ventana} {contexto}".casefold().replace(" ", "")
            pista_juego = ""
            if "deadbydaylight" in contexto_juego:
                pista_juego = (
                    "El juego es Dead by Daylight. Prestá atención a persecución, asesino o superviviente, "
                    "generadores, ganchos, rescates, estados de salud, skill checks y colapso final. "
                )
                if "rolconfirmadoporusuario:asesino" in contexto_juego:
                    pista_juego += (
                        "Usuario juega como ASESINO. Nunca digas que Usuario está colgado, herido, perseguido o "
                        "siendo sacrificado: cualquier superviviente en gancho es su rival. Comentá desde la "
                        "perspectiva del asesino. "
                    )
                elif "rolconfirmadoporusuario:superviviente" in contexto_juego:
                    pista_juego += (
                        "Usuario juega como SUPERVIVIENTE. Comentá desde esa perspectiva y no le atribuyas las "
                        "acciones del asesino. "
                    )
            prompt = (
                "Sos Rebecca, la compañera de escritorio de Usuario. "
                f"{pista_juego}"
                "Comentá UN hecho concreto que realmente se vea en la captura y nombrá su elemento específico. "
                "No digas frases vagas como 'cuidado', 'ahí viene', 'está cerca' o 'qué situación' sin explicar "
                "qué personaje, enemigo, objeto o acción visible las justifica. No describas botones ni toda la "
                "interfaz. Si no podés identificar algo concreto, respondé exactamente 'sin evento claro'. "
                "Una frase corta, sin inventar y sin repetir comentarios anteriores. "
                f"Ventana activa: {titulo_ventana}. Contexto opcional: {contexto or 'sin contexto adicional'}. "
            )
        else:
            prompt = (
                "Sos Rebecca. Analizá esta captura del monitor donde está la ventana activa del usuario. "
                f"Ventana activa: {titulo_ventana}. Respondé en 1 o 2 oraciones. "
            )
        prompt += (
            "Podés usar (laughs) o (sighs), sin emojis ni asteriscos."
            if para_audio
            else "Sin formato complejo."
        )

        # El SDK suele codificar un PIL como PNG. En capturas de juegos eso
        # produce bastante más tráfico del necesario. JPEG a esta resolución
        # conserva lo importante para comentar la escena y baja la latencia.
        image_buffer = io.BytesIO()
        img.convert("RGB").save(image_buffer, format="JPEG", quality=58, optimize=True)
        image_part = genai_types.Part.from_bytes(
            data=image_buffer.getvalue(),
            mime_type="image/jpeg",
        )

        # Las mediciones reales muestran que la clave principal responde en
        # pocos segundos, mientras la secundaria a veces consume todo el
        # deadline. La secundaria queda como respaldo: un 429 de la principal
        # falla rápido y todavía permite continuar con la otra.
        vision_clients = [
            (nombre, client)
            for nombre, client in (
                ("principal", GEMINI_CLIENT),
                ("vision", GEMINI_VISION_FALLBACK_CLIENT),
            )
            if client is not None
        ]
        if not vision_clients:
            raise RuntimeError("Falta GEMINI_API_KEY para analizar la pantalla.")
        selected_client_index = None
        if modo_juego and len(vision_clients) > 1:
            # Una sola clave por escena evita sumar dos deadlines. Si falla,
            # la próxima observación rota a la otra clave automáticamente.
            selected_client_index = VISION_CLIENT_CURSOR % len(vision_clients)
            vision_clients = [vision_clients[selected_client_index]]
        last_error = None
        vision_models = (
            (GEMINI_VISION_FAST_MODEL,)
            if modo_juego
            else (GEMINI_VISION_MODEL,)
        )
        max_output_tokens = 96 if modo_juego else 180
        for model_name in vision_models:
            for client_name, vision_client in vision_clients:
                try:
                    # Flash-Lite acepta imágenes, pero actualmente rechaza la
                    # combinación mediaResolution + thinkingBudget usada por
                    # Flash. Para ambos alcanza con acotar la salida.
                    response = vision_client.models.generate_content(
                        model=model_name,
                        contents=[prompt, image_part],
                        config=genai_types.GenerateContentConfig(
                            maxOutputTokens=max_output_tokens,
                        ),
                    )
                    text = str(response.text or "").strip()
                    if text:
                        return text
                    last_error = RuntimeError("Gemini devolvió una respuesta visual vacía.")
                except Exception as exc:
                    last_error = exc
                    print(
                        f"[VISION] Modelo {model_name}, cliente {client_name} falló: "
                        f"{type(exc).__name__}: {str(exc)[:220]}"
                    )
        if selected_client_index is not None:
            VISION_CLIENT_CURSOR = (selected_client_index + 1) % 2
        raise last_error or RuntimeError("No pude analizar la captura.")

    except ScreenCaptureError as e:
        return f"Error de captura: {str(e)}"
    except Exception as e:
        return f"Error de visión: {str(e)}"


def analizar_pantallas_rebecca(para_audio=False, contexto="", modo_juego=False, solo_hechos=False):
    # asyncio.wait_for no puede cancelar el hilo que ya está consultando a
    # Gemini. Sin este candado, cada timeout dejaba una consulta huérfana y la
    # siguiente escena abría otra, agotando cuota y empeorando la latencia.
    if not VISION_ANALYSIS_LOCK.acquire(blocking=False):
        return "Error de visión: ya estoy terminando de analizar la escena anterior."
    try:
        return _analizar_pantallas_rebecca(
            para_audio=para_audio,
            contexto=contexto,
            modo_juego=modo_juego,
            solo_hechos=solo_hechos,
        )
    finally:
        VISION_ANALYSIS_LOCK.release()
    
def bucle_observador():
    global BECCA_OJO_ACTIVO
    while True:
        if BECCA_OJO_ACTIVO and not MODO_NO_MOLESTAR:
            time.sleep(random.randint(180, 420))
            if BECCA_OJO_ACTIVO and not MODO_NO_MOLESTAR:
                tipo_envio = random.choice(["audio", "texto"])
                comentario = analizar_pantallas_rebecca(para_audio=(tipo_envio == "audio"))
                if not comentario.startswith("Error"):
                    try:
                        if tipo_envio == "audio": asyncio.run(generar_y_enviar_audio(comentario))
                        else: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": f"👁️ {comentario}"}, timeout=10)
                    except: pass
        else: time.sleep(10)

def bucle_reporte_ventas():
    while True:
        time.sleep(300)  # cada 5 min
        try:
            ahora = datetime.now()
            if ahora.hour < 21:
                continue
            hoy = ahora.strftime("%Y-%m-%d")
 
            conn = sqlite3.connect(DB_LOCAL)
            ya_hecho = conn.execute("SELECT ultima_fecha FROM control_ejecuciones WHERE tarea = 'reporte_ventas'").fetchone()
            if ya_hecho == (hoy,):
                conn.close()
                continue
 
            cantidad = conn.execute("SELECT COUNT(*) FROM ventas_dia WHERE fecha = ? AND COALESCE(anulada,0)=0", (hoy,)).fetchone()[0]
            conn.execute("INSERT OR REPLACE INTO control_ejecuciones (tarea, ultima_fecha) VALUES ('reporte_ventas', ?)", (hoy,))
            conn.commit()
            conn.close()
 
            if cantidad == 0:
                continue  # no vendiste nada hoy, no manda nada
 
            archivo = generar_pdf_ventas_dia(hoy)
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
            with open(archivo, "rb") as f:
                requests.post(url, data={"chat_id": CHAT_ID, "caption": f"📦 Ventas de hoy: {cantidad} unidad(es)"}, files={"document": f}, timeout=15)
            os.remove(archivo)
        except Exception as e:
            print(f"[REPORTE VENTAS] Error: {e}")

def bucle_recordatorios():
    while True:
        try:
            conn = sqlite3.connect(DB_LOCAL)
            ahora_dt = datetime.now()
            pendientes = conn.execute("SELECT id, mensaje FROM recordatorios WHERE enviado = 0 AND fecha_hora <= ?", (ahora_dt.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
            for id_rec, mensaje in pendientes:
                if requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": f"🔔 RECORDATORIO:\n{mensaje}"}, timeout=10).status_code == 200:
                    conn.execute("UPDATE recordatorios SET enviado = 1 WHERE id = ?", (id_rec,)); conn.commit()

            recurrentes = conn.execute("SELECT id, mensaje FROM recordatorios_recurrentes WHERE dia_semana = ? AND hora = ? AND ultimo_envio != ?", (ahora_dt.weekday(), ahora_dt.strftime("%H:%M"), ahora_dt.strftime("%Y-%m-%d"))).fetchall()
            for id_rec, mensaje in recurrentes:
                if requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": f"📚 RUTINA SEMANAL:\n{mensaje}"}, timeout=10).status_code == 200:
                    conn.execute("UPDATE recordatorios_recurrentes SET ultimo_envio = ? WHERE id = ?", (ahora_dt.strftime("%Y-%m-%d"), id_rec)); conn.commit()
            conn.close()
        except: pass
        time.sleep(30)

def enviar_correo_real(destinatario, asunto, cuerpo, ruta_archivo=""):
    destinatario = validar_email(destinatario)
    if not destinatario:
        print("❌ [MAIL] Dirección de correo inválida")
        return False
    if not EMAIL_USUARIO or not EMAIL_PASSWORD:
        print("❌ [MAIL] Faltan credenciales en el .env")
        return False
    try:
        msg = MIMEMultipart()
        msg['From'] = formataddr((EMAIL_FROM_NAME, EMAIL_USUARIO))
        msg['To'] = destinatario
        msg['Subject'] = asunto
        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

        # Si le pasamos un archivo y existe, lo adjunta
        if ruta_archivo and os.path.exists(ruta_archivo):
            with open(ruta_archivo, "rb") as adjunto:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(adjunto.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f"attachment; filename= {os.path.basename(ruta_archivo)}")
            msg.attach(part)

        # Conexión a Gmail
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_USUARIO, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USUARIO, destinatario, msg.as_string())
        return True
    except Exception as e:
        print(f"❌ [MAIL ERROR]: {e}")
        return False


def enviar_pdf_whatsapp(numero, ruta_pdf, caption):
    numero = str(numero or "").strip()
    if not numero:
        return False, "Falta el número de WhatsApp."
    try:
        respuesta = requests.post(
            f"{WHATSAPP_BRIDGE_URL}/enviar_pdf",
            json={"numero": numero, "ruta_pdf": ruta_pdf, "caption": caption},
            timeout=20,
        )
        if respuesta.status_code == 200:
            return True, ""
        return False, f"Puente WhatsApp respondió {respuesta.status_code}: {respuesta.text[:300]}"
    except requests.RequestException as exc:
        return False, str(exc)


def enviar_texto_whatsapp(numero, texto, timeout=15):
    """Envía texto por el puente y devuelve (éxito, error) sin inventar entregas."""
    numero = str(numero or "").strip()
    texto = str(texto or "").strip()
    if not numero or not texto:
        return False, "Falta número o mensaje."
    try:
        respuesta = requests.post(
            f"{WHATSAPP_BRIDGE_URL}/enviar",
            json={"numero": numero, "texto": texto},
            timeout=timeout,
        )
        if respuesta.status_code == 200:
            return True, ""
        return False, f"Puente WhatsApp respondió {respuesta.status_code}: {respuesta.text[:300]}"
    except requests.RequestException as exc:
        return False, str(exc)


def normalizar_whatsapp(numero):
    return re.sub(r"\D", "", str(numero or ""))


def whatsapp_coincide(esperado, recibido):
    esperado = normalizar_whatsapp(esperado)
    recibido = normalizar_whatsapp(recibido)
    return bool(esperado and recibido and esperado[-10:] == recibido[-10:])


def registrar_historial_pedido(conn, pedido_id, evento, descripcion, actor="sistema"):
    conn.execute(
        "INSERT INTO historial_pedidos (pedido_id, evento, descripcion, actor, creado_en) VALUES (?, ?, ?, ?, ?)",
        (
            int(pedido_id), str(evento or "actualizacion")[:80], str(descripcion or "")[:800],
            str(actor or "sistema")[:80], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )


def pedido_pertenece_a_whatsapp(conn, pedido_id, whatsapp):
    fila = conn.execute(
        """
        SELECT p.id, p.producto, p.estado, p.precio_total, p.sena_pagada,
               p.diseno_estado, p.fecha_entrega, p.modalidad_entrega,
               COALESCE(NULLIF(c.whatsapp, ''), c.telefono, '') AS whatsapp,
               c.nombre, p.seguimiento_token
        FROM pedidos p LEFT JOIN clientes c ON c.id=p.cliente_id WHERE p.id=?
        """,
        (int(pedido_id),),
    ).fetchone()
    if not fila or not whatsapp_coincide(fila[8], whatsapp):
        return None
    return fila


def notificar_evento_pedido(pedido_id, evento_clave, texto):
    """Notificación idempotente: un evento nunca se duplica aunque n8n reintente."""
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    fila = conn.execute(
        """
        SELECT COALESCE(NULLIF(c.whatsapp, ''), c.telefono, ''), p.estado
        FROM pedidos p LEFT JOIN clientes c ON c.id=p.cliente_id WHERE p.id=?
        """,
        (int(pedido_id),),
    ).fetchone()
    if not fila or not str(fila[0] or "").strip():
        conn.close()
        return {"status": "sin_destino", "message": "El pedido no tiene WhatsApp."}
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute(
            """
            INSERT INTO notificaciones_clientes
                (pedido_id, evento_clave, canal, estado, intentos, creado_en)
            VALUES (?, ?, 'whatsapp', 'pendiente', 0, ?)
            """,
            (int(pedido_id), str(evento_clave)[:120], ahora),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        previa = conn.execute(
            "SELECT estado FROM notificaciones_clientes WHERE pedido_id=? AND evento_clave=? AND canal='whatsapp'",
            (int(pedido_id), str(evento_clave)[:120]),
        ).fetchone()
        conn.close()
        return {"status": "ya_procesada", "estado": previa[0] if previa else "desconocido"}
    numero = fila[0]
    conn.close()

    ok, error = enviar_texto_whatsapp(numero, texto)
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    conn.execute(
        """
        UPDATE notificaciones_clientes
        SET estado=?, intentos=intentos+1, ultimo_error=?, enviado_en=?
        WHERE pedido_id=? AND evento_clave=? AND canal='whatsapp'
        """,
        ("enviado" if ok else "error", error[:500], ahora if ok else "", int(pedido_id), str(evento_clave)[:120]),
    )
    conn.commit()
    conn.close()
    return {"status": "enviado" if ok else "error", "message": error}


def registrar_resultado_envio(pedido_id, canal, destinatario, exitoso, error=""):
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    conn.execute(
        """
        INSERT INTO envios_documentos
            (pedido_id, tipo, canal, destinatario, estado, intentos, ultimo_error, enviado_en, actualizado_en)
        VALUES (?, 'comprobante_sena', ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(pedido_id, tipo, canal) DO UPDATE SET
            destinatario = excluded.destinatario,
            estado = excluded.estado,
            intentos = envios_documentos.intentos + 1,
            ultimo_error = excluded.ultimo_error,
            enviado_en = CASE WHEN excluded.estado = 'enviado' THEN excluded.enviado_en ELSE envios_documentos.enviado_en END,
            actualizado_en = excluded.actualizado_en
        """,
        (
            int(pedido_id), canal, destinatario,
            "enviado" if exitoso else "error", str(error or "")[:500],
            ahora if exitoso else "", ahora,
        ),
    )
    conn.commit()
    conn.close()


def canal_documento_enviado(pedido_id, canal):
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    row = conn.execute(
        "SELECT estado FROM envios_documentos WHERE pedido_id = ? AND tipo = 'comprobante_sena' AND canal = ?",
        (int(pedido_id), canal),
    ).fetchone()
    conn.close()
    return bool(row and row[0] == "enviado")


def detectar_tipo_comprobante(ruta):
    with open(ruta, "rb") as archivo:
        cabecera = archivo.read(16)
    if cabecera.startswith(b"%PDF-"):
        return "application/pdf", ".pdf"
    if cabecera.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if cabecera.startswith(b"RIFF") and cabecera[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ValueError("El comprobante debe ser una imagen JPG, PNG, WEBP o un PDF válido.")


def ruta_comprobante_permitida(ruta):
    ruta_real = os.path.realpath(str(ruta or ""))
    raices = [
        os.path.realpath(os.path.join(BASE_DIR, "puente-whatsapp", "comprobantes_pago_entrantes")),
        os.path.realpath(COMPROBANTES_PAGO_DIR),
    ]
    try:
        return ruta_real if any(os.path.commonpath([ruta_real, raiz]) == raiz for raiz in raices) else ""
    except ValueError:
        return ""


def comprobante_aprobado(pedido_id, conn=None):
    propia = conn is None
    if propia:
        conn = sqlite3.connect(DB_LOCAL, timeout=10)
    fila = conn.execute(
        "SELECT id FROM comprobantes_pago WHERE pedido_id = ? AND estado = 'aprobado' ORDER BY id DESC LIMIT 1",
        (int(pedido_id),),
    ).fetchone()
    if propia:
        conn.close()
    return int(fila[0]) if fila else None


def registrar_comprobante_pago(pedido_id, ruta_origen, monto_declarado=0, remitente="", nombre_original=""):
    ruta_origen = ruta_comprobante_permitida(ruta_origen)
    if not ruta_origen or not os.path.isfile(ruta_origen):
        return {"status": "error", "message": "No encontré un comprobante válido recibido por WhatsApp."}
    tamano = os.path.getsize(ruta_origen)
    if tamano <= 0 or tamano > MAX_COMPROBANTE_BYTES:
        return {"status": "error", "message": "El comprobante está vacío o supera el límite permitido."}
    try:
        mime, extension = detectar_tipo_comprobante(ruta_origen)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    with open(ruta_origen, "rb") as archivo:
        sha256 = hashlib.sha256(archivo.read()).hexdigest()

    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    conn.row_factory = sqlite3.Row
    pedido = conn.execute(
        """
        SELECT p.id, p.precio_total, p.estado, p.confirmado,
               COALESCE(NULLIF(c.whatsapp, ''), c.telefono, '') AS whatsapp
        FROM pedidos p LEFT JOIN clientes c ON c.id = p.cliente_id WHERE p.id = ?
        """,
        (int(pedido_id),),
    ).fetchone()
    if not pedido:
        conn.close()
        return {"status": "error", "message": f"No encontré el pedido #{int(pedido_id)}."}
    if pedido["estado"] == "cancelado" or not pedido["confirmado"]:
        conn.close()
        return {"status": "error", "message": "El pedido no está confirmado o fue cancelado."}
    esperado = re.sub(r"\D", "", str(pedido["whatsapp"] or ""))
    recibido = re.sub(r"\D", "", str(remitente or ""))
    if esperado and recibido and esperado[-10:] != recibido[-10:]:
        conn.close()
        return {"status": "error", "message": "El comprobante llegó desde otro WhatsApp y no se asoció al pedido."}
    duplicado = conn.execute(
        "SELECT id, pedido_id, estado FROM comprobantes_pago WHERE sha256 = ?", (sha256,)
    ).fetchone()
    if duplicado:
        conn.close()
        if int(duplicado["pedido_id"]) != int(pedido_id):
            return {"status": "error", "message": "Ese mismo comprobante ya fue usado en otro pedido."}
        return {
            "status": "payment_proof_received", "pedido_id": int(pedido_id),
            "comprobante_id": int(duplicado["id"]), "estado_verificacion": duplicado["estado"],
            "message": "El comprobante ya estaba guardado. El pago sigue sujeto a verificación.",
        }

    carpeta = os.path.join(COMPROBANTES_PAGO_DIR, f"pedido_{int(pedido_id)}")
    os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, f"{sha256[:20]}{extension}")
    if os.path.realpath(ruta_origen) != os.path.realpath(destino):
        shutil.copy2(ruta_origen, destino)
    requerida = round(float(pedido["precio_total"] or 0) * 0.5, 2)
    monto = float(monto_declarado or 0)
    estado = "monto_inconsistente" if monto > 0 and abs(monto - requerida) > 0.01 else "pendiente"
    observacion = ""
    if estado == "monto_inconsistente":
        observacion = f"Monto declarado ${monto:,.2f}; seña esperada ${requerida:,.2f}."
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """
        INSERT INTO comprobantes_pago
            (pedido_id, ruta_archivo, nombre_original, mime, sha256, tamano,
             monto_declarado, remitente, estado, recibido_en, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(pedido_id), destino, str(nombre_original or os.path.basename(ruta_origen))[:180],
         mime, sha256, tamano, monto, str(remitente or "")[:80], estado, ahora, observacion),
    )
    conn.execute(
        "UPDATE pedidos SET tareas_pendientes = ?, actualizado_en = ? WHERE id = ?",
        ("Comprobante recibido; pago pendiente de verificación", ahora, int(pedido_id)),
    )
    registrar_historial_pedido(
        conn, int(pedido_id), "comprobante_recibido",
        "El cliente envió un comprobante; el pago quedó pendiente de verificación.", "cliente",
    )
    conn.commit()
    comprobante_id = cursor.lastrowid
    conn.close()
    return {
        "status": "payment_proof_received", "pedido_id": int(pedido_id),
        "comprobante_id": comprobante_id, "estado_verificacion": estado,
        "sena_requerida": requerida,
        "message": "Recibí y guardé el comprobante. El pago todavía debe verificarse antes de emitir el recibo.",
    }


def revisar_comprobante_pago(pedido_id, aprobar, comprobante_id=None, observaciones=""):
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    conn.row_factory = sqlite3.Row
    parametros = [int(pedido_id)]
    filtro = "pedido_id = ? AND estado IN ('pendiente', 'monto_inconsistente')"
    if comprobante_id is not None:
        filtro += " AND id = ?"
        parametros.append(int(comprobante_id))
    comprobante = conn.execute(
        f"SELECT * FROM comprobantes_pago WHERE {filtro} ORDER BY id DESC LIMIT 1", parametros
    ).fetchone()
    pedido = conn.execute("SELECT precio_total FROM pedidos WHERE id = ?", (int(pedido_id),)).fetchone()
    if not pedido:
        conn.close()
        return {"status": "error", "message": f"No existe el pedido #{int(pedido_id)}."}
    if not comprobante:
        conn.close()
        return {"status": "error", "message": "No hay un comprobante pendiente para revisar."}
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not aprobar:
        motivo_rechazo = str(observaciones or "No se pudo verificar la transferencia")[:500]
        conn.execute(
            "UPDATE comprobantes_pago SET estado='rechazado', verificado_en=?, observaciones=? WHERE id=?",
            (ahora, motivo_rechazo, comprobante["id"]),
        )
        conn.execute(
            "UPDATE pedidos SET tareas_pendientes='Comprobante rechazado; esperando uno nuevo', actualizado_en=? WHERE id=?",
            (ahora, int(pedido_id)),
        )
        registrar_historial_pedido(conn, int(pedido_id), "comprobante_rechazado", motivo_rechazo, "operador")
        conn.commit()
        conn.close()
        notificacion = notificar_evento_pedido(
            int(pedido_id), f"comprobante_rechazado_{int(comprobante['id'])}",
            f"No pudimos verificar el comprobante del pedido #{int(pedido_id)}. Motivo: {motivo_rechazo}. Por favor, revisalo y enviá una nueva imagen o PDF.",
        )
        return {"status": "rejected", "pedido_id": int(pedido_id), "notificacion": notificacion["status"], "message": "Comprobante rechazado; se informó el motivo y Rebecca esperará uno nuevo."}

    requerida = round(float(pedido[0] or 0) * 0.5, 2)
    conn.execute(
        "UPDATE comprobantes_pago SET estado='aprobado', verificado_en=?, observaciones=? WHERE id=?",
        (ahora, str(observaciones or "Pago verificado manualmente")[:500], comprobante["id"]),
    )
    conn.execute(
        "UPDATE comprobantes_pago SET estado='rechazado', verificado_en=?, observaciones='Reemplazado por otro comprobante aprobado' WHERE pedido_id=? AND id!=? AND estado IN ('pendiente','monto_inconsistente')",
        (ahora, int(pedido_id), comprobante["id"]),
    )
    conn.execute(
        """
        UPDATE pedidos SET sena_pagada=?, sena_confirmada=1, confirmado=1,
            estado='en_proceso', tareas_pendientes='Pedido confirmado; producción pendiente', actualizado_en=?
        WHERE id=?
        """,
        (requerida, ahora, int(pedido_id)),
    )
    registrar_historial_pedido(conn, int(pedido_id), "sena_acreditada", f"Se acreditó una seña de ${requerida:,.2f}.", "operador")
    conn.commit()
    conn.close()
    resultado = emitir_y_enviar_comprobante(int(pedido_id))
    notificar_evento_pedido(
        int(pedido_id), "sena_acreditada",
        f"¡Se acreditó la seña del pedido #{int(pedido_id)}! Ya quedó confirmado. Te enviamos el comprobante y podés seguir el avance desde su QR.",
    )
    return resultado


def emitir_y_enviar_comprobante(pedido_id):
    """Entrega idempotente: nunca repite un canal que ya quedó confirmado."""
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    row = conn.execute(
        """
        SELECT c.nombre, c.email, COALESCE(NULLIF(c.whatsapp, ''), c.telefono),
               p.producto, p.precio_total, p.sena_pagada, p.sena_confirmada,
               p.seguimiento_token
        FROM pedidos p LEFT JOIN clientes c ON c.id = p.cliente_id
        WHERE p.id = ?
        """,
        (int(pedido_id),),
    ).fetchone()
    conn.close()
    if not row:
        return {"status": "error", "message": f"No encontré el pedido #{pedido_id}."}

    cliente, email, whatsapp, producto, total, sena, sena_confirmada, token = row
    email = validar_email(email)
    whatsapp = str(whatsapp or "").strip()
    if not comprobante_aprobado(pedido_id):
        return {"status": "awaiting_payment_verification", "message": "Todavía no hay un comprobante de transferencia aprobado; no emití el recibo."}
    if not sena_confirmada or float(sena or 0) <= 0:
        return {"status": "error", "message": "La seña todavía no está confirmada; no emití el comprobante."}
    if not email and not whatsapp:
        return {"status": "error", "message": "El pedido no tiene correo ni WhatsApp para entregar el comprobante."}

    ruta_pdf = generar_recibo_pdf(cliente or "Cliente", producto, sena, int(pedido_id))
    tracking_url = f"{PUBLIC_BASE_URL}/seguimiento/{token or obtener_token_seguimiento(pedido_id)}"
    resultados = {}

    if not email:
        resultados["email"] = "sin_destino"
    elif canal_documento_enviado(pedido_id, "email"):
        resultados["email"] = "ya_enviado"
    else:
        asunto = f"SubliZen - comprobante de seña del pedido #{int(pedido_id)}"
        cuerpo = (
            f"Hola {cliente or 'cliente'},\n\n"
            f"Recibimos la seña de ${float(sena):,.2f} correspondiente al pedido #{int(pedido_id)} ({producto}).\n"
            f"Total: ${float(total or 0):,.2f}\n"
            f"Saldo pendiente: ${max(0.0, float(total or 0) - float(sena or 0)):,.2f}\n\n"
            f"Podés seguir el avance en vivo desde este enlace:\n{tracking_url}\n\n"
            "Adjuntamos el comprobante de seña.\n\nSubliZen"
        )
        ok = enviar_correo_real(email, asunto, cuerpo, ruta_pdf)
        registrar_resultado_envio(pedido_id, "email", email, ok, "No se pudo entregar por SMTP" if not ok else "")
        resultados["email"] = "enviado" if ok else "error"

    if not whatsapp:
        resultados["whatsapp"] = "sin_destino"
    elif canal_documento_enviado(pedido_id, "whatsapp"):
        resultados["whatsapp"] = "ya_enviado"
    else:
        caption = (
            f"Comprobante de seña del pedido #{int(pedido_id)}. "
            f"Seguimiento en vivo: {tracking_url}"
        )
        ok, error = enviar_pdf_whatsapp(whatsapp, ruta_pdf, caption)
        registrar_resultado_envio(pedido_id, "whatsapp", whatsapp, ok, error)
        resultados["whatsapp"] = "enviado" if ok else "error"

    fallidos = [canal for canal, estado in resultados.items() if estado == "error"]
    entregados = [canal for canal, estado in resultados.items() if estado in {"enviado", "ya_enviado"}]
    sin_destino = [canal for canal, estado in resultados.items() if estado == "sin_destino"]
    if not fallidos and entregados:
        nombres_canales = {"email": "correo", "whatsapp": "WhatsApp"}
        entregados_texto = ", ".join(nombres_canales.get(canal, canal) for canal in entregados)
        detalle = ""
        if sin_destino == ["email"]:
            detalle = " Falta el correo para enviar también una copia por email."
        elif sin_destino == ["whatsapp"]:
            detalle = " Falta el WhatsApp para enviar también una copia por ese canal."
        return {
            "status": "success", "pedido_id": int(pedido_id), "tracking_url": tracking_url,
            "message": f"Comprobante del pedido #{int(pedido_id)} entregado por {entregados_texto}.{detalle}",
            "channels": resultados,
        }
    enviados = entregados
    return {
        "status": "partial" if enviados else "error", "pedido_id": int(pedido_id),
        "tracking_url": tracking_url, "channels": resultados,
        "message": f"El comprobante se procesó, pero falló: {', '.join(fallidos)}. Rebecca puede reintentar sin duplicar lo ya enviado.",
    }

def bucle_correos():
    while True:
        time.sleep(60) # Revisa el reloj cada 1 minuto
        try:
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_LOCAL)
            pendientes = conn.execute("SELECT id, destinatario, asunto, cuerpo, archivo FROM correos_programados WHERE enviado = 0 AND fecha_hora <= ?", (ahora,)).fetchall()
            
            for pid, dest, asun, cuerp, arch in pendientes:
                print(f"📧 [MAIL] Disparando correo programado a {dest}...")
                exito = enviar_correo_real(dest, asun, cuerp, arch)
                if exito:
                    conn.execute("UPDATE correos_programados SET enviado = 1 WHERE id = ?", (pid,))
                    # Me avisa por Telegram que ya lo mandó
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": f"📧 ¡Correo enviado a {dest} exitosamente!"}, timeout=10)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[BUCLE CORREOS ERROR]: {e}")


def bucle_reintento_comprobantes():
    """Reintenta únicamente canales fallidos; los enviados nunca se duplican."""
    while True:
        time.sleep(300)
        try:
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            pedidos = conn.execute(
                """
                SELECT DISTINCT e.pedido_id
                FROM envios_documentos e
                JOIN pedidos p ON p.id=e.pedido_id
                JOIN comprobantes_pago cp ON cp.pedido_id=p.id AND cp.estado='aprobado'
                WHERE e.tipo='comprobante_sena' AND e.estado='error' AND e.intentos < 5
                  AND p.estado != 'cancelado'
                """
            ).fetchall()
            conn.close()
            for (pedido_id,) in pedidos:
                emitir_y_enviar_comprobante(int(pedido_id))
        except Exception as exc:
            print(f"[REINTENTO COMPROBANTES ERROR]: {exc}")


def bucle_automatizaciones_sublizen():
    """Recordatorios moderados para pedidos nuevos; cada evento se envía una sola vez."""
    while True:
        time.sleep(300)
        try:
            limite = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            pedidos = conn.execute(
                """
                SELECT p.id, p.estado, p.diseno_estado, p.sena_confirmada, p.actualizado_en,
                       p.fecha_creacion, p.postventa_enviada,
                       COALESCE((SELECT cp.estado FROM comprobantes_pago cp WHERE cp.pedido_id=p.id ORDER BY cp.id DESC LIMIT 1),'sin_comprobante')
                FROM pedidos p
                WHERE p.automatizaciones_habilitadas=1 AND p.estado!='cancelado'
                """
            ).fetchall()
            conn.close()
            for pedido_id, estado, diseno, sena_ok, actualizado, creado, postventa, comprobante in pedidos:
                referencia = actualizado or creado or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if referencia > limite:
                    continue
                if not sena_ok and comprobante == "sin_comprobante":
                    notificar_evento_pedido(
                        pedido_id, "recordatorio_sena_24h",
                        f"Te recordamos que el pedido #{pedido_id} está reservado y falta la seña del 50% para confirmarlo. Si necesitás ayuda, respondé este mensaje.",
                    )
                elif sena_ok and diseno == "enviado":
                    notificar_evento_pedido(
                        pedido_id, "recordatorio_diseno_24h",
                        f"¿Pudiste revisar el diseño del pedido #{pedido_id}? Respondé APROBAR o contanos qué cambios necesitás. No produciremos sin tu confirmación.",
                    )
                elif estado == "listo":
                    notificar_evento_pedido(
                        pedido_id, "recordatorio_retiro_24h",
                        f"Tu pedido #{pedido_id} sigue listo. Respondé para coordinar el retiro o la entrega.",
                    )
                elif estado == "entregado" and not postventa:
                    resultado = notificar_evento_pedido(
                        pedido_id, "postventa_24h",
                        f"¡Hola! Queríamos saber cómo salió el pedido #{pedido_id}. Respondé con una puntuación del 1 al 5 y, si querés, un comentario. También podemos repetir el pedido usando los mismos datos.",
                    )
                    if resultado.get("status") in {"enviado", "ya_procesada"}:
                        conn = sqlite3.connect(DB_LOCAL, timeout=10)
                        conn.execute("UPDATE pedidos SET postventa_enviada=1 WHERE id=?", (pedido_id,))
                        conn.commit(); conn.close()
        except Exception as exc:
            print(f"[AUTOMATIZACIONES SUBLIZEN ERROR]: {exc}")

def bucle_alertas_entrega():
    while True:
        time.sleep(21600)
        try:
            hoy = datetime.now().strftime("%Y-%m-%d")
            import datetime as dt_module
            manana = (datetime.now() + dt_module.timedelta(days=1)).strftime("%Y-%m-%d")
            conn = sqlite3.connect(DB_LOCAL)
            proximos = conn.execute("SELECT p.id, p.producto, p.tareas_pendientes, p.fecha_entrega, c.nombre FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id WHERE p.estado != 'entregado' AND p.fecha_entrega IN (?, ?) AND p.alerta_enviada = 0", (hoy, manana)).fetchall()
            if proximos:
                lineas = ["⚠️ ENTREGAS PRÓXIMAS:\n"]
                for id_, producto, tareas, fecha, cliente in proximos:
                    lineas.append(f"• #{id_} {producto} — {cliente or 'sin cliente'} ({'HOY' if fecha == hoy else 'mañana'})" + (f" — falta: {tareas}" if tareas else ""))
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": "\n".join(lineas)}, timeout=10)
                conn.execute(f"UPDATE pedidos SET alerta_enviada = 1 WHERE id IN ({','.join([str(p[0]) for p in proximos])})"); conn.commit()
            conn.close()
        except: pass

def bucle_alertas_turnos():
    while True:
        time.sleep(300)
        try:
            ahora = datetime.now()
            en_una_hora = ahora + __import__("datetime").timedelta(hours=1)
            conn = sqlite3.connect(DB_LOCAL)
            proximos = conn.execute(
                "SELECT id, nombre, motivo, fecha_hora FROM turnos WHERE estado = 'pendiente' AND alerta_enviada = 0 AND fecha_hora BETWEEN ? AND ?",
                (ahora.strftime("%Y-%m-%d %H:%M"), en_una_hora.strftime("%Y-%m-%d %H:%M"))
            ).fetchall()
            for id_t, nombre, motivo, fecha_hora in proximos:
                extra = f" — {motivo}" if motivo else ""
                texto = f"📅 Turno pronto: {nombre}{extra} a las {fecha_hora[-5:]}."
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": texto}, timeout=10)
                conn.execute("UPDATE turnos SET alerta_enviada = 1 WHERE id = ?", (id_t,))
            conn.commit()
            conn.close()
        except: pass

def enviar_resumen_arranque(forzar=False):
    try:
        conn = sqlite3.connect(DB_LOCAL)
        conn.execute("DELETE FROM recordatorios WHERE enviado = 1")
        # Los pedidos entregados son el historial de ventas y alimentan las métricas.
        # Solo se eliminan cuando el usuario lo pide de forma explícita.
        conn.commit()
        hoy = datetime.now().strftime("%Y-%m-%d")
        if not forzar and conn.execute("SELECT ultima_fecha FROM control_ejecuciones WHERE tarea = 'resumen_diario'").fetchone() == (hoy,): conn.close(); return

        limite_recs = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        recordatorios = conn.execute("SELECT mensaje, fecha_hora FROM recordatorios WHERE enviado = 0 AND fecha_hora <= ? ORDER BY fecha_hora", (limite_recs,)).fetchall()
        recs_txt = "\n".join([f"• {msg} (📅 {fecha})" for msg, fecha in recordatorios]) if recordatorios else "Nada pendiente."
        
        recs_hoy_rec = conn.execute("SELECT hora, mensaje FROM recordatorios_recurrentes WHERE dia_semana = ?", (datetime.now().weekday(),)).fetchall()
        if recs_hoy_rec: recs_txt += "\n\n🔄 RUTINAS DE HOY:\n" + "\n".join([f"• A las {hora} hs: {msg}" for hora, msg in recs_hoy_rec])

        pendientes = conn.execute("SELECT p.id, p.producto, p.tareas_pendientes, c.nombre FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id WHERE p.estado = 'pendiente' ORDER BY p.id").fetchall()
        pedidos_txt = "\n".join([f"• #{id_} {prod} — {cli or 'sin cliente'}" + (f" (Falta: {tar})" if tar else "") for id_, prod, tar, cli in pendientes]) if pendientes else "¡Mesa limpia!"

        insumos_bajos = conn.execute("SELECT nombre, cantidad, unidad FROM insumos WHERE cantidad <= alerta_minima").fetchall()
        stock_txt = ("\n\n⚠️ ALERTA DE STOCK:\n" + "\n".join([f"• Quedan {cant:g} {uni} de '{nom}'" for nom, cant, uni in insumos_bajos])) if insumos_bajos else ""

        clima_txt = "No disponible"
        try: clima_txt = requests.get("https://wttr.in/TU_CIUDAD?format=3&lang=es", timeout=5).text.strip()
        except: pass

# 1. NOTICIAS DE BOCA (Con navegación web vía REST API)
        noticias_boca = "Sin novedades."
        try:
            print("📰 [BOCA] Buscando noticias xeneizes...")
            url_rss = "https://news.google.com/rss/search?q=Boca+Juniors&hl=es-419&gl=AR&ceid=AR:es-419"
            res_boca = requests.get(url_rss, timeout=10)
            root = ET.fromstring(res_boca.content)
            titulares = [item.find("title").text for item in root.findall(".//item")[:3]]
            if titulares:
                noticias_boca = "\n".join(f"• {t}" for t in titulares)
        except Exception as e:
            print(f"[BOCA] Error RSS: {e}")
                
            if res_boca.status_code == 200:
                noticias_boca = res_boca.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                print("📰 [BOCA] Noticias obtenidas con éxito de la web.")
            else:
                print(f"❌ [BOCA ERROR API]: Status {res_boca.status_code} - {res_boca.text}")
        except Exception as e:
            print(f"❌ [BOCA ERROR LOCAL]: {e}")

        mensaje_resumen = f"☀️ SISTEMA ONLINE\n\n🌡️ CLIMA:\n{clima_txt}\n\n⏰ RECORDATORIOS:\n{recs_txt}\n\n📋 PENDIENTES ({len(pendientes)}):\n{pedidos_txt}{stock_txt}\n\n📰 XENEIZES:\n{noticias_boca}"
        
        res_tg = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": mensaje_resumen}, timeout=10)
        if res_tg.status_code == 200:
            conn.execute("INSERT OR REPLACE INTO control_ejecuciones (tarea, ultima_fecha) VALUES ('resumen_diario', ?)", (hoy,)); conn.commit()
            prompt_audio = f"Sos Rebecca. El usuario tiene {len(pendientes)} trabajos y " + (f"{len(insumos_bajos)} insumos sin stock." if insumos_bajos else "stock OK.") + " Saludo corto. Usa (laughs) o (sighs)."
            res_audio = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}", json={"contents": [{"parts": [{"text": prompt_audio}]}]}, timeout=20)
            if res_audio.status_code == 200: asyncio.run(generar_y_enviar_audio(res_audio.json()["candidates"][0]["content"]["parts"][0]["text"].strip()))
    except: pass
    finally: conn.close()

def bucle_reporte_diario():
    # FASE 1: ESPERAR A QUE n8n ESTÉ 100% INICIADO
    n8n_listo = False
    print("⏳ [CENTINELA] Esperando a que n8n inicie...")
    while not n8n_listo:
        try:
            res = requests.get("http://127.0.0.1:5678/healthz", timeout=3)
            if res.status_code == 200:
                n8n_listo = True
                print("✅ [CENTINELA] n8n detectado. Esperando 15s para que cargue los workflows...")
                time.sleep(15)
        except:
            time.sleep(5)
            
    # FASE 2: BUCLE DE RELOJ Y CALENDARIO
    while True:
        ahora = datetime.now()
        fecha_hoy = ahora.strftime("%Y-%m-%d")
        
        # Consultamos la memoria (DB) para ver si ya se envió hoy
        ya_enviado = False
        try:
            conn = sqlite3.connect(DB_LOCAL)
            fila = conn.execute("SELECT ultima_fecha FROM control_ejecuciones WHERE tarea = 'resumen_diario'").fetchone()
            if fila and fila[0] == fecha_hoy:
                ya_enviado = True
            conn.close()
        except:
            pass

        # === LAS 3 REGLAS ===
        # 1. n8n listo (pasó la fase 1)
        # 2. Son las 7 de la mañana o más (ahora.hour >= 7)
        # 3. No se mandó hoy (not ya_enviado)
        if ahora.hour >= 7 and not ya_enviado:
            print("☀️ [REPORTE] Reloj en hora y nuevo día detectado. Disparando resumen...")
            try:
                # Le quitamos el forzar=True. Ahora es un envío legal.
                enviar_resumen_arranque(forzar=False) 
            except Exception as e:
                print(f"❌ [ERROR REPORTE DIARIO]: {e}")
                
        # Duerme 5 minutos (300 seg) antes de volver a mirar el reloj para no saturar la CPU
        time.sleep(300)

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
FOOTBALL_WATCH = FootballWatch(os.path.dirname(os.path.abspath(__file__)), API_FOOTBALL_KEY)
EQUIPOS_VIGILADOS = ["Boca Juniors", "River Plate", "Inter Miami", "Atletico Madrid",
                      "Barcelona", "Inter Milan", "Real Madrid", "Napoli", "Aston Villa", "Colo Colo"]
_cache_ids_equipos = {}

def normalizar_equipo(nombre_input):
    nombre_lower = nombre_input.lower().strip()
    for nombre_completo in EQUIPOS_VIGILADOS:
        if nombre_lower in nombre_completo.lower() or nombre_completo.lower() in nombre_lower:
            return nombre_completo
    return nombre_input  # no está en la lista corta, se busca tal cual igual

def obtener_id_equipo(nombre):
    if nombre in _cache_ids_equipos:
        return _cache_ids_equipos[nombre]
    try:
        data = FOOTBALL_WATCH.request("teams", {"search": nombre})
        if not data: return None
        tid = data[0]["team"]["id"]
        _cache_ids_equipos[nombre] = tid
        return tid
    except FootballUnavailable:
        raise
    except Exception as e:
        print(f"[FUTBOL] Error buscando ID de {nombre}: {e}")
        return None

def bucle_aburrimiento():
    global ULTIMA_INTERACCION
    while True:
        time.sleep(120)
        if MODO_NO_MOLESTAR:
            continue
        if (time.time() - ULTIMA_INTERACCION) > 3600:
            try:
                tipo_envio = random.choice(["audio", "texto"])
                prompt = "Sos Rebecca. Hace 1 hora no te hablan. Saludo sarcástico. " + ("Usa (sighs), (laughs)." if tipo_envio == "audio" else "Corto.")
                res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY_BOCA}", json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
                if res.status_code == 200:
                    mensaje = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if tipo_envio == "audio": asyncio.run(generar_y_enviar_audio(mensaje))
                    else: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": mensaje}, timeout=10)
                    ULTIMA_INTERACCION = time.time()
            except: pass
            
def bucle_nocturno():
    global ULTIMA_INTERACCION
    ultimo_reto = ""
    ultimo_bloqueo = ""
    ultimo_apagado = ""
    
    while True:
        # Chequeamos cada 1 minuto
        time.sleep(60) 
        ahora = datetime.now()
        fecha_hoy = ahora.strftime("%Y-%m-%d")
        
        # FASE 1: EL RETO AUDIO (De 2:00 AM a 3:59 AM)
        if 2 <= ahora.hour < 4:
            # Le sacamos el (time.time() - ULTIMA_INTERACCION) < 2700
            if ultimo_reto != fecha_hoy:
                try:
                    prompt = "Sos Rebecca. Son pasadas las 2 AM. Retá a Usuario por seguir despierto en la PC. Obligalo a que vaya a dormir para rendir bien en la facultad (UNAHUR) y en el taller de sublimación. Usá (sighs) o (laughs), puteá un poco, sé breve, sarcástica y agresiva pero que se note que lo cuidás. Usa tu jerga cyberpunk y argentina de barrio."
                    res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}", json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
                    
                    if res.status_code == 200:
                        mensaje = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                        asyncio.run(generar_y_enviar_audio(mensaje))
                        ultimo_reto = fecha_hoy
                except Exception as e: 
                    print(f"[ERROR NOCTURNO FASE 1]: {e}") # Le agregué un print por si falla Fish Audio o Gemini

        # FASE 2: ADVERTENCIA FINAL Y BLOQUEO (4:00 AM a 4:14 AM)
        elif ahora.hour == 4 and ahora.minute < 15:
            if ultimo_bloqueo != fecha_hoy:
                try:
                    aviso = "⚠️ [SISTEMA] 4:00 AM. ADVERTENCIA FINAL DE REBECCA: Te bloqueo la pantalla. Tenés exactamente 15 minutos para guardar tus códigos y diseños. A las 4:15 te apago la compu de cuajo."
                    enviar_mensaje_telegram(aviso)
                    
                    time.sleep(3) 
                    os.system("rundll32.exe user32.dll,LockWorkStation") # Bloquea la sesión
                    ultimo_bloqueo = fecha_hoy
                except Exception as e:
                    print(f"[ERROR NOCTURNO FASE 2]: {e}")

        # FASE 3: EL APAGÓN MORTAL (A partir de las 4:15 AM)
        elif ahora.hour == 4 and 15 <= ahora.minute <= 59:
            if ultimo_apagado != fecha_hoy:
                try:
                    aviso_final = "💀 [SISTEMA] 4:15 AM. Se acabó el tiempo, choom. Protocolo Zzz activado. Apagando el cyberdeck en 10 segundos. Que descanses."
                    enviar_mensaje_telegram(aviso_final)
                    time.sleep(2)
                    # Comando nativo de Windows para APAGAR la PC en 10 segundos
                    os.system("shutdown /s /t 10") 
                    
                    ultimo_apagado = fecha_hoy
                except Exception as e:
                    print(f"[ERROR NOCTURNO FASE 3]: {e}")

async def obtener_sesion_media():
    manager = await MediaManager.request_async()
    return manager.get_current_session()

async def obtener_info_media():
    sesion = await obtener_sesion_media()
    if not sesion:
        return None
    info = await sesion.try_get_media_properties_async()
    playback = sesion.get_playback_info()
    estado_map = {4: "sonando", 5: "en pausa", 3: "detenido"}
    return {
        "titulo": info.title,
        "artista": info.artist,
        "app": sesion.source_app_user_model_id,
        "estado": estado_map.get(playback.playback_status, "desconocido")
    }

async def controlar_media(comando):
    sesion = await obtener_sesion_media()
    if not sesion:
        return False
    if comando == "play": await sesion.try_play_async()
    elif comando == "pause": await sesion.try_pause_async()
    elif comando == "toggle": await sesion.try_toggle_play_pause_async()
    elif comando == "next": await sesion.try_skip_next_async()
    elif comando == "previous": await sesion.try_skip_previous_async()
    else: return False
    return True


def bucle_aprendizaje_musical():
    """Aprende sólo canciones que permanecen sonando; todo queda en la base local."""
    clave_actual = ""
    inicio_actual = 0.0
    registrada = False
    while True:
        try:
            info = asyncio.run(obtener_info_media())
            titulo = str((info or {}).get("titulo") or "").strip()
            artista = str((info or {}).get("artista") or "").strip()
            estado = str((info or {}).get("estado") or "")
            clave = _normalizar_clave_musical(f"{titulo}|{artista}")
            if estado == "sonando" and titulo and artista:
                if clave != clave_actual:
                    clave_actual = clave
                    inicio_actual = time.monotonic()
                    registrada = False
                elif not registrada and time.monotonic() - inicio_actual >= 60:
                    registrar_preferencia_musical(titulo, artista, origen="escucha")
                    registrada = True
            elif clave != clave_actual:
                clave_actual = clave
                inicio_actual = time.monotonic()
                registrada = False
        except Exception:
            pass
        time.sleep(15)


@app.post("/ejecutar")
async def ejecutar_comando_maestro(req: ComandoMaster):
    global ULTIMA_INTERACCION, BECCA_OJO_ACTIVO
    inicio_comando = time.perf_counter()
    accion = normalizar_accion(req.accion)
    param = req.parametro.strip()
    if accion == 'gestionar_comercio':
        from rebecca_companion.commerce import execute
        return await asyncio.to_thread(execute, DB_LOCAL, param, req.parametro4)

    # LISTA DE ACCIONES FANTASMA (No resetean el reloj de inactividad)
    if accion not in ["revisar_recordatorios", "consultar_recordatorios", "ver_recordatorios", "estado_observador", "consultar_observador", "estado_pc", "tiempo_inactivo", "consultar_estado_partido", "consultar_estado_partido_protegido", "consultar_partido_protegido", "consultar_partido_boca"]:
        ULTIMA_INTERACCION = time.time()

    print(f"[NODO MAESTRO] Acción: {accion} | Param: {param}")

    
    try:
        

       
        # SISTEMA Y PROGRAMAS
        if accion == "sistema":
            sistema_param = normalizar_accion(param).replace("_", " ")
            # Cancelar siempre tiene prioridad: "abortar apagado" también contiene "apagar".
            if re.search(r"\b(?:abortar|aborta|cancelar|cancela)\b.*\b(?:apagado|reinicio)\b", sistema_param):
                os.system("shutdown /a")
                return {"status": "success", "message": "Apagado o reinicio cancelado."}
            elif re.search(r"\b(?:bloquear|bloquea)\b.*\b(?:pc|computadora|equipo)\b", sistema_param) or sistema_param in {"bloquear", "bloquea"}:
                os.system("rundll32.exe user32.dll,LockWorkStation")
                return {"status": "success", "message": "PC bloqueada."}
            elif re.search(r"\b(?:apagar|apaga)\b.*\b(?:pc|computadora|equipo)\b", sistema_param) or sistema_param in {"apagar", "apaga"}:
                os.system("shutdown /s /t 10")
                return {"status": "success", "message": "Apagando."}
            elif re.search(r"\b(?:reiniciar|reinicia)\b.*\b(?:pc|computadora|equipo)\b", sistema_param) or sistema_param in {"reiniciar", "reinicia"}:
                os.system("shutdown /r /t 10")
                return {"status": "success", "message": "Reiniciando."}
            elif re.search(r"\b(?:minimizar|minimiza|mostrar|mostra)\b.*\b(?:todo|escritorio|ventanas)\b", sistema_param) or sistema_param in {"minimizar", "minimiza"}:
                pyautogui.hotkey('win', 'd')
                return {"status": "success", "message": "Ventanas minimizadas."}
            elif re.search(r"\b(?:subir|subi|aumentar|aumenta)\b.*\b(?:volumen|audio|sonido)\b", sistema_param):
                pyautogui.press("volumeup", presses=12)
                return {"status": "success", "message": "Volumen alto."}
            elif re.search(r"\b(?:bajar|baja|disminuir|disminui)\b.*\b(?:volumen|audio|sonido)\b", sistema_param):
                pyautogui.press("volumedown", presses=12)
                return {"status": "success", "message": "Volumen bajo."}
            elif re.search(r"\b(?:mutear|mutea|silenciar|silencia)\b(?:.*\b(?:volumen|audio|sonido)\b)?", sistema_param):
                pyautogui.press("volumemute")
                return {"status": "success", "message": "Audio silenciado o restaurado."}
            
            # --- ACÁ ESTÁ LA CAPTURA DE PANTALLA INTELIGENTE ---
            elif re.search(r"\b(?:captura|capturar|pantallazo|screenshot|foto de (?:la )?pantalla)\b", sistema_param):
                return await asyncio.to_thread(capturar_y_enviar_telegram, param)
            return {
                "status": "error",
                "message": "No reconocí ese control del sistema. No ejecuté nada.",
            }

        elif accion == "tiempo_inactivo":
            segundos_inactivo = time.time() - ULTIMA_INTERACCION
            return {"status": "success", "inactividad": segundos_inactivo}
        
        elif accion == "abrir_programa":
            return await asyncio.to_thread(abrir_programa_local, param)

        elif accion == "capturar_pantalla_local":
            return await asyncio.to_thread(capturar_pantalla_local, param)

        elif accion == "imprimir_mesa":
            if not WIN32_DISPONIBLE: raise HTTPException(status_code=500, detail="Falta pywin32.")
            ps_app = win32com.client.Dispatch("Photoshop.Application")
            if os.path.exists(MESA_TRABAJO): ps_app.Open(MESA_TRABAJO); time.sleep(2)
            else: raise HTTPException(status_code=404, detail="Mesa no encontrada.")
            try: ps_app.DoAction("Imprimir_Subli", "Sublizen"); return {"status": "success", "message": "Sublimación enviada."}
            except: ps_app.ActiveDocument.PrintOut(); return {"status": "success", "message": "Impresión estándar enviada."}

        elif accion == "estado_pc":
            return {"status": "success", "message": f"CPU: {psutil.cpu_percent()}%. RAM: {psutil.virtual_memory().percent}%. Disco: {psutil.disk_usage('C:\\').percent}% usado."}

        elif accion in ["estado_observador", "consultar_observador"]:
            return {
                "status": "success",
                "message": f"El observador de Rebecca está {'ACTIVADO' if BECCA_OJO_ACTIVO else 'desactivado'}.",
            }

        elif accion == "consultar_cliente":
            busqueda = param.strip()
            if not busqueda:
                return {"status": "error", "message": "Decime el nombre, teléfono o número del cliente."}
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            try:
                if busqueda.lstrip("#").isdigit():
                    cliente = conn.execute(
                        """
                        SELECT id,nombre,COALESCE(telefono,''),COALESCE(email,''),
                               COALESCE(whatsapp,''),COALESCE(notas,''),COALESCE(modo_humano,0)
                        FROM clientes WHERE id=?
                        """,
                        (int(busqueda.lstrip("#")),),
                    ).fetchone()
                else:
                    patron = f"%{busqueda}%"
                    cliente = conn.execute(
                        """
                        SELECT id,nombre,COALESCE(telefono,''),COALESCE(email,''),
                               COALESCE(whatsapp,''),COALESCE(notas,''),COALESCE(modo_humano,0)
                        FROM clientes
                        WHERE nombre LIKE ? OR telefono LIKE ? OR whatsapp LIKE ? OR email LIKE ?
                        ORDER BY CASE WHEN LOWER(nombre)=LOWER(?) THEN 0 ELSE 1 END, id DESC
                        LIMIT 1
                        """,
                        (patron, patron, patron, patron, busqueda),
                    ).fetchone()
                if not cliente:
                    return {"status": "error", "message": f"No encontré un cliente que coincida con '{busqueda}'."}
                pedidos = conn.execute(
                    """
                    SELECT id,producto,estado,COALESCE(fecha_entrega,''),
                           COALESCE(precio_total,0),COALESCE(sena_pagada,0)
                    FROM pedidos WHERE cliente_id=? ORDER BY id DESC LIMIT 5
                    """,
                    (cliente[0],),
                ).fetchall()
            finally:
                conn.close()
            contacto = cliente[4] or cliente[2] or "sin teléfono"
            detalle = [
                f"Cliente #{cliente[0]}: {cliente[1]}",
                f"Contacto: {contacto}",
                f"Email: {cliente[3] or 'sin email'}",
                f"Atención: {'humana' if cliente[6] else 'Rebecca'}",
            ]
            if cliente[5]:
                detalle.append(f"Notas: {cliente[5]}")
            if pedidos:
                detalle.append("Pedidos recientes:")
                detalle.extend(
                    f"#{pedido_id} {producto} — {estado} — saldo ${max(0, float(total)-float(pagado)):,.2f}"
                    + (f" — entrega {entrega}" if entrega else "")
                    for pedido_id, producto, estado, entrega, total, pagado in pedidos
                )
            else:
                detalle.append("No tiene pedidos registrados.")
            return {"status": "success", "message": "\n".join(detalle)}

        # RECORDATORIOS
        elif accion == "crear_recordatorio":
            f_txt = req.parametro2.strip() or param
            f_limpia = re.sub(r'(\d{1,2})\s*(?:hs|hrs|h)\b', r'\1:00', f_txt, flags=re.IGNORECASE)
            f_pars = dateparser.parse(f_limpia, languages=["es"], settings={"PREFER_DATES_FROM": "future"})
            if not f_pars: return {"status": "error", "message": "No entendí la fecha."}
            conn = sqlite3.connect(DB_LOCAL)
            conn.execute("INSERT INTO recordatorios (mensaje, fecha_hora) VALUES (?, ?)", (param or "Aviso", f_pars.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit(); conn.close()
            return {"status": "success", "message": f"Anotado para el {f_pars.strftime('%d/%m a las %H:%M')}."}

        elif accion in ["crear_recordatorio_recurrente", "recordatorio_recurrente"]:
            d_map = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6}
            d_num = d_map.get(req.parametro2.lower().strip())
            if d_num is None: return {"status": "error", "message": "Día inválido."}
            hm = re.search(r'(\d{1,2})[:\s]*(\d{2})?', req.parametro3)
            if not hm: return {"status": "error", "message": "Hora inválida."}
            h_fmt = f"{int(hm.group(1)):02d}:{int(hm.group(2)) if hm.group(2) else 0:02d}"
            conn = sqlite3.connect(DB_LOCAL)
            conn.execute("INSERT INTO recordatorios_recurrentes (mensaje, dia_semana, hora) VALUES (?, ?, ?)", (param, d_num, h_fmt))
            conn.commit(); conn.close()
            return {"status": "success", "message": f"Guardado. Todos los {req.parametro2.strip()} a las {h_fmt}."}

        elif accion in ["consultar_recordatorios", "ver_recordatorios"]:
            conn = sqlite3.connect(DB_LOCAL)
            recs = conn.execute("SELECT id, mensaje, fecha_hora FROM recordatorios WHERE enviado = 0 ORDER BY fecha_hora").fetchall()
            recs_rec = conn.execute("SELECT id, mensaje, dia_semana, hora FROM recordatorios_recurrentes").fetchall()
            conn.close()
            if not recs and not recs_rec: return {"status": "success", "message": "Agenda limpia."}
            pt = []
            if recs: pt.append("🗓️ ÚNICOS:\n" + "\n".join([f"• [#{r[0]}] {r[1]} ({r[2]})" for r in recs]))
            if recs_rec: pt.append("🔁 SEMANALES:\n" + "\n".join([f"• [#{r[0]}] {r[1]} (Día {r[2]} a las {r[3]})" for r in recs_rec]))
            return {"status": "success", "message": "\n\n".join(pt)}
        
        elif accion == "crear_presupuesto_cliente":
            producto_buscado = param.strip()
            cantidad = extraer_numero(req.parametro2) if req.parametro2 else 1
            cliente = req.parametro3.strip()
            whatsapp = req.parametro4.strip()
            if not producto_buscado or not cliente or not whatsapp or cantidad is None or cantidad <= 0:
                return {"status": "error", "message": "Necesito producto, cantidad, nombre y WhatsApp para preparar el presupuesto."}
            producto = buscar_producto_difuso(producto_buscado)
            if not producto:
                return {"status": "error", "message": f"No tengo un precio cargado para '{producto_buscado}'. Un vendedor debe cotizarlo manualmente."}
            _, nombre_real, precio_unitario, _ = producto
            cantidad = float(cantidad)
            total = round(float(precio_unitario) * cantidad, 2)
            cliente_id = obtener_o_crear_cliente(cliente)
            ahora_dt = datetime.now()
            ahora = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
            vence = (ahora_dt + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            conn.execute("UPDATE clientes SET telefono=?, whatsapp=?, ultima_interaccion=? WHERE id=?", (whatsapp, whatsapp, ahora, cliente_id))
            existente = conn.execute(
                """
                SELECT id, total, vence_en FROM presupuestos
                WHERE cliente_id=? AND LOWER(producto)=LOWER(?) AND cantidad=? AND total=?
                  AND estado='pendiente' AND vence_en>=?
                ORDER BY id DESC LIMIT 1
                """,
                (cliente_id, nombre_real, cantidad, total, ahora),
            ).fetchone()
            if existente:
                presupuesto_id, _, vence = existente
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO presupuestos
                        (cliente_id, producto, cantidad, precio_unitario, total, estado, creado_en, vence_en)
                    VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?)
                    """,
                    (cliente_id, nombre_real, cantidad, float(precio_unitario), total, ahora, vence),
                )
                presupuesto_id = cursor.lastrowid
            conn.commit(); conn.close()
            cantidad_txt = int(cantidad) if cantidad.is_integer() else cantidad
            return {
                "status": "quote_ready", "presupuesto_id": int(presupuesto_id),
                "producto": nombre_real, "cantidad": cantidad, "precio_unitario": float(precio_unitario),
                "total": total, "sena_requerida": round(total * 0.5, 2), "vence_en": vence,
                "message": (
                    f"Presupuesto #{int(presupuesto_id)}: {cantidad_txt} x {nombre_real} a ${float(precio_unitario):,.2f} "
                    f"= ${total:,.2f}. Seña del 50%: ${total * 0.5:,.2f}. "
                    "Pedile al cliente que confirme expresamente este resumen antes de crear el pedido."
                ),
            }

        elif accion == "confirmar_presupuesto_cliente":
            presupuesto_id = extraer_numero(param)
            whatsapp = req.parametro2.strip()
            if presupuesto_id is None or not whatsapp:
                return {"status": "error", "message": "Necesito el número de presupuesto y el WhatsApp actual."}
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            fila = conn.execute(
                """
                SELECT pr.cliente_id, pr.producto, pr.cantidad, pr.total, pr.estado,
                       pr.vence_en, pr.pedido_id, COALESCE(NULLIF(c.whatsapp,''), c.telefono, ''), c.nombre
                FROM presupuestos pr JOIN clientes c ON c.id=pr.cliente_id WHERE pr.id=?
                """,
                (int(presupuesto_id),),
            ).fetchone()
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not fila or not whatsapp_coincide(fila[7], whatsapp):
                conn.close()
                return {"status": "error", "message": "No encontré ese presupuesto para este WhatsApp."}
            if fila[6]:
                conn.close()
                return {"status": "awaiting_deposit", "pedido_id": int(fila[6]), "total": float(fila[3]), "sena_requerida": round(float(fila[3]) * 0.5, 2), "message": f"Ese presupuesto ya creó el pedido #{int(fila[6])}."}
            if fila[4] != "pendiente" or fila[5] < ahora:
                conn.close()
                return {"status": "expired_quote", "message": "El presupuesto venció o ya no está disponible. Voy a preparar uno actualizado."}
            token = secrets.token_urlsafe(24)
            cursor = conn.execute(
                """
                INSERT INTO pedidos
                    (producto, cantidad, estado, cliente_id, tareas_pendientes, fecha_creacion,
                     precio_total, sena_pagada, confirmado, sena_confirmada, presupuesto_id,
                     seguimiento_token, actualizado_en, diseno_estado, automatizaciones_habilitadas)
                VALUES (?, ?, 'esperando_sena', ?, 'Esperando acreditación de la seña del 50%', ?,
                        ?, 0, 1, 0, ?, ?, ?, 'pendiente', 1)
                """,
                (fila[1], float(fila[2]), int(fila[0]), ahora, float(fila[3]), int(presupuesto_id), token, ahora),
            )
            pedido_id = cursor.lastrowid
            conn.execute("UPDATE presupuestos SET estado='aceptado', aceptado_en=?, pedido_id=? WHERE id=?", (ahora, pedido_id, int(presupuesto_id)))
            registrar_historial_pedido(conn, pedido_id, "pedido_confirmado", f"Presupuesto #{int(presupuesto_id)} aceptado por el cliente.", "cliente")
            conn.commit(); conn.close()
            return {
                "status": "awaiting_deposit", "pedido_id": int(pedido_id), "total": float(fila[3]),
                "sena_requerida": round(float(fila[3]) * 0.5, 2),
                "seguimiento_url": f"{PUBLIC_BASE_URL}/seguimiento/{token}",
                "message": f"Pedido #{int(pedido_id)} confirmado. Seña exacta: ${float(fila[3]) * 0.5:,.2f}. Ahora puede enviar la captura o PDF de la transferencia.",
            }

        elif accion in ["confirmar_compra_cliente", "crear_pedido_cliente"]:
            producto = param.strip()
            cliente = req.parametro2.strip()
            total = extraer_numero(req.parametro3)
            whatsapp = req.parametro4.strip()
            if not producto or not cliente or total is None or total <= 0 or not whatsapp:
                return {
                    "status": "error",
                    "message": "Para confirmar necesito producto, nombre del cliente, precio total y su WhatsApp.",
                }
            cliente_id = obtener_o_crear_cliente(cliente)
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            conn.execute(
                "UPDATE clientes SET telefono = ?, whatsapp = ? WHERE id = ?",
                (whatsapp, whatsapp, cliente_id),
            )
            existente = conn.execute(
                """
                SELECT id FROM pedidos
                WHERE cliente_id = ? AND LOWER(producto) = LOWER(?) AND precio_total = ?
                  AND confirmado = 1 AND sena_confirmada = 0 AND estado != 'cancelado'
                ORDER BY id DESC LIMIT 1
                """,
                (cliente_id, producto, float(total)),
            ).fetchone()
            if existente:
                pedido_id = existente[0]
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO pedidos
                        (producto, estado, cliente_id, tareas_pendientes, fecha_creacion,
                         precio_total, sena_pagada, confirmado, sena_confirmada,
                         seguimiento_token, actualizado_en, automatizaciones_habilitadas)
                    VALUES (?, 'esperando_sena', ?, 'Esperando acreditación de la seña del 50%', ?, ?, 0, 1, 0, ?, ?, 1)
                    """,
                    (producto, cliente_id, ahora, float(total), secrets.token_urlsafe(24), ahora),
                )
                pedido_id = cursor.lastrowid
                registrar_historial_pedido(conn, pedido_id, "pedido_confirmado", "Compra confirmada por el cliente.", "cliente")
            conn.commit()
            conn.close()
            sena_requerida = round(float(total) * 0.5, 2)
            return {
                "status": "awaiting_deposit", "pedido_id": pedido_id,
                "total": float(total), "sena_requerida": sena_requerida,
                "message": (
                    f"Pedido #{pedido_id} confirmado. La seña obligatoria es ${sena_requerida:,.2f} (50%). "
                    "Pedile una captura o PDF de la transferencia. Recibir el archivo no equivale a acreditar el pago."
                ),
            }

        elif accion in ["registrar_comprobante_transferencia", "guardar_comprobante_pago"]:
            pedido_id = extraer_numero(param)
            ruta_archivo = req.parametro2.strip()
            remitente = req.parametro3.strip()
            monto = extraer_numero(req.parametro4) or 0
            if pedido_id is None or not ruta_archivo:
                return {"status": "error", "message": "Necesito el ID del pedido y la ruta local del comprobante."}
            return registrar_comprobante_pago(int(pedido_id), ruta_archivo, monto, remitente)

        elif accion in ["registrar_sena_cliente", "confirmar_sena_cliente"]:
            pedido_id = extraer_numero(param)
            monto = extraer_numero(req.parametro2)
            whatsapp = req.parametro3.strip()
            email = validar_email(req.parametro4)
            if pedido_id is None or monto is None:
                return {"status": "error", "message": "Necesito el ID del pedido y el monto acreditado."}
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            row = conn.execute(
                "SELECT cliente_id, precio_total, sena_pagada, sena_confirmada FROM pedidos WHERE id = ?",
                (int(pedido_id),),
            ).fetchone()
            if not row:
                conn.close()
                return {"status": "error", "message": f"No encontré el pedido #{int(pedido_id)}."}
            cliente_id, total, sena_actual, ya_confirmada = row
            total = float(total or 0)
            requerida = round(total * 0.5, 2)
            if total <= 0:
                conn.close()
                return {"status": "error", "message": "El pedido todavía no tiene un precio total definido."}
            if abs(float(monto) - requerida) > 0.01:
                conn.close()
                return {
                    "status": "invalid_deposit", "pedido_id": int(pedido_id),
                    "sena_requerida": requerida,
                    "message": f"La seña debe ser exactamente el 50%: ${requerida:,.2f}. No registré ${float(monto):,.2f}.",
                }
            prueba_aprobada = comprobante_aprobado(int(pedido_id), conn)
            prueba_pendiente = conn.execute(
                "SELECT id FROM comprobantes_pago WHERE pedido_id=? AND estado IN ('pendiente','monto_inconsistente') ORDER BY id DESC LIMIT 1",
                (int(pedido_id),),
            ).fetchone()
            if cliente_id:
                campos, valores = [], []
                if whatsapp:
                    campos.extend(["telefono = ?", "whatsapp = ?"])
                    valores.extend([whatsapp, whatsapp])
                if email:
                    campos.append("email = ?")
                    valores.append(email)
                if campos:
                    valores.append(cliente_id)
                    conn.execute(f"UPDATE clientes SET {', '.join(campos)} WHERE id = ?", valores)
            conn.commit()
            conn.close()
            if not prueba_aprobada:
                if prueba_pendiente:
                    return {
                        "status": "awaiting_payment_verification", "pedido_id": int(pedido_id),
                        "message": "El comprobante fue recibido, pero falta aprobarlo en el dashboard. No marqué el pago ni emití el recibo.",
                    }
                return {
                    "status": "needs_payment_proof", "pedido_id": int(pedido_id),
                    "message": "Antes de acreditar la seña necesito una captura o PDF de la transferencia.",
                }
            return emitir_y_enviar_comprobante(int(pedido_id))

        elif accion in ["aprobar_comprobante_transferencia", "aprobar_pago_cliente"]:
            pedido_id = extraer_numero(param)
            comprobante_id = extraer_numero(req.parametro2)
            if pedido_id is None:
                return {"status": "error", "message": "Necesito el ID del pedido."}
            return revisar_comprobante_pago(int(pedido_id), True, comprobante_id, req.parametro3)

        elif accion in ["rechazar_comprobante_transferencia", "rechazar_pago_cliente"]:
            pedido_id = extraer_numero(param)
            comprobante_id = extraer_numero(req.parametro2)
            if pedido_id is None:
                return {"status": "error", "message": "Necesito el ID del pedido."}
            return revisar_comprobante_pago(int(pedido_id), False, comprobante_id, req.parametro3)

        elif accion in ["guardar_email_cliente", "guardar_email_y_enviar_comprobante"]:
            pedido_id = extraer_numero(param)
            email = validar_email(req.parametro2)
            whatsapp = req.parametro3.strip()
            if pedido_id is None or not email:
                return {"status": "error", "message": "Necesito un ID de pedido y un correo válido."}
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            row = conn.execute("SELECT cliente_id FROM pedidos WHERE id = ?", (int(pedido_id),)).fetchone()
            if not row:
                conn.close()
                return {"status": "error", "message": f"No encontré el pedido #{int(pedido_id)}."}
            if row[0]:
                if whatsapp:
                    conn.execute(
                        "UPDATE clientes SET email = ?, telefono = ?, whatsapp = ? WHERE id = ?",
                        (email, whatsapp, whatsapp, row[0]),
                    )
                else:
                    conn.execute("UPDATE clientes SET email = ? WHERE id = ?", (email, row[0]))
            conn.commit()
            conn.close()
            return emitir_y_enviar_comprobante(int(pedido_id))

        elif accion in ["reenviar_comprobante_cliente", "reintentar_comprobante"]:
            pedido_id = extraer_numero(param)
            if pedido_id is None:
                return {"status": "error", "message": "Necesito el ID del pedido."}
            return emitir_y_enviar_comprobante(int(pedido_id))

        elif accion == "consultar_pedido_cliente":
            pedido_id = extraer_numero(param)
            whatsapp = req.parametro2.strip()
            if pedido_id is None or not whatsapp:
                return {"status": "error", "message": "Necesito el número de pedido y el WhatsApp actual."}
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            fila = pedido_pertenece_a_whatsapp(conn, int(pedido_id), whatsapp)
            conn.close()
            if not fila:
                return {"status": "error", "message": "No encontré ese pedido asociado a este WhatsApp."}
            token = fila[10]
            datos = datos_seguimiento_publico(token) if token else None
            if not datos:
                return {"status": "error", "message": "El seguimiento de ese pedido todavía no está disponible."}
            return {"status": "success", **datos, "seguimiento_url": f"{PUBLIC_BASE_URL}/seguimiento/{token}"}

        elif accion in ["responder_diseno_cliente", "aprobar_diseno_cliente"]:
            pedido_id = extraer_numero(param)
            decision = unicodedata.normalize("NFKD", req.parametro2.lower()).encode("ascii", "ignore").decode("ascii").strip()
            comentario = req.parametro3.strip()
            whatsapp = req.parametro4.strip()
            if pedido_id is None or not whatsapp or not decision:
                return {"status": "error", "message": "Necesito ID de pedido, aprobación o cambios, y el WhatsApp actual."}
            aprobar = decision in {"aprobar", "aprobado", "si", "ok", "acepto", "confirmar", "confirmado"}
            pedir_cambios = any(p in decision for p in ["cambio", "correg", "rechaz", "no", "modific"])
            if not aprobar and not pedir_cambios:
                return {"status": "error", "message": "Decime claramente si aprobás el diseño o qué cambios necesitás."}
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            fila = pedido_pertenece_a_whatsapp(conn, int(pedido_id), whatsapp)
            if not fila:
                conn.close()
                return {"status": "error", "message": "No encontré ese pedido asociado a este WhatsApp."}
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if aprobar:
                nueva_tarea = "Diseño aprobado; producción pendiente" if fila[4] <= 0 else "Diseño aprobado; listo para producción"
                conn.execute("UPDATE pedidos SET diseno_estado='aprobado', tareas_pendientes=?, actualizado_en=? WHERE id=?", (nueva_tarea, ahora, int(pedido_id)))
                registrar_historial_pedido(conn, int(pedido_id), "diseno_aprobado", comentario or "Diseño aprobado sin cambios.", "cliente")
                mensaje = f"Diseño del pedido #{int(pedido_id)} aprobado. Quedó registrado y no se modificará sin una nueva confirmación."
                estado_respuesta = "design_approved"
            else:
                detalle = comentario or "El cliente pidió cambios sin especificar detalles."
                conn.execute("UPDATE pedidos SET diseno_estado='cambios_solicitados', tareas_pendientes=?, actualizado_en=? WHERE id=?", (f"Corregir diseño: {detalle}"[:500], ahora, int(pedido_id)))
                registrar_historial_pedido(conn, int(pedido_id), "cambios_diseno", detalle, "cliente")
                mensaje = f"Anoté los cambios del diseño para el pedido #{int(pedido_id)}. El taller enviará una nueva muestra antes de producir."
                estado_respuesta = "design_changes_requested"
            conn.commit(); conn.close()
            return {"status": estado_respuesta, "pedido_id": int(pedido_id), "message": mensaje}

        elif accion == "configurar_entrega_cliente":
            pedido_id = extraer_numero(param)
            modalidad = unicodedata.normalize("NFKD", req.parametro2.lower()).encode("ascii", "ignore").decode("ascii").strip()
            entrega_partes = [parte.strip() for parte in req.parametro3.split("|", 1)]
            direccion = entrega_partes[0] if entrega_partes else ""
            turno = entrega_partes[1] if len(entrega_partes) > 1 else ""
            whatsapp = req.parametro4.strip()
            modalidad = "envio" if any(p in modalidad for p in ["envio", "delivery", "domicilio"]) else "retiro" if any(p in modalidad for p in ["retiro", "local", "buscar"]) else ""
            if pedido_id is None or not whatsapp or not modalidad:
                return {"status": "error", "message": "Necesito ID de pedido y saber si es retiro por el local o envío."}
            if modalidad == "envio" and not direccion:
                return {"status": "needs_address", "message": "Para el envío necesito la dirección completa y una referencia si hace falta."}
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            fila = pedido_pertenece_a_whatsapp(conn, int(pedido_id), whatsapp)
            if not fila:
                conn.close()
                return {"status": "error", "message": "No encontré ese pedido asociado a este WhatsApp."}
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE pedidos SET modalidad_entrega=?, direccion_entrega=?, turno_entrega=?, actualizado_en=? WHERE id=?", (modalidad, direccion[:500], turno[:120], ahora, int(pedido_id)))
            registrar_historial_pedido(conn, int(pedido_id), "entrega_configurada", f"{modalidad}: {direccion or 'retiro por el local'}; {turno or 'turno a coordinar'}", "cliente")
            conn.commit(); conn.close()
            detalle = f" a {direccion}" if modalidad == "envio" else " por el local"
            return {"status": "delivery_configured", "pedido_id": int(pedido_id), "message": f"Entrega del pedido #{int(pedido_id)} configurada: {modalidad}{detalle}. El horario se confirma cuando esté listo."}

        elif accion == "solicitar_atencion_humana":
            whatsapp = param.strip()
            nombre = req.parametro2.strip() or "Cliente"
            motivo = req.parametro3.strip() or "El cliente pidió hablar con una persona."
            if not whatsapp:
                return {"status": "error", "message": "Falta el WhatsApp actual."}
            cliente_id = obtener_o_crear_cliente(nombre)
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            conn.execute("UPDATE clientes SET telefono=?, whatsapp=?, modo_humano=1, modo_humano_desde=?, ultima_interaccion=? WHERE id=?", (whatsapp, whatsapp, ahora, ahora, cliente_id))
            conn.commit(); conn.close()
            try:
                enviar_mensaje_telegram(f"🙋 Atención humana solicitada por {nombre} ({whatsapp}). Motivo: {motivo}")
            except Exception:
                pass
            return {"status": "human_handoff", "message": "Listo, pausé las respuestas automáticas. Una persona del equipo va a continuar por este chat."}

        elif accion == "registrar_satisfaccion_cliente":
            pedido_id = extraer_numero(param)
            puntuacion = extraer_numero(req.parametro2)
            comentario = req.parametro3.strip()
            whatsapp = req.parametro4.strip()
            if pedido_id is None or puntuacion is None or not whatsapp or not 1 <= int(puntuacion) <= 5:
                return {"status": "error", "message": "Necesito ID de pedido y una puntuación del 1 al 5."}
            conn = sqlite3.connect(DB_LOCAL, timeout=10)
            fila = pedido_pertenece_a_whatsapp(conn, int(pedido_id), whatsapp)
            if not fila or fila[2] != "entregado":
                conn.close()
                return {"status": "error", "message": "No encontré un pedido entregado asociado a este WhatsApp."}
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO opiniones_clientes (pedido_id,puntuacion,comentario,creado_en) VALUES (?,?,?,?) ON CONFLICT(pedido_id) DO UPDATE SET puntuacion=excluded.puntuacion, comentario=excluded.comentario, creado_en=excluded.creado_en",
                (int(pedido_id), int(puntuacion), comentario[:1000], ahora),
            )
            registrar_historial_pedido(conn, int(pedido_id), "opinion_cliente", f"{int(puntuacion)}/5. {comentario}".strip(), "cliente")
            conn.commit(); conn.close()
            return {"status": "feedback_saved", "message": "¡Gracias! Guardé tu opinión. Nos ayuda muchísimo a mejorar."}

        elif accion in ["generar_recibo_sena", "recibo_sena"]:
            pedido_id = extraer_numero(param)
            if not pedido_id:
                return {"status": "error", "message": "Falta el número de pedido (#ID)."}
            
            conn = sqlite3.connect(DB_LOCAL)
            ped = conn.execute("""
                SELECT c.nombre, p.producto, p.sena_pagada, p.estado 
                FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id 
                WHERE p.id = ?
            """, (int(pedido_id),)).fetchone()
            conn.close()
            
            if not ped:
                return {"status": "error", "message": f"No encontré el pedido #{pedido_id}."}
            
            cliente_nom, producto_nom, monto_sena, estado_actual = ped
            
            # 1. Genera el PDF
            ruta_pdf = generar_recibo_pdf(
                cliente=cliente_nom or "Cliente", 
                producto=producto_nom, 
                monto=monto_sena or 0, 
                pedido_id=int(pedido_id)
            )
            
            # 2. Envío forzado y directo a Telegram
            try:
                with open(ruta_pdf, "rb") as archivo_pdf:
                    url_tel = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
                    respuesta = requests.post(url_tel, data={"chat_id": CHAT_ID}, files={"document": archivo_pdf}, timeout=15)
                    
                    if respuesta.status_code != 200:
                        print(f"❌ Error de Telegram: {respuesta.text}")
            except Exception as e:
                print(f"❌ Error interno enviando PDF: {e}")
            
            return {"status": "success", "message": f"Recibo con QR emitido para el pedido #{pedido_id} (Estado: {estado_actual})."}
        
        elif accion == "enviar_recibo_whatsapp":
            pedido_id = extraer_numero(param)
            numero_cliente = req.parametro3.strip() # Lo inyectamos directo desde n8n
            
            if not pedido_id or not numero_cliente:
                return {"status": "error", "message": "Falta el número de pedido o el contacto."}
            
            conn = sqlite3.connect(DB_LOCAL)
            ped = conn.execute("""
                SELECT c.nombre, p.producto, p.sena_pagada, p.estado 
                FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id 
                WHERE p.id = ?
            """, (int(pedido_id),)).fetchone()
            conn.close()
            
            if not ped:
                return {"status": "error", "message": f"No encontré el pedido #{pedido_id}."}
            
            cliente_nom, producto_nom, monto_sena, estado_actual = ped
            
            # 1. Genera el PDF con la misma función de siempre
            ruta_pdf = generar_recibo_pdf(
                cliente=cliente_nom or "Cliente", 
                producto=producto_nom, 
                monto=monto_sena or 0, 
                pedido_id=int(pedido_id)
            )
            
            # 2. Envío al puente de Node.js
            try:
                url_node = f"{WHATSAPP_BRIDGE_URL}/enviar_pdf"
                payload = {
                    "numero": numero_cliente,
                    "ruta_pdf": ruta_pdf,
                    "caption": f"📄 ¡Hola {cliente_nom}! Acá tenés el comprobante de tu pedido #{pedido_id}."
                }
                res_node = requests.post(url_node, json=payload, timeout=10)
                
                if res_node.status_code == 200:
                    return {"status": "success", "message": "¡Recibo generado y enviado por WhatsApp!"}
                else:
                    return {"status": "error", "message": f"Error del puente Node: {res_node.text}"}
            except Exception as e:
                return {"status": "error", "message": f"Fallo al enviar a WhatsApp: {str(e)}"}
        
        elif accion == "programar_correo":
            destinatario = param.strip()
            fecha_texto = req.parametro2.strip()
            
            # Dividimos param3 usando un | (pipe) para separar el asunto del cuerpo
            contenido = req.parametro3.split("|", 1)
            asunto = contenido[0].strip() if len(contenido) > 0 else "Aviso de SubliZen"
            cuerpo = contenido[1].strip() if len(contenido) > 1 else ""
            archivo_crudo = req.parametro4.strip()
            archivo_final = ""

            if archivo_crudo:
                # 1. Si n8n me pasa la ruta completa y exacta, la uso de una
                if os.path.isabs(archivo_crudo) and os.path.exists(archivo_crudo):
                    archivo_final = archivo_crudo
                else:
                    # 2. Si solo me pasaste el nombre, prendo el radar y lo busco
                    user_profile = os.environ.get("USERPROFILE", "C:\\")
                    carpetas_a_buscar = [
                        os.path.join(user_profile, "Desktop"),  # Busca en tu Escritorio
                        os.path.join(user_profile, "Documents"), # Busca en tus Documentos
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "correos") # Busca en tu carpeta local
                    ]
                    
                    for carpeta in carpetas_a_buscar:
                        if not os.path.exists(carpeta): continue
                        for root, _, files in os.walk(carpeta):
                            for file in files:
                                if archivo_crudo.lower() in file.lower(): # Coincidencia flexible
                                    archivo_final = os.path.join(root, file)
                                    break # Lo encontró, corta la búsqueda
                            if archivo_final: break
                        if archivo_final: break
                    
            fecha_parseada = dateparser.parse(fecha_texto, languages=["es"], settings={"PREFER_DATES_FROM": "future"})
            if not fecha_parseada:
                return {"status": "error", "message": f"No pude entender la fecha '{fecha_texto}'."}

            conn = sqlite3.connect(DB_LOCAL)
            conn.execute(
                "INSERT INTO correos_programados (destinatario, asunto, cuerpo, archivo, fecha_hora) VALUES (?, ?, ?, ?, ?)",
                (destinatario, asunto, cuerpo, archivo_final, fecha_parseada.strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"¡Anotado, choom! Correo encolado para {destinatario} el {fecha_parseada.strftime('%d/%m a las %H:%M')}."}
        
        elif accion in ["eliminar_recordatorio", "borrar_recordatorio", "quitar_recordatorio", "eliminar", "borrar", "quitar", "sacar", "volar_recordatorio"]:
 
            conn = sqlite3.connect(DB_LOCAL)
 
            texto_param = param.replace("#", "").strip()
            if texto_param.isdigit():
                id_borrar = int(texto_param)
                conn.execute("DELETE FROM recordatorios WHERE id = ?", (id_borrar,))
                conn.execute("DELETE FROM recordatorios_recurrentes WHERE id = ?", (id_borrar,))
                msg_si_borro = f"Listo, recordatorio #{id_borrar} fulminado de la base de datos."
                msg_si_nada = f"No encontré ningún recordatorio con el número #{id_borrar}."
            else:
                conn.execute("DELETE FROM recordatorios WHERE LOWER(mensaje) LIKE ?", (f"%{param.lower().strip()}%",))
                conn.execute("DELETE FROM recordatorios_recurrentes WHERE LOWER(mensaje) LIKE ?", (f"%{param.lower().strip()}%",))
                msg_si_borro = f"Listo, borré los recordatorios que decían '{param}'."
                msg_si_nada = f"No encontré ningún recordatorio que coincida con '{param}'. Decime el número exacto (#) o una palabra que sí esté en el mensaje original."
 
            # Antes de festejar, chequeamos si realmente borró algo
            filas_afectadas = conn.total_changes
            conn.commit()
            conn.close()
 
            if filas_afectadas > 0:
                return {"status": "success", "message": msg_si_borro}
            else:
                return {"status": "error", "message": msg_si_nada}
        
        elif accion in ["registrar_venta", "anotar_venta", "vendi"]:
            producto = req.parametro.replace("=", "").strip()
            diseno = req.parametro2.replace("=", "").strip()
            if not producto:
                return {"status": "error", "message": "Necesito saber qué producto vendiste."}
 
            fecha_hoy = datetime.now().strftime("%Y-%m-%d")
            hora_ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_LOCAL)
            cursor = conn.execute("INSERT INTO ventas_dia (producto, diseno, fecha, hora) VALUES (?, ?, ?, ?)", (producto, diseno, fecha_hoy, hora_ahora))
            venta_id = cursor.lastrowid
            conn.commit()
            conn.close()
 
            extra = f" con diseño '{diseno}'" if diseno else ""
            return {"status": "success", "venta_id": venta_id, "message": f"Venta #{venta_id} registrada: {producto}{extra}. Importe pendiente; no se confirmó ningún cobro."}
        
        # STOCK E INSUMOS
        elif accion == "actualizar_stock":
            nom_ins = param
            cant_str = req.parametro2.replace("=", "").strip()
            unidad = req.parametro3.replace("=", "").strip() or "unidades"
            alerta = extraer_numero(req.parametro4) if req.parametro4 else 5
            cant = extraer_numero(cant_str)
            if not nom_ins or cant is None: return {"status": "error", "message": "Falta nombre o cantidad."}

            conn = sqlite3.connect(DB_LOCAL)
            ins = conn.execute("SELECT cantidad FROM insumos WHERE LOWER(nombre) = LOWER(?)", (nom_ins,)).fetchone()
            if ins:
                ncant = ins[0] + cant if ("+" in cant_str or "-" in cant_str) else cant 
                conn.execute("UPDATE insumos SET cantidad = ? WHERE LOWER(nombre) = LOWER(?)", (ncant, nom_ins))
                msg = f"Stock de '{nom_ins}' actualizado a {ncant:g} {unidad}."
            else:
                conn.execute("INSERT INTO insumos (nombre, cantidad, unidad, alerta_minima) VALUES (?, ?, ?, ?)", (nom_ins, cant, unidad, alerta))
                msg = f"Insumo '{nom_ins}' registrado con {cant:g} {unidad}."
            conn.commit(); conn.close()
            return {"status": "success", "message": msg}

        elif accion == "consultar_stock":
            conn = sqlite3.connect(DB_LOCAL)
            ins = conn.execute("SELECT nombre, cantidad, unidad, alerta_minima FROM insumos ORDER BY nombre").fetchall()
            conn.close()
            if not ins: return {"status": "success", "message": "No tenés insumos registrados."}
            res = "📦 CONTROL DE STOCK:\n\n"
            for n, c, u, min_a in ins:
                res += f"⚠️ {n.upper()}: {c:g} {u} (Queda poco)\n" if c <= min_a else f"• {n}: {c:g} {u}\n"
            return {"status": "success", "message": res.strip()}

        # === FINANZAS (VERSIÓN DEFINITIVA 2.0) ===
        elif accion in ["registrar_pago", "fijar_deuda"]:
            id_txt = str(req.parametro).strip()
            pago = extraer_numero(req.parametro2) if req.parametro2 != "" else None
            total = extraer_numero(req.parametro3) if req.parametro3 != "" else None
            
            if pago is None and total is None:
                return {"status": "error", "message": "ERROR: Necesito los montos en números."}
            
            conn = sqlite3.connect(DB_LOCAL)
            id_pedido = None
            match_num = re.search(r'\d+', id_txt)
            if match_num:
                posible_id = int(match_num.group())
                if conn.execute("SELECT id FROM pedidos WHERE id = ?", (posible_id,)).fetchone():
                    id_pedido = posible_id

            if not id_pedido:
                cli_id = buscar_cliente_difuso(id_txt)
                if cli_id:
                    row = conn.execute("SELECT id FROM pedidos WHERE cliente_id = ? AND estado != 'entregado' ORDER BY id DESC", (cli_id,)).fetchone()
                    if row: id_pedido = row[0]
            
            # --- EL PARCHE ANTIBALAS ---
            if not id_pedido:
                # Si no hay pedido activo, creamos el cliente (si no existe) y un pedido genérico
                nuevo_cli_id = obtener_o_crear_cliente(id_txt)
                cursor = conn.execute("INSERT INTO pedidos (producto, estado, cliente_id, fecha_creacion) VALUES ('Trabajo a saldar', 'pendiente', ?, ?)", (nuevo_cli_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                id_pedido = cursor.lastrowid
                conn.commit()
                sena_act, tot_act = 0.0, 0.0
            else:
                # Si ya existía, traemos los datos viejos
                ped = conn.execute("SELECT sena_pagada, precio_total FROM pedidos WHERE id = ?", (id_pedido,)).fetchone()
                sena_act = ped[0] if ped[0] is not None else 0.0
                tot_act = ped[1] if ped[1] is not None else 0.0
            
            ntot = total if total is not None else tot_act
            
            if accion == "fijar_deuda":
                nsena = pago if pago is not None else sena_act
            else:
                nsena = sena_act + (pago if pago is not None else 0.0)
            
            conn.execute("UPDATE pedidos SET sena_pagada = ?, precio_total = ? WHERE id = ?", (nsena, ntot, id_pedido))
            conn.commit(); conn.close()
            
            resta = ntot - nsena
            return {"status": "success", "message": f"¡Listo! Pedido #{id_pedido} actualizado. Total: ${ntot:g} | Pagado: ${nsena:g} | Deuda actual: ${resta:g}"}
            
            # --- ACÁ ESTÁ LA MAGIA NUEVA ---
            if accion == "fijar_deuda":
                # MODO MARTILLAZO: Ignora la base de datos y sobreescribe los valores exactos
                nsena = pago if pago is not None else sena_act
            else:
                # MODO SUMA: Ideal para cuando el cliente trae plata nueva para achicar la deuda
                nsena = sena_act + (pago if pago is not None else 0.0)
            # -------------------------------
            
            conn.execute("UPDATE pedidos SET sena_pagada = ?, precio_total = ? WHERE id = ?", (nsena, ntot, id_pedido))
            conn.commit(); conn.close()
            
            resta = ntot - nsena
            return {"status": "success", "message": f"¡Listo! Pedido #{id_pedido} actualizado. Total: ${ntot:g} | Pagado: ${nsena:g} | Deuda actual: ${resta:g}"}

        elif accion == "consultar_deudas":
            conn = sqlite3.connect(DB_LOCAL)
            # Solo busca los que de verdad deben plata o dejaron seña sin precio fijo
            deudas = conn.execute("SELECT p.id, c.nombre, p.producto, p.precio_total, p.sena_pagada FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id WHERE (p.precio_total > 0 AND p.sena_pagada < p.precio_total) OR (p.precio_total = 0 AND p.sena_pagada > 0)").fetchall()
            conn.close()
            if not deudas: return {"status": "success", "message": "Nadie te debe plata."}
            lin = []; t_ad = 0
            for ip, cl, pr, tot, sen in deudas:
                tot = tot or 0.0; sen = sen or 0.0
                if tot > 0:
                    db = tot - sen
                    if db > 0: lin.append(f"• #{ip} {cl} ({pr}): Debe ${db:g} (Pagó ${sen:g})"); t_ad += db
                elif sen > 0: lin.append(f"• #{ip} {cl} ({pr}): Dejó seña ${sen:g} (Total sin definir)")
            return {"status": "success", "message": f"💸 REGISTRO DE DEUDAS (En la calle: ${t_ad:g}):\n\n" + "\n".join(lin)}

        # EXCEL
        elif accion in ["exportar_excel", "dame_el_excel"]:
            try:
                import pandas as pd
                conn = sqlite3.connect(DB_LOCAL)
                desktop = os.path.join(os.environ.get("USERPROFILE", "C:\\"), "Desktop")
                ex_path = os.path.join(desktop, f"SubliZen_BD_{int(time.time())}.xlsx")
                with pd.ExcelWriter(ex_path, engine='openpyxl') as w:
                    pd.read_sql_query("SELECT * FROM pedidos", conn).to_excel(w, sheet_name="Pedidos", index=False)
                    pd.read_sql_query("SELECT * FROM clientes", conn).to_excel(w, sheet_name="Clientes", index=False)
                    pd.read_sql_query("SELECT * FROM productos", conn).to_excel(w, sheet_name="Precios", index=False)
                    pd.read_sql_query("SELECT * FROM insumos", conn).to_excel(w, sheet_name="Stock", index=False)
                    pd.read_sql_query("SELECT * FROM recordatorios_recurrentes", conn).to_excel(w, sheet_name="Rutinas", index=False)
                conn.close()
                with open(ex_path, "rb") as d: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument", data={"chat_id": CHAT_ID, "caption": "📊 Base de datos en Excel."}, files={"document": d})
                if os.path.exists(ex_path): os.remove(ex_path)
                return {"status": "success", "message": "Excel enviado."}
            except Exception as e: return {"status": "error", "message": f"Error: {str(e)}"}

        # PEDIDOS GENERALES
        elif accion == "crear_pedido":
            cli_id = obtener_o_crear_cliente(req.parametro2.strip())
            f_lim = dateparser.parse(req.parametro4.strip(), languages=["es"], settings={"PREFER_DATES_FROM": "future"}).strftime("%Y-%m-%d") if req.parametro4.strip() and dateparser.parse(req.parametro4.strip(), languages=["es"], settings={"PREFER_DATES_FROM": "future"}) else ""
            conn = sqlite3.connect(DB_LOCAL)
            cursor = conn.execute("INSERT INTO pedidos (producto, estado, cliente_id, tareas_pendientes, fecha_creacion, fecha_entrega) VALUES (?, 'pendiente', ?, ?, ?, ?)", (param, cli_id, req.parametro3.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f_lim))
            pedido_id = cursor.lastrowid
            conn.commit(); conn.close()
            return {"status": "success", "pedido_id": pedido_id, "message": f"Pedido #{pedido_id} de '{param}' anotado. Fecha: {f_lim}."}

        elif accion == "reporte_pedidos":
            conn = sqlite3.connect(DB_LOCAL)
            pend = conn.execute("SELECT p.id, p.producto, p.tareas_pendientes, c.nombre, p.fecha_entrega FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id WHERE p.estado NOT IN ('entregado','cancelado') ORDER BY p.id").fetchall()
            conn.close()
            if not pend: return {"status": "success", "message": "No tenés pedidos pendientes."}
            return {"status": "success", "message": "📋 TRABAJOS PENDIENTES:\n\n" + "\n".join([f"• #{p[0]} {p[1]} — {p[3] or 'sin cliente'}" + (f" (Falta: {p[2]})" if p[2] else "") + (f" [Entrega: {p[4]}]" if p[4] else "") for p in pend])}
        
        elif accion == "agregar_producto":
            nombre_prod = req.parametro.replace("=", "").strip()
            precio_txt = extraer_numero(req.parametro2)
            if not nombre_prod or precio_txt is None:
                return {"status": "error", "message": "Necesito el nombre del producto y un precio válido."}
            pid = buscar_producto_exacto(nombre_prod)
            conn = sqlite3.connect(DB_LOCAL)
            if pid:
                costo_existente = conn.execute("SELECT costo_unitario FROM productos WHERE id = ?", (pid,)).fetchone()[0]
                costo_txt = extraer_numero(req.parametro3) if req.parametro3 else costo_existente
                conn.execute("UPDATE productos SET precio_unitario = ?, costo_unitario = ? WHERE id = ?", (precio_txt, costo_txt or 0, pid))
            else:
                costo_txt = extraer_numero(req.parametro3) if req.parametro3 else 0
                conn.execute("INSERT INTO productos (nombre, precio_unitario, costo_unitario) VALUES (?, ?, ?)", (nombre_prod, precio_txt, costo_txt or 0))
            conn.commit(); conn.close()
            extra = f" (costo: ${costo_txt:g})" if costo_txt else ""
            return {"status": "success", "message": f"Precio de '{nombre_prod}' guardado: ${precio_txt:g}{extra}."}
 
        elif accion == "cotizar":
            nombre_buscado = req.parametro.replace("=", "").strip()
            cantidad = extraer_numero(req.parametro2) if req.parametro2 else 1.0
            if not nombre_buscado:
                return {"status": "error", "message": "Necesito saber qué producto querés cotizar."}
            resultado = buscar_producto_difuso(nombre_buscado)
            if not resultado:
                return {"status": "error", "message": f"No tengo precio cargado para '{nombre_buscado}'."}
            pid, nombre_real, precio, costo = resultado
            cantidad = cantidad or 1.0
            total = precio * cantidad
            mensaje = f"💰 {int(cantidad) if cantidad == int(cantidad) else cantidad} x {nombre_real} = ${total:,.0f}"
            if costo:
                mensaje += f"\nGanancia estimada: ${(precio - costo) * cantidad:,.0f}"
            return {"status": "success", "message": mensaje}
 
        elif accion == "listar_precios":
            conn = sqlite3.connect(DB_LOCAL)
            productos = conn.execute("SELECT nombre, precio_unitario FROM productos ORDER BY nombre").fetchall()
            conn.close()
            if not productos:
                return {"status": "success", "message": "No tenés ningún precio cargado todavía."}
            lineas = [f"• {nombre}: ${precio:,.0f}" for nombre, precio in productos]
            return {"status": "success", "message": "📋 Lista de precios:\n\n" + "\n".join(lineas)}
        
            
        elif accion == "gestion_lote_pedidos":
            conn = sqlite3.connect(DB_LOCAL)
            campos = [
                str(req.parametro or "").strip(),
                str(req.parametro2 or "").strip(),
                str(req.parametro3 or "").strip(),
                str(req.parametro4 or "").strip(),
            ]
            texto_completo = " ".join(campo for campo in campos if campo)
            texto_lower = texto_completo.lower()
            numeros_con_marca = re.findall(r'#(\d+)', texto_completo)
            numeros_sueltos = [
                coincidencia.group(1)
                for campo in campos
                if (coincidencia := re.fullmatch(r'\s*#?(\d+)\s*', campo))
            ]
            numeros = list(dict.fromkeys(numeros_con_marca or numeros_sueltos))
            operacion = ""

            if any(k in texto_lower for k in ["elimina", "eliminar", "borra", "borrar", "saca", "quita", "volá", "vola"]):
                operacion = "eliminar"
            elif any(k in texto_lower for k in ["complet", "entreg", "termin"]):
                operacion = "completar"
            elif any(k in texto_lower for k in ["actualiz", "falta"]):
                operacion = "actualizar"

            if "|" in req.parametro and ":" in req.parametro:
                for inst in req.parametro.split("|"):
                    p = [x.strip() for x in inst.split(":", 2)]
                    if len(p) > 1 and p[1].replace("#", "").isdigit():
                        pid = int(p[1].replace("#", ""))
                        if p[0].lower() == "eliminar":
                            conn.execute("DELETE FROM pedidos WHERE id = ?", (pid,))
                        elif p[0].lower() == "completar":
                            conn.execute("UPDATE pedidos SET estado = 'entregado' WHERE id = ?", (pid,))
                        elif p[0].lower() == "actualizar" and len(p) >= 3:
                            conn.execute("UPDATE pedidos SET tareas_pendientes = ? WHERE id = ?", (p[2], pid))
            else:
                if operacion == "eliminar":
                    for n in numeros:
                        conn.execute("DELETE FROM pedidos WHERE id = ?", (int(n),))
                elif operacion == "completar":
                    for n in numeros:
                        conn.execute("UPDATE pedidos SET estado = 'entregado' WHERE id = ?", (int(n),))
                elif operacion == "actualizar" and numeros:
                    sub = re.sub(r'(?i)^.*?(?:pedido|trabajo)?\s*#?\d+\s*[:\-,]?\s*', '', texto_completo).strip()
                    if sub:
                        conn.execute("UPDATE pedidos SET tareas_pendientes = ? WHERE id = ?", (sub, int(numeros[0])))
 
            filas_afectadas = conn.total_changes
            conn.commit()
            conn.close()
 
            if filas_afectadas > 0:
                ids_texto = ", ".join(f"#{numero}" for numero in numeros)
                if operacion == "eliminar" and ids_texto:
                    return {"status": "success", "message": f"Listo, eliminé {ids_texto}."}
                if operacion == "completar" and ids_texto:
                    return {"status": "success", "message": f"Listo, marqué como entregados {ids_texto}."}
                return {"status": "success", "message": "Listo, cambios aplicados de verdad."}
            else:
                if not numeros:
                    return {"status": "error", "message": "No recibí ningún número de pedido. Decímelo con #, por ejemplo #10."}
                ids_texto = ", ".join(f"#{numero}" for numero in numeros)
                if not operacion:
                    return {"status": "error", "message": f"Entendí los pedidos {ids_texto}, pero no si querés eliminarlos, completarlos o actualizarlos."}
                return {"status": "error", "message": f"No encontré pedidos activos con estos números: {ids_texto}."}
        
        
        
        # === SEGUIMIENTO DE PARTIDOS (DINÁMICO) ===
        elif accion in ["seguir_partido", "seguir_partido_boca"]:
            equipo_input = param.lower().strip() if param.strip() else "boca juniors"
            nombre_oficial = normalizar_equipo(equipo_input)
            try:
                team_id = await asyncio.to_thread(obtener_id_equipo, nombre_oficial)
                if not team_id:
                    return {"status": "error", "message": f"No encontré el equipo '{equipo_input}'."}
                await asyncio.to_thread(FOOTBALL_WATCH.start, team_id, nombre_oficial)
            except FootballUnavailable as exc:
                return {"status": "error", "message": str(exc)}
            return {"status": "success", "message": f"Seguimiento activado para {nombre_oficial}, hasta el final o por un máximo de 3 horas. Si no encuentro partido en vivo, lo apagaré automáticamente."}

        elif accion == "detener_partido":
            await asyncio.to_thread(FOOTBALL_WATCH.stop)
            return {"status": "success", "message": "Modo partido desactivado. Apago el radar."}

        elif accion == "consultar_estado_partido":
            # Old exported workflows called the external API directly. Fail closed
            # until their request is migrated to the budgeted Core gateway.
            estado = await asyncio.to_thread(FOOTBALL_WATCH.status)
            return {**estado, "activo": False, "motivo": "usar_workflow_protegido"}

        elif accion == "consultar_estado_partido_protegido":
            return await asyncio.to_thread(FOOTBALL_WATCH.status)

        elif accion == "consultar_partido_protegido":
            return await asyncio.to_thread(FOOTBALL_WATCH.poll)

        elif accion == "pausar_entretiempo":
            try:
                await asyncio.to_thread(FOOTBALL_WATCH.pause)
            except FootballUnavailable as exc:
                return {"status": "error", "message": str(exc)}
            return {"status": "success", "message": "Radar puesto a dormir por 15 minutos. ¡A recargar los termos!"}
        elif accion in ["resumen_partido_vivo", "como_va_el_partido"]:
            return await asyncio.to_thread(FOOTBALL_WATCH.summary)
        
        elif accion == "marcar_entregado":
            pedido_id = extraer_numero(param)
            if pedido_id is None or pedido_id < 1 or not float(pedido_id).is_integer():
                return {"status": "error", "message": "Necesito el número de pedido, por ejemplo: #12."}
            pedido_id = int(pedido_id)
            conn = sqlite3.connect(DB_LOCAL)
            cursor = conn.execute(
                "UPDATE pedidos SET estado = 'entregado', tareas_pendientes = '' WHERE id = ?",
                (pedido_id,),
            )
            conn.commit()
            conn.close()
            if cursor.rowcount == 0:
                return {"status": "error", "message": f"No encontré el pedido #{pedido_id}."}
            return {"status": "success", "message": f"Pedido #{pedido_id} marcado como entregado."}
        
        elif accion == "chequear_partidos_dia":
            encontrados = []
            for nombre_eq in EQUIPOS_VIGILADOS:
                try:
                    tid = await asyncio.to_thread(obtener_id_equipo, nombre_eq)
                    if not tid:
                        continue
                    fixtures = await asyncio.to_thread(FOOTBALL_WATCH.daily_fixtures, tid)
                    if any(f.get("fixture", {}).get("status", {}).get("short") not in TERMINAL_STATUSES for f in fixtures):
                        encontrados.append(nombre_eq)
                except FootballUnavailable as exc:
                    return {"status": "error", "message": str(exc)}
            if encontrados:
                return {"status": "success", "message": f"Hoy tienen partido pendiente o en vivo: {', '.join(encontrados)}. El seguimiento no se activa solo; pedime seguir un equipo."}
            return {"status": "success", "message": "No encontré partidos pendientes o en vivo para los equipos vigilados hoy. El seguimiento no fue modificado."}
        
        # === CONTROL DE MÚSICA (YTM DESKTOP) ===
        elif accion in ["reproducir_musica", "pon_musica", "buscar_cancion", "buscar_musica", "musica", "reproducir", "poner_musica"]:
            return await asyncio.to_thread(buscar_y_reproducir_musica, param)

        elif accion in ["gustos_musicales", "perfil_musical", "que_musica_me_gusta"]:
            return await asyncio.to_thread(resumen_preferencias_musicales)
 
        elif accion in ["pausar_musica", "pausa", "continuar_musica", "play_musica"]:
            ok = await controlar_media("toggle")
            return {"status": "success" if ok else "error", "message": "Play/pausa alternado." if ok else "No encontré ninguna reproducción activa."}
 
        elif accion in ["siguiente_cancion", "pasar_cancion"]:
            ok = await controlar_media("next")
            return {"status": "success" if ok else "error", "message": "Pasé a la siguiente." if ok else "No pude cambiar de canción."}
 
        elif accion in ["anterior_cancion", "volver_cancion"]:
            ok = await controlar_media("previous")
            return {"status": "success" if ok else "error", "message": "Volví a la anterior." if ok else "No pude retroceder."}
 
        elif accion in ["estado_musica", "que_suena", "que_escuchamos", "que_esta_sonando"]:
            try:
                info = await obtener_info_media()
                if not info or not info["titulo"]:
                    return {"status": "success", "message": "No hay nada sonando en ningún lado ahora mismo."}
                return {"status": "success", "message": f"Está {info['estado']}: '{info['titulo']}' de {info['artista']} (desde {info['app']})."}
            except Exception as e:
                return {"status": "error", "message": f"No pude leer qué está sonando: {str(e)}"}
        
        # === VISOR DE IMÁGENES DE TELEGRAM ===
        elif accion in ["analizar_imagen", "ver_imagen", "ver_foto"]:
            file_id = param.strip()
            if not file_id:
                return {"status": "error", "message": "No me pasaste el file_id de Telegram."}
                
            try:
                print(f"📷 [VISION] Solicitando archivo {file_id[:15]}... a Telegram")
                
                # 1. Pedirle a Telegram la ruta interna
                url_getFile = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
                res_path_raw = requests.get(url_getFile)
                res_path = res_path_raw.json()
                
                if not res_path.get("ok"):
                    print(f"❌ [ERROR TELEGRAM GETFILE]: {res_path}")
                    return {"status": "error", "message": f"Telegram rechazó el archivo: {res_path.get('description', 'Error desconocido')}"}
                    
                file_path = res_path["result"]["file_path"]
                print(f"📷 [VISION] Ruta de descarga encontrada: {file_path}")
                
                # 2. Descargar la imagen
                url_descarga = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                res_img = requests.get(url_descarga)
                
                base_dir = os.path.dirname(os.path.abspath(__file__))
                carpeta_img = os.path.join(base_dir, "imagenes")
                os.makedirs(carpeta_img, exist_ok=True)
                
                nombre_archivo = f"Telegram_{int(time.time())}.jpg"
                ruta_guardado = os.path.join(carpeta_img, nombre_archivo)
                
                with open(ruta_guardado, "wb") as f:
                    f.write(res_img.content)
                
                print(f"📷 [VISION] Foto guardada en {ruta_guardado}. Analizando con Gemini...")
                
                # 3. Leerla para el cerebro de Gemini
                img = Image.open(ruta_guardado)
                
                # 4. Análisis visual
                prompt = "Sos Rebecca. Analizá esta imagen que te mandó el usuario por Telegram. Describila brevemente con tu estilo cyberpunk y sarcástico. Si parece un diseño, logo o dibujo, tirá algún comentario sobre cómo quedaría sublimado."
                if GEMINI_CLIENT is None:
                    raise RuntimeError("Falta GEMINI_API_KEY para analizar imágenes.")
                respuesta = GEMINI_CLIENT.models.generate_content(
                    model=GEMINI_VISION_MODEL,
                    contents=[prompt, img],
                )
                
                return {"status": "success", "message": f"¡Foto recibida y guardada! Esto es lo que vi: {respuesta.text.strip()}"}
                
            except Exception as e:
                print(f"❌ [VISION ERROR FATAL]: {e}")
                return {"status": "error", "message": f"Explotó el visor óptico: {str(e)}"}
        
        # --- MODO NO MOLESTAR ---
        elif accion == "activar_no_molestar":
            global MODO_NO_MOLESTAR
            MODO_NO_MOLESTAR = True
            if param:
                fecha_fin = dateparser.parse(param, languages=["es"], settings={"PREFER_DATES_FROM": "future"}) \
                    or dateparser.parse(f"en {param}", languages=["es"], settings={"PREFER_DATES_FROM": "future"})
                if fecha_fin:
                    segundos = max(0, (fecha_fin - datetime.now()).total_seconds())
                    def _auto_desactivar(seg):
                        time.sleep(seg)
                        global MODO_NO_MOLESTAR
                        MODO_NO_MOLESTAR = False
                    threading.Thread(target=_auto_desactivar, args=(segundos,), daemon=True).start()
                    return {"status": "success", "message": f"No molestar activado hasta las {fecha_fin.strftime('%H:%M')}."}
            return {"status": "success", "message": "No molestar activado. Avisame cuando lo saque."}
 
        elif accion == "desactivar_no_molestar":
            MODO_NO_MOLESTAR = False
            return {"status": "success", "message": "No molestar desactivado. Volví al ruido de siempre."}
 
        elif accion == "estado_no_molestar":
            return {"status": "success", "message": f"No molestar está {'ACTIVADO' if MODO_NO_MOLESTAR else 'desactivado'}."}
 
        # --- AGENDA DE TURNOS ---
        elif accion == "crear_turno":
            nombre_turno = req.parametro.replace("=", "").strip()
            motivo_turno = req.parametro2.replace("=", "").strip()
            fecha_texto = req.parametro3.replace("=", "").strip()
 
            if not nombre_turno or not fecha_texto:
                return {"status": "error", "message": "Necesito el nombre y cuándo es el turno."}
 
            fecha_parseada = dateparser.parse(fecha_texto, languages=["es"], settings={"PREFER_DATES_FROM": "future"})
            if not fecha_parseada:
                return {"status": "error", "message": f"No entendí la fecha/hora '{fecha_texto}'."}
 
            conn = sqlite3.connect(DB_LOCAL)
            conn.execute(
                "INSERT INTO turnos (nombre, motivo, fecha_hora) VALUES (?, ?, ?)",
                (nombre_turno, motivo_turno, fecha_parseada.strftime("%Y-%m-%d %H:%M"))
            )
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"Turno anotado: {nombre_turno} el {fecha_parseada.strftime('%d/%m a las %H:%M')}."}
 
        elif accion == "consultar_turnos":
            conn = sqlite3.connect(DB_LOCAL)
            proximos = conn.execute(
                "SELECT id, nombre, motivo, fecha_hora FROM turnos WHERE estado = 'pendiente' AND fecha_hora >= ? ORDER BY fecha_hora ASC",
                (datetime.now().strftime("%Y-%m-%d %H:%M"),)
            ).fetchall()
            conn.close()
            if not proximos:
                return {"status": "success", "message": "No tenés turnos agendados."}
            lineas = [f"• #{id_} {nombre}{' — ' + motivo if motivo else ''} — {fh}" for id_, nombre, motivo, fh in proximos]
            return {"status": "success", "message": "📅 **Turnos:**\n\n" + "\n".join(lineas)}
 
        elif accion == "cancelar_turno":
            id_turno = req.parametro.replace("=", "").replace("#", "").strip()
            if not id_turno.isdigit():
                return {"status": "error", "message": "Necesito el número de turno."}
            conn = sqlite3.connect(DB_LOCAL)
            conn.execute("UPDATE turnos SET estado = 'cancelado' WHERE id = ?", (int(id_turno),))
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"Turno #{id_turno} cancelado."}
 
        elif accion == "completar_turno":
            id_turno = req.parametro.replace("=", "").replace("#", "").strip()
            if not id_turno.isdigit():
                return {"status": "error", "message": "Necesito el número de turno."}
            conn = sqlite3.connect(DB_LOCAL)
            conn.execute("UPDATE turnos SET estado = 'cumplido' WHERE id = ?", (int(id_turno),))
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"Turno #{id_turno} marcado como cumplido."}
 
        # --- MODO PÁNICO ---
        elif accion in ["activar_panico", "modo_panico"]:
            try:
                pyautogui.press("volumedown", presses=50)  # fuerza el volumen a 0 sin importar el estado previo
 
                pyautogui.hotkey("ctrl", "win", "d")  # crea un escritorio virtual nuevo y cambia a él
                time.sleep(0.6)
 
                os.startfile(os.path.expanduser("~"))  # abre el Explorador
                time.sleep(0.4)
 
                if "youtube" in param:
                    webbrowser.open("https://www.youtube.com")
                else:
                    webbrowser.open("https://www.ambito.com/economia")
 
                return {"status": "success", "message": "Listo."}
            except Exception as e:
                return {"status": "error", "message": f"Fallo el modo pánico: {str(e)}"}
 
        elif accion == "desactivar_panico":
            try:
                pyautogui.hotkey("ctrl", "win", "left")  # vuelve al escritorio original
                return {"status": "success", "message": "De vuelta."}
            except Exception as e:
                return {"status": "error", "message": f"No pude volver: {str(e)}"}
        
                # Ampliar noticia
        elif accion in ["explicar_noticia", "leer_noticia", "ampliar_noticia"]:
            tema = param.strip()
            if not tema:
                return {"status": "error", "message": "Decime sobre qué titular o tema querés que profundice."}
            payload = {
                "contents": [{"parts": [{"text": (
                    "Buscá información actual sobre esta noticia o tema. Corregí errores obvios "
                    "del titular, contrastá los datos y respondé en español con un resumen claro "
                    f"de 4 a 6 oraciones, sin inventar información: {tema}"
                )}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 600},
            }
            modelos = ("gemini-3.5-flash", "gemini-3.5-flash-lite")
            ultimo_codigo = None
            for modelo in modelos:
                try:
                    url_gemini = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
                    res = await asyncio.to_thread(
                        requests.post,
                        url_gemini,
                        json=payload,
                        timeout=8,
                    )
                    ultimo_codigo = res.status_code
                    if res.status_code == 200:
                        partes = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        texto = "\n".join(
                            str(parte.get("text", "")).strip()
                            for parte in partes
                            if parte.get("text")
                        ).strip()
                        if texto:
                            return {"status": "success", "message": texto}
                    print(f"[EXPLICAR NOTICIA] {modelo} devolvió {res.status_code}: {res.text[:300]}")
                    if res.status_code not in (429, 500, 502, 503, 504):
                        break
                except requests.Timeout:
                    print(f"[EXPLICAR NOTICIA] {modelo} superó el límite de 8 segundos.")
                except Exception as e:
                    print(f"[EXPLICAR NOTICIA] {modelo} falló: {str(e)[:200]}")

            # Respaldo sin tokens: Google News RSS mantiene la función disponible
            # aunque ambos modelos hayan agotado cuota o estén temporalmente lentos.
            try:
                consulta_rss = re.sub(
                    r"\btriunfazo\s+af[oó]nico\b",
                    "triunfazo agónico",
                    tema,
                    flags=re.IGNORECASE,
                )
                respuesta_rss = await asyncio.to_thread(
                    requests.get,
                    "https://news.google.com/rss/search",
                    params={"q": consulta_rss, "hl": "es-419", "gl": "AR", "ceid": "AR:es-419"},
                    headers={"User-Agent": "Rebecca-Core/1.0"},
                    timeout=6,
                )
                respuesta_rss.raise_for_status()
                raiz = ET.fromstring(respuesta_rss.content)
                resultados = []
                for item_rss in raiz.findall(".//item"):
                    titulo = unescape((item_rss.findtext("title") or "").strip())
                    enlace = (item_rss.findtext("link") or "").strip()
                    fecha = (item_rss.findtext("pubDate") or "").strip()
                    if titulo and enlace:
                        resultados.append((titulo, enlace, fecha))
                    if len(resultados) == 3:
                        break
                if resultados:
                    lineas = [
                        "La IA de resumen está sin cuota por ahora, pero encontré estas notas relacionadas y actuales:"
                    ]
                    for indice, (titulo, enlace, fecha) in enumerate(resultados, 1):
                        detalle_fecha = f" ({fecha})" if fecha else ""
                        lineas.append(f"{indice}. {titulo}{detalle_fecha}\n{enlace}")
                    return {"status": "success", "message": "\n\n".join(lineas)}
            except Exception as e:
                print(f"[EXPLICAR NOTICIA] El respaldo RSS falló: {str(e)[:200]}")

            if ultimo_codigo == 429:
                return {"status": "error", "message": "La cuota de noticias está agotada y no pude recuperar fuentes alternativas por ahora."}
            return {"status": "error", "message": "No pude ampliar la noticia dentro del tiempo esperado. Probá nuevamente en unos segundos."}
        
        # -----------------------------------------
        # BUSCAR Y ENVIAR ARCHIVOS (Documentos + Escritorio, recursivo)
        # -----------------------------------------
        elif accion == "buscar_archivo":
            busqueda_raw = req.parametro.strip()
            if not busqueda_raw:
                return {"status": "error", "message": "Decime el nombre del archivo que buscás."}
 
            ext_conocidas = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.psd', '.png', '.jpg', '.jpeg', '.txt', '.zip', '.rar')
            busqueda_lower = busqueda_raw.lower()
            ext_pedida = None
            nombre_base = busqueda_lower
            for ext in ext_conocidas:
                if busqueda_lower.endswith(ext):
                    ext_pedida = ext
                    nombre_base = busqueda_lower[:-len(ext)]
                    break
 
            user_profile = os.environ.get("USERPROFILE", "C:\\")
            carpetas = [os.path.join(user_profile, "Documents"), os.path.join(user_profile, "Desktop")]
 
            candidatos = []
            for carpeta in carpetas:
                if not os.path.exists(carpeta): continue
                for root, _, files in os.walk(carpeta):
                    for file in files:
                        file_lower = file.lower()
                        file_stem, file_ext = os.path.splitext(file_lower)
                        if ext_pedida and file_ext != ext_pedida:
                            continue
                        if nombre_base == file_stem and (not ext_pedida or file_ext == ext_pedida):
                            score = 1.0
                        elif nombre_base in file_stem:
                            score = 0.9
                        else:
                            score = difflib.SequenceMatcher(None, nombre_base, file_stem).ratio()
                        if score >= 0.45:
                            candidatos.append((score, os.path.join(root, file)))
 
            if not candidatos:
                return {"status": "error", "message": f"No encontré nada parecido a '{busqueda_raw}' en Documentos ni Escritorio."}
 
            candidatos.sort(key=lambda x: x[0], reverse=True)
            mejor_score, archivo_encontrado = candidatos[0]
            nombre_archivo = os.path.basename(archivo_encontrado)
 
            if mejor_score < 1.0:
                return {
                    "status": "success",
                    "message": f"No encontré '{busqueda_raw}' exacto. Lo más parecido es '{nombre_archivo}'. PAUSA. Preguntale al usuario '¿Quisiste decir {nombre_archivo}?'. Si confirma, volvé a llamar a buscar_archivo con '{nombre_archivo}' tal cual, extensión incluida."
                }
 
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
                with open(archivo_encontrado, "rb") as doc:
                    res = requests.post(url, data={"chat_id": CHAT_ID}, files={"document": doc})
                if res.status_code == 200:
                    return {"status": "success", "message": f"Ahí va '{nombre_archivo}'."}
                return {"status": "error", "message": f"Encontré el archivo pero Telegram lo rechazó: {res.text[:150]}"}
            except Exception as e:
                return {"status": "error", "message": f"Encontré '{nombre_archivo}' pero no pude enviarlo: {str(e)}"}

        # -----------------------------------------
        # IMPRESIÓN DIRECTA DE PDF
        # parametro = nombre del archivo
        # parametro2 = doble o simple
        # -----------------------------------------
        elif accion == "imprimir_pdf_telegram":
            file_id = param.strip()
            nombre_original = req.parametro2.strip() or "documento.pdf"
            modo = req.parametro3.strip().lower() or "simple"
            chat_origen = req.parametro4.strip()

            if not CHAT_ID or chat_origen != str(CHAT_ID):
                return {"status": "error", "message": "Rechacé la impresión: el PDF no vino del chat autorizado."}
            if modo not in ["simple", "doble", "doble faz"]:
                modo = "simple"

            try:
                archivo_encontrado = descargar_pdf_telegram(file_id, nombre_original)
                enviar_pdf_a_impresora(archivo_encontrado)
                return {
                    "status": "success",
                    "message": (
                        f"Recibí '{os.path.basename(archivo_encontrado)}' y lo mandé a la impresora normal "
                        f"con la configuración actual de Windows. Modo solicitado: {modo}."
                    ),
                }
            except requests.RequestException as e:
                return {"status": "error", "message": f"No pude descargar el PDF desde Telegram: {str(e)}"}
            except (ValueError, OSError) as e:
                return {"status": "error", "message": f"No pude imprimir el PDF: {str(e)}"}

        elif accion == "imprimir_pdf":
            busqueda_raw = param.strip()
            modo = req.parametro2.strip().lower()

            if not busqueda_raw:
                return {"status": "error", "message": "Falta el nombre del PDF a imprimir."}

            if not busqueda_raw.endswith(".pdf"):
                busqueda_raw += ".pdf"

            user_profile = os.environ.get("USERPROFILE", "C:\\")
            carpetas = [os.path.join(user_profile, "Documents"), os.path.join(user_profile, "Desktop")]
            archivo_encontrado = None

            for carpeta in carpetas:
                if not os.path.exists(carpeta): continue
                for root, _, files in os.walk(carpeta):
                    for file in files:
                        if busqueda_raw.lower() in file.lower():
                            archivo_encontrado = os.path.join(root, file)
                            break
                    if archivo_encontrado: break
                if archivo_encontrado: break

            if not archivo_encontrado:
                return {"status": "error", "message": f"No encontré '{busqueda_raw}' en la compu."}

            try:
                # Dispara la impresión silenciosa al default de Windows
                enviar_pdf_a_impresora(archivo_encontrado)
                return {"status": "success", "message": f"Mandé '{os.path.basename(archivo_encontrado)}' a la cola de impresión normal. Modo solicitado: {modo or 'simple'}; Windows usará la configuración actual de la impresora."}
            except Exception as e:
                return {"status": "error", "message": f"El spooler de Windows rebotó la impresión: {str(e)}"}
        
        
        # -----------------------------------------
        # GENERAR Y ENVIAR REPORTE EN PDF
        # parametro = "precios" / "deudores" / "pendientes"
        # -----------------------------------------
        elif accion == "generar_reporte_pdf":
            tipo = param.lower()
            try:
                if any(k in tipo for k in ["precio", "catalogo"]):
                    archivo = generar_pdf_precios()
                elif any(k in tipo for k in ["deudor", "deuda"]):
                    archivo = generar_pdf_deudores()
                elif any(k in tipo for k in ["pendiente", "trabajo", "falta"]):
                    archivo = generar_pdf_pendientes()
                else:
                    return {"status": "error", "message": "Decime si querés el PDF de precios, deudores o trabajos pendientes."}
 
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
                with open(archivo, "rb") as f:
                    res = requests.post(url, data={"chat_id": CHAT_ID}, files={"document": f})
                os.remove(archivo)
                if res.status_code == 200:
                    return {"status": "success", "message": "PDF enviado."}
                return {"status": "error", "message": f"El PDF se generó pero Telegram lo rechazó: {res.text[:150]}"}
            except Exception as e:
                return {"status": "error", "message": f"Error generando el PDF: {str(e)}"}
        
        
        # FINAL
        elif accion in ["observar_pantalla", "ver_pantalla", "que_hago"]:
            try:
                comentario = await asyncio.wait_for(
                    asyncio.to_thread(analizar_pantallas_rebecca),
                    timeout=10.5,
                )
            except asyncio.TimeoutError:
                return {"status": "error", "message": "La lectura visual tardó demasiado; descarté esa captura vieja."}
            return {"status": "success", "message": comentario}
        elif accion in ["opinar_juego", "modo_espectadora_opinar"]:
            try:
                comentario = await asyncio.wait_for(
                    asyncio.to_thread(
                        analizar_pantallas_rebecca,
                        para_audio=True,
                        contexto=param,
                        modo_juego=True,
                    ),
                    timeout=GAME_VISION_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                return {
                    "status": "error",
                    "message": "El modelo visual no respondió a tiempo; reintentaré en la próxima escena.",
                }
            if comentario.startswith("Error"):
                return {"status": "error", "message": comentario}
            return {"status": "success", "message": comentario}
        elif accion in ["observar_juego_contexto", "capturar_contexto_juego"]:
            try:
                observacion = await asyncio.wait_for(
                    asyncio.to_thread(
                        analizar_pantallas_rebecca,
                        para_audio=False,
                        contexto=param,
                        modo_juego=True,
                        solo_hechos=True,
                    ),
                    timeout=13.0,
                )
            except asyncio.TimeoutError:
                return {"status": "error", "message": "La escena tardó demasiado en analizarse y ya no era actual."}
            if observacion.startswith("Error"):
                return {"status": "error", "message": observacion}
            return {"status": "success", "message": observacion}
        elif accion in ["hablar_pc", "hablar_en_pc"]:
            if not param:
                return {"status": "error", "message": "Falta el texto que Rebecca debe decir."}
            try:
                dispositivo = int(req.parametro2) if req.parametro2.strip() else None
            except ValueError:
                dispositivo = None
            if req.parametro3.strip():
                try:
                    expires_at = float(req.parametro3)
                    if not 0 < expires_at < float("inf"):
                        raise ValueError("deadline inválido")
                except ValueError:
                    return {"status": "error", "message": "El plazo del comentario no es válido."}
                return await asyncio.to_thread(
                    generar_y_reproducir_audio_pc, param, dispositivo, expires_at=expires_at
                )
            return await asyncio.to_thread(generar_y_reproducir_audio_pc, param, dispositivo)
        elif accion == "detener_voz_pc":
            try:
                sd.stop()
            except Exception:
                pass
            if winsound:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:
                    pass
            return {"status": "success", "message": "Detuve la voz local."}
        elif accion == "activar_observador": BECCA_OJO_ACTIVO = True; return {"status": "success", "message": "Ojo activo."}
        elif accion == "desactivar_observador": BECCA_OJO_ACTIVO = False; return {"status": "success", "message": "Ojo desactivado."}
        elif accion in ["hablar", "manda_audio", "nota_de_voz"]: return await generar_y_enviar_audio(param or "¡Acá estoy, choom!")
        elif accion in ["resumen_diario", "dame_el_reporte"]: threading.Thread(target=enviar_resumen_arranque, kwargs={"forzar": True}, daemon=True).start(); return {"status": "success", "message": "Generando reporte..."}
        else: return {"status": "error", "message": f"La acción '{accion}' no existe."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        demora = time.perf_counter() - inicio_comando
        if demora >= 1.0:
            print(f"[RENDIMIENTO] Acción lenta: {accion} demoró {demora:.2f}s")


ACCIONES_SUBLIZEN_CLIENTES = {
    "listar_precios", "cotizar", "crear_presupuesto_cliente", "confirmar_presupuesto_cliente",
    "consultar_pedido_cliente", "responder_diseno_cliente",
    "configurar_entrega_cliente", "solicitar_atencion_humana", "registrar_satisfaccion_cliente",
    "registrar_comprobante_transferencia", "guardar_email_y_enviar_comprobante",
    "reintentar_comprobante",
}


@app.post("/ejecutar/sublizen")
async def ejecutar_comando_sublizen(req: ComandoMaster):
    accion = normalizar_accion(req.accion)
    if accion not in ACCIONES_SUBLIZEN_CLIENTES:
        raise HTTPException(status_code=403, detail=f"La acción '{accion}' no está habilitada para clientes.")
    return await ejecutar_comando_maestro(req)


@app.post("/sublizen/estado-atencion")
async def estado_atencion_sublizen(datos: EstadoAtencionModel):
    """El puente consulta esto antes de n8n; si hay operador humano, Rebecca guarda silencio."""
    numero = datos.numero.strip()
    if not normalizar_whatsapp(numero):
        raise HTTPException(status_code=400, detail="Número de WhatsApp inválido.")
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    clientes = conn.execute("SELECT id, telefono, whatsapp, modo_humano FROM clientes").fetchall()
    encontrado = next((fila for fila in clientes if whatsapp_coincide(fila[2] or fila[1], numero)), None)
    if encontrado:
        cliente_id, _, _, modo_humano = encontrado
        conn.execute("UPDATE clientes SET ultima_interaccion=? WHERE id=?", (ahora, cliente_id))
    else:
        nombre_base = datos.nombre.strip() or f"Cliente {normalizar_whatsapp(numero)[-4:]}"
        cliente_id = obtener_o_crear_cliente(nombre_base)
        conn.execute("UPDATE clientes SET telefono=?, whatsapp=?, ultima_interaccion=? WHERE id=?", (numero, numero, ahora, cliente_id))
        modo_humano = 0
    conn.commit(); conn.close()
    return {"status": "success", "cliente_id": int(cliente_id), "modo_humano": bool(modo_humano), "responder_automaticamente": not bool(modo_humano)}



@app.get("/health")
async def health_check():
    try:
        conn = sqlite3.connect(DB_LOCAL, timeout=10)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return {
            "status": "ok",
            "servicio": "rebecca-core",
            "version": CORE_VERSION,
            "capacidades": [
        "observar_juego_contexto",
        "capturar_pantalla_local",
                "hablar_pc",
                "imprimir_pdf_telegram",
                "abrir_programa",
                "venta_sena_50",
                "comprobante_email_whatsapp",
                "comprobante_transferencia_verificado",
                "reintentos_documentos",
                "seguimiento_privado_en_vivo",
                "presupuestos_confirmables",
                "aprobacion_diseno",
                "atencion_humana",
                "notificaciones_por_estado",
                "postventa",
            ],
        }
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Base de datos no disponible: {exc}")


@app.get("/dashboard")
async def dashboard_page():
    dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    if not os.path.exists(dashboard_path):
        raise HTTPException(status_code=404, detail="No se encontró dashboard.html")
    return FileResponse(dashboard_path, media_type="text/html")


@app.get("/dashboard/data")
async def dashboard_data():
    conn = sqlite3.connect(DB_LOCAL)
    conn.row_factory = sqlite3.Row
 
    pedidos = conn.execute("""
        SELECT p.id, p.producto, p.tareas_pendientes, p.fecha_entrega,
               p.precio_total, p.sena_pagada, p.estado, c.nombre as cliente,
               p.seguimiento_token, p.cantidad, p.diseno_estado, p.modalidad_entrega,
               p.direccion_entrega, p.turno_entrega, c.id AS cliente_id,
               COALESCE(NULLIF(c.whatsapp,''), c.telefono, '') AS whatsapp,
               COALESCE(c.modo_humano,0) AS modo_humano,
               (SELECT cp.id FROM comprobantes_pago cp WHERE cp.pedido_id=p.id ORDER BY cp.id DESC LIMIT 1) AS comprobante_pago_id,
               COALESCE((SELECT cp.estado FROM comprobantes_pago cp WHERE cp.pedido_id=p.id ORDER BY cp.id DESC LIMIT 1), 'sin_comprobante') AS comprobante_pago_estado,
               COALESCE((SELECT cp.nombre_original FROM comprobantes_pago cp WHERE cp.pedido_id=p.id ORDER BY cp.id DESC LIMIT 1), '') AS comprobante_pago_nombre,
               COALESCE((SELECT cp.recibido_en FROM comprobantes_pago cp WHERE cp.pedido_id=p.id ORDER BY cp.id DESC LIMIT 1), '') AS comprobante_pago_recibido,
               COALESCE((SELECT cp.observaciones FROM comprobantes_pago cp WHERE cp.pedido_id=p.id ORDER BY cp.id DESC LIMIT 1), '') AS comprobante_pago_observaciones,
               COALESCE((SELECT estado FROM envios_documentos e WHERE e.pedido_id=p.id AND e.tipo='comprobante_sena' AND e.canal='email'), 'pendiente') AS comprobante_email,
               COALESCE((SELECT estado FROM envios_documentos e WHERE e.pedido_id=p.id AND e.tipo='comprobante_sena' AND e.canal='whatsapp'), 'pendiente') AS comprobante_whatsapp
        FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id
        WHERE p.estado NOT IN ('entregado', 'cancelado')
        ORDER BY (p.fecha_entrega = '' OR p.fecha_entrega IS NULL), p.fecha_entrega ASC
    """).fetchall()
 
    deudores = conn.execute("""
        SELECT c.nombre as cliente, p.id as pedido_id, p.producto,
               (p.precio_total - p.sena_pagada) as deuda,
               p.tareas_pendientes, p.fecha_entrega, p.precio_total,
               p.sena_pagada, p.estado
        FROM pedidos p JOIN clientes c ON p.cliente_id = c.id
        WHERE p.precio_total > p.sena_pagada AND p.estado != 'cancelado'
        ORDER BY deuda DESC
    """).fetchall()
 
    insumos = conn.execute("SELECT id, nombre, cantidad, unidad, alerta_minima FROM insumos ORDER BY nombre").fetchall()
    productos = conn.execute("SELECT id, nombre, precio_unitario, costo_unitario FROM productos ORDER BY nombre").fetchall()
    
    # ¡Agregamos la lectura de rutinas acá!
    rutinas = conn.execute("SELECT id, mensaje, dia_semana, hora FROM recordatorios_recurrentes").fetchall()
 
    mes_actual = datetime.now().strftime("%Y-%m")
    facturado_mes = conn.execute(
        "SELECT COALESCE(SUM(precio_total), 0) FROM pedidos WHERE estado = 'entregado' AND fecha_creacion LIKE ?",
        (f"{mes_actual}%",)
    ).fetchone()[0]
 

    total_adeudado = sum(row["deuda"] for row in deudores)
    insumos_bajos = sum(1 for i in insumos if i["cantidad"] <= i["alerta_minima"])
    comprobantes_pendientes = conn.execute(
        "SELECT COUNT(*) FROM comprobantes_pago WHERE estado IN ('pendiente','monto_inconsistente')"
    ).fetchone()[0]
    envios_fallidos = conn.execute(
        "SELECT COUNT(*) FROM envios_documentos WHERE estado='error' AND intentos < 5"
    ).fetchone()[0]
    try:
        bridge_response = requests.get(f"{WHATSAPP_BRIDGE_URL}/health", timeout=1.5)
        whatsapp_estado = "conectado" if bridge_response.status_code == 200 else "desconectado"
    except requests.RequestException:
        whatsapp_estado = "desconectado"
    
    recordatorios = conn.execute("SELECT id, mensaje, fecha_hora FROM recordatorios WHERE enviado = 0 ORDER BY fecha_hora ASC").fetchall()
    atenciones_humanas = conn.execute(
        "SELECT id, nombre, COALESCE(NULLIF(whatsapp,''),telefono,'') AS whatsapp, modo_humano_desde, ultima_interaccion FROM clientes WHERE modo_humano=1 ORDER BY modo_humano_desde"
    ).fetchall()
    presupuestos_pendientes = conn.execute(
        """
        SELECT pr.id, c.nombre AS cliente, pr.producto, pr.cantidad, pr.total, pr.creado_en, pr.vence_en
        FROM presupuestos pr JOIN clientes c ON c.id=pr.cliente_id
        WHERE pr.estado='pendiente' ORDER BY pr.creado_en DESC LIMIT 30
        """
    ).fetchall()
    hoy = datetime.now().strftime("%Y-%m-%d")
    alertas = []
    for fila in pedidos:
        p = dict(fila)
        if p["comprobante_pago_estado"] in {"pendiente", "monto_inconsistente"}:
            alertas.append({"tipo": "pago", "prioridad": "alta", "pedido_id": p["id"], "mensaje": f"Revisar el pago del pedido #{p['id']} — {p['cliente'] or 'sin cliente'}"})
        if p["diseno_estado"] == "cambios_solicitados":
            alertas.append({"tipo": "diseno", "prioridad": "media", "pedido_id": p["id"], "mensaje": f"El pedido #{p['id']} tiene cambios de diseño pendientes"})
        if p["fecha_entrega"] and p["fecha_entrega"] < hoy and p["estado"] not in {"listo", "entregado", "cancelado"}:
            alertas.append({"tipo": "atraso", "prioridad": "alta", "pedido_id": p["id"], "mensaje": f"Pedido #{p['id']} atrasado desde {p['fecha_entrega']}"})
    for cliente in atenciones_humanas:
        alertas.append({"tipo": "humano", "prioridad": "alta", "cliente_id": cliente["id"], "mensaje": f"{cliente['nombre']} espera atención humana"})
    
    pedidos_publicos = []
    for fila in pedidos:
        pedido = dict(fila)
        token = pedido.pop("seguimiento_token", "")
        pedido["seguimiento_url"] = f"{PUBLIC_BASE_URL}/seguimiento/{token}" if token else ""
        pedidos_publicos.append(pedido)

    resultado = {
        "pedidos": pedidos_publicos,
        "deudores": [dict(r) for r in deudores],
        "insumos": [dict(r) for r in insumos],
        "productos": [dict(r) for r in productos],
        "rutinas": [dict(r) for r in rutinas],
        "recordatorios": [dict(r) for r in recordatorios], # <--- Agregá esta línea
        "atenciones_humanas": [dict(r) for r in atenciones_humanas],
        "presupuestos": [dict(r) for r in presupuestos_pendientes],
        "alertas": alertas,
        "diagnostico": {
            "core": "activo",
            "correo": "configurado" if EMAIL_USUARIO and EMAIL_PASSWORD else "incompleto",
            "whatsapp": whatsapp_estado,
            "url_publica": "configurada" if PUBLIC_BASE_URL.startswith("https://") else "revisar",
            "url_estable": "no" if "ngrok" in PUBLIC_BASE_URL.lower() else "si",
            "comprobantes_pendientes": comprobantes_pendientes,
            "envios_fallidos": envios_fallidos,
            "atenciones_humanas": len(atenciones_humanas),
            "alertas_activas": len(alertas),
        },
        "resumen": {
            "pedidos_pendientes": len(pedidos),
            "facturado_mes": facturado_mes,
            "total_adeudado": total_adeudado,
            "insumos_bajos": insumos_bajos
        }
    }
    conn.close()
    return resultado


@app.post("/dashboard/editar_pedido")
async def editar_pedido_dashboard(datos: EditarPedidoModel):
    campos, valores = [], []

    if datos.estado is not None:
        estado = datos.estado.strip().lower()
        if estado not in {"pendiente", "esperando_sena", "diseno_pendiente", "en_proceso", "listo", "entregado", "cancelado"}:
            raise HTTPException(status_code=400, detail="Estado de pedido inválido.")
        campos.append("estado = ?")
        valores.append(estado)

    if datos.tareas_pendientes is not None:
        campos.append("tareas_pendientes = ?")
        valores.append(datos.tareas_pendientes.strip())

    if datos.fecha_entrega is not None:
        fecha_entrega = datos.fecha_entrega.strip()
        if fecha_entrega:
            try:
                datetime.strptime(fecha_entrega, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail="La fecha debe tener formato AAAA-MM-DD.")
        campos.append("fecha_entrega = ?")
        valores.append(fecha_entrega)

    for campo in ["precio_total", "sena_pagada"]:
        valor = getattr(datos, campo)
        if valor is not None:
            if valor < 0:
                raise HTTPException(status_code=400, detail="Los importes no pueden ser negativos.")
            campos.append(f"{campo} = ?")
            valores.append(valor)

    if datos.diseno_estado is not None:
        diseno_estado = datos.diseno_estado.strip().lower()
        if diseno_estado not in {"pendiente", "enviado", "cambios_solicitados", "aprobado", "no_requiere"}:
            raise HTTPException(status_code=400, detail="Estado de diseño inválido.")
        campos.append("diseno_estado = ?")
        valores.append(diseno_estado)

    if datos.modalidad_entrega is not None:
        modalidad = datos.modalidad_entrega.strip().lower()
        if modalidad not in {"a_confirmar", "retiro", "envio"}:
            raise HTTPException(status_code=400, detail="Modalidad de entrega inválida.")
        campos.append("modalidad_entrega = ?")
        valores.append(modalidad)

    if datos.direccion_entrega is not None:
        campos.append("direccion_entrega = ?")
        valores.append(datos.direccion_entrega.strip()[:500])

    if datos.turno_entrega is not None:
        campos.append("turno_entrega = ?")
        valores.append(datos.turno_entrega.strip()[:120])

    if not campos:
        raise HTTPException(status_code=400, detail="No hay cambios para guardar.")

    campos.append("actualizado_en = ?")
    valores.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    anterior = conn.execute(
        "SELECT estado, tareas_pendientes, diseno_estado, modalidad_entrega, automatizaciones_habilitadas FROM pedidos WHERE id=?",
        (datos.id,),
    ).fetchone()
    if not anterior:
        conn.close()
        raise HTTPException(status_code=404, detail=f"No existe el pedido #{datos.id}.")
    if datos.estado is not None and estado == "en_proceso" and anterior[4] and (datos.diseno_estado or anterior[2]) not in {"aprobado", "no_requiere"}:
        conn.close()
        raise HTTPException(status_code=400, detail="Primero el cliente debe aprobar el diseño, o marcá que este trabajo no requiere diseño.")
    valores.append(datos.id)
    cursor = conn.execute(f"UPDATE pedidos SET {', '.join(campos)} WHERE id = ?", valores)
    cambios = []
    if datos.estado is not None and estado != anterior[0]:
        cambios.append(f"Estado: {anterior[0]} → {estado}")
    if datos.diseno_estado is not None and diseno_estado != anterior[2]:
        cambios.append(f"Diseño: {anterior[2]} → {diseno_estado}")
    if datos.modalidad_entrega is not None and modalidad != anterior[3]:
        cambios.append(f"Entrega: {anterior[3]} → {modalidad}")
    if datos.tareas_pendientes is not None and datos.tareas_pendientes.strip() != (anterior[1] or ""):
        cambios.append(f"Tarea actualizada: {datos.tareas_pendientes.strip()}")
    if cambios:
        registrar_historial_pedido(conn, datos.id, "pedido_actualizado", "; ".join(cambios), "operador")
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"No existe el pedido #{datos.id}.")
    if datos.estado is not None and estado != anterior[0]:
        textos_estado = {
            "esperando_sena": f"El pedido #{datos.id} quedó reservado. Estamos esperando la seña para confirmarlo.",
            "diseno_pendiente": f"Estamos preparando el diseño del pedido #{datos.id}. Te enviaremos una muestra para que la apruebes antes de producir.",
            "en_proceso": f"¡El pedido #{datos.id} entró en producción! Podés seguir su avance desde el QR de tu comprobante.",
            "listo": f"¡Tu pedido #{datos.id} ya está listo! Te contactaremos para coordinar el retiro o la entrega.",
            "entregado": f"El pedido #{datos.id} figura como entregado. ¡Gracias por elegir SubliZen! En breve te pediremos una opinión.",
            "cancelado": f"El pedido #{datos.id} fue cancelado. Si necesitás ayuda, respondé este mensaje y te atenderá el equipo.",
        }
        if estado in textos_estado:
            notificar_evento_pedido(datos.id, f"estado_{estado}", textos_estado[estado])
    if datos.diseno_estado == "enviado" and datos.diseno_estado != anterior[2]:
        notificar_evento_pedido(
            datos.id, "diseno_enviado",
            f"Te enviamos la muestra del diseño para el pedido #{datos.id}. Respondé APROBAR si está correcto o indicá los cambios. No empezaremos a producir hasta tener tu aprobación.",
        )
    return {"status": "success", "message": f"Pedido #{datos.id} actualizado.", "cambios": cambios}


@app.post("/dashboard/aprobar_comprobante")
async def aprobar_comprobante_dashboard(datos: RevisarComprobanteModel):
    resultado = revisar_comprobante_pago(
        datos.pedido_id, True, datos.comprobante_id, datos.observaciones
    )
    if resultado.get("status") == "error":
        raise HTTPException(status_code=400, detail=resultado.get("message", "No se pudo aprobar."))
    return resultado


@app.post("/dashboard/rechazar_comprobante")
async def rechazar_comprobante_dashboard(datos: RevisarComprobanteModel):
    resultado = revisar_comprobante_pago(
        datos.pedido_id, False, datos.comprobante_id, datos.observaciones
    )
    if resultado.get("status") == "error":
        raise HTTPException(status_code=400, detail=resultado.get("message", "No se pudo rechazar."))
    return resultado


@app.post("/dashboard/cambiar_atencion")
async def cambiar_atencion_dashboard(datos: CambiarAtencionModel):
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    cursor = conn.execute(
        "UPDATE clientes SET modo_humano=?, modo_humano_desde=? WHERE id=?",
        (1 if datos.modo_humano else 0, ahora if datos.modo_humano else "", datos.cliente_id),
    )
    conn.commit(); conn.close()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="No existe ese cliente.")
    estado = "humana" if datos.modo_humano else "automática"
    return {"status": "success", "message": f"Atención {estado} configurada."}


@app.get("/dashboard/historial/{pedido_id}")
async def historial_pedido_dashboard(pedido_id: int):
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    conn.row_factory = sqlite3.Row
    existe = conn.execute("SELECT id FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not existe:
        conn.close()
        raise HTTPException(status_code=404, detail=f"No existe el pedido #{pedido_id}.")
    eventos = conn.execute(
        "SELECT id, evento, descripcion, actor, creado_en FROM historial_pedidos WHERE pedido_id=? ORDER BY id DESC LIMIT 100",
        (pedido_id,),
    ).fetchall()
    conn.close()
    return {"pedido_id": pedido_id, "eventos": [dict(evento) for evento in eventos]}


@app.post("/dashboard/enviar_diseno")
async def enviar_diseno_dashboard(datos: EnviarDisenoModel):
    tipos = {
        "image/jpeg": (".jpg", lambda b: b[:3] == b"\xff\xd8\xff"),
        "image/png": (".png", lambda b: b[:8] == b"\x89PNG\r\n\x1a\n"),
        "image/webp": (".webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
        "application/pdf": (".pdf", lambda b: b[:5] == b"%PDF-"),
    }
    mime = datos.mime.lower().strip()
    if mime not in tipos:
        raise HTTPException(status_code=400, detail="Usá JPG, PNG, WEBP o PDF para la muestra.")
    contenido = datos.contenido_base64.split(",", 1)[-1]
    try:
        archivo = base64.b64decode(contenido, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="El archivo de diseño no es válido.")
    if not archivo or len(archivo) > MAX_COMPROBANTE_BYTES:
        raise HTTPException(status_code=400, detail="El diseño está vacío o supera 12 MB.")
    extension, validar_firma = tipos[mime]
    if not validar_firma(archivo):
        raise HTTPException(status_code=400, detail="El contenido no coincide con el formato declarado.")

    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    pedido = conn.execute(
        """
        SELECT COALESCE(NULLIF(c.whatsapp,''),c.telefono,''), p.producto
        FROM pedidos p LEFT JOIN clientes c ON c.id=p.cliente_id WHERE p.id=?
        """,
        (datos.pedido_id,),
    ).fetchone()
    if not pedido:
        conn.close()
        raise HTTPException(status_code=404, detail=f"No existe el pedido #{datos.pedido_id}.")
    if not str(pedido[0] or "").strip():
        conn.close()
        raise HTTPException(status_code=400, detail="El cliente no tiene WhatsApp guardado.")
    carpeta = os.path.join(DISENOS_CLIENTES_DIR, f"pedido_{datos.pedido_id}")
    os.makedirs(carpeta, exist_ok=True)
    digest = hashlib.sha256(archivo).hexdigest()
    ruta = os.path.join(carpeta, f"{digest[:20]}{extension}")
    if not os.path.exists(ruta):
        with open(ruta, "xb") as salida:
            salida.write(archivo)
    caption = datos.mensaje.strip() or f"Muestra de diseño del pedido #{datos.pedido_id}. Respondé APROBAR o indicá los cambios que necesitás."
    try:
        respuesta = requests.post(
            f"{WHATSAPP_BRIDGE_URL}/enviar_archivo",
            json={"numero": pedido[0], "ruta_archivo": ruta, "caption": caption},
            timeout=25,
        )
    except requests.RequestException as exc:
        conn.close()
        raise HTTPException(status_code=502, detail=f"No se pudo contactar el puente de WhatsApp: {exc}")
    if respuesta.status_code != 200:
        conn.close()
        raise HTTPException(status_code=502, detail=f"WhatsApp no pudo enviar el diseño: {respuesta.text[:300]}")
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE pedidos SET diseno_estado='enviado', diseno_referencia=?, tareas_pendientes='Esperando aprobación del diseño', actualizado_en=? WHERE id=?",
        (ruta, ahora, datos.pedido_id),
    )
    registrar_historial_pedido(conn, datos.pedido_id, "diseno_enviado", f"Muestra enviada: {datos.nombre[:180]}", "operador")
    conn.commit(); conn.close()
    return {"status": "success", "message": f"Diseño del pedido #{datos.pedido_id} enviado. Rebecca esperará la aprobación o los cambios."}


@app.post("/dashboard/reintentar_comprobante")
async def reintentar_comprobante_dashboard(datos: EliminarModel):
    resultado = emitir_y_enviar_comprobante(datos.id)
    if resultado.get("status") in {"error", "awaiting_payment_verification"}:
        raise HTTPException(status_code=400, detail=resultado.get("message", "No se pudo reintentar."))
    return resultado


@app.get("/dashboard/comprobante/{comprobante_id}")
async def ver_comprobante_dashboard(comprobante_id: int):
    conn = sqlite3.connect(DB_LOCAL, timeout=10)
    fila = conn.execute(
        "SELECT ruta_archivo, mime, nombre_original FROM comprobantes_pago WHERE id = ?",
        (int(comprobante_id),),
    ).fetchone()
    conn.close()
    if not fila:
        raise HTTPException(status_code=404, detail="No existe ese comprobante.")
    ruta, mime, nombre = fila
    ruta_segura = ruta_comprobante_permitida(ruta)
    if not ruta_segura or not os.path.isfile(ruta_segura):
        raise HTTPException(status_code=404, detail="El archivo del comprobante ya no está disponible.")
    return FileResponse(ruta_segura, media_type=mime, filename=nombre or os.path.basename(ruta_segura), content_disposition_type="inline")

@app.post("/dashboard/editar_producto")
async def editar_producto_dashboard(datos: EditarProductoModel):
    campos, valores = [], []
    for campo in ["precio_unitario", "costo_unitario"]:
        valor = getattr(datos, campo)
        if valor is not None:
            campos.append(f"{campo} = ?")
            valores.append(valor)
    if not campos:
        return {"status": "error", "message": "Nada para actualizar."}
    valores.append(datos.id)
    conn = sqlite3.connect(DB_LOCAL)
    conn.execute(f"UPDATE productos SET {', '.join(campos)} WHERE id = ?", valores)
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Producto #{datos.id} actualizado."}

@app.post("/dashboard/eliminar_recordatorio")
async def eliminar_recordatorio_dashboard(datos: EliminarModel):
    conn = sqlite3.connect(DB_LOCAL)
    conn.execute("DELETE FROM recordatorios WHERE id = ?", (datos.id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Recordatorio eliminado."}

@app.post("/dashboard/eliminar_producto")
async def eliminar_producto_dashboard(datos: EliminarModel):
    conn = sqlite3.connect(DB_LOCAL)
    conn.execute("DELETE FROM productos WHERE id = ?", (datos.id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Producto eliminado."}

@app.post("/dashboard/eliminar_rutina")
async def eliminar_rutina_dashboard(datos: EliminarModel):
    conn = sqlite3.connect(DB_LOCAL)
    conn.execute("DELETE FROM recordatorios_recurrentes WHERE id = ?", (datos.id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Rutina eliminada."}

@app.post("/dashboard/editar_rutina")
async def editar_rutina_dashboard(datos: EditarRutinaModel):
    conn = sqlite3.connect(DB_LOCAL)
    conn.execute("UPDATE recordatorios_recurrentes SET dia_semana = ?, hora = ?, mensaje = ? WHERE id = ?", 
                 (datos.dia_semana, datos.hora, datos.mensaje, datos.id))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Rutina actualizada."}

@app.post("/dashboard/guardar_insumo")
async def guardar_insumo_dashboard(datos: InsumoModel):
    conn = sqlite3.connect(DB_LOCAL)
    if datos.id > 0:
        conn.execute("UPDATE insumos SET nombre=?, cantidad=?, unidad=?, alerta_minima=? WHERE id=?",
                     (datos.nombre, datos.cantidad, datos.unidad, datos.alerta_minima, datos.id))
    else:
        conn.execute("INSERT INTO insumos (nombre, cantidad, unidad, alerta_minima) VALUES (?, ?, ?, ?)",
                     (datos.nombre, datos.cantidad, datos.unidad, datos.alerta_minima))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Insumo guardado."}

@app.post("/dashboard/eliminar_insumo")
async def eliminar_insumo_dashboard(datos: EliminarModel):
    conn = sqlite3.connect(DB_LOCAL)
    conn.execute("DELETE FROM insumos WHERE id = ?", (datos.id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Insumo eliminado."}

def datos_seguimiento_publico(token):
    conn = sqlite3.connect(DB_LOCAL)
    pedido = conn.execute(
        """
        SELECT p.id, p.producto, p.estado, p.tareas_pendientes, p.actualizado_en, c.nombre,
               p.sena_confirmada, p.precio_total, p.sena_pagada, p.fecha_entrega,
               p.diseno_estado, p.modalidad_entrega, p.turno_entrega,
               COALESCE((
                   SELECT cp.estado FROM comprobantes_pago cp
                   WHERE cp.pedido_id = p.id ORDER BY cp.id DESC LIMIT 1
               ), 'sin_comprobante') AS comprobante_estado
        FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.id
        WHERE p.seguimiento_token = ?
        """,
        (str(token or ""),),
    ).fetchone()
    conn.close()
    if not pedido:
        return None
    (pedido_id, producto, estado, tareas, actualizado, cliente, sena_confirmada,
     precio_total, sena_pagada, fecha_entrega, diseno_estado, modalidad_entrega,
     turno_entrega, comprobante_estado) = pedido
    partes_nombre = str(cliente or "Cliente").split()
    cliente_publico = partes_nombre[0]
    if len(partes_nombre) > 1:
        cliente_publico += f" {partes_nombre[-1][0]}."
    if estado == "cancelado":
        estado_etiqueta, progreso, pago_estado = "Pedido cancelado", 0, "cancelado"
    elif estado == "entregado":
        estado_etiqueta, progreso, pago_estado = "Pedido entregado", 100, "acreditado"
    elif estado == "listo":
        estado_etiqueta, progreso, pago_estado = "Listo para entregar", 90, "acreditado"
    elif not sena_confirmada and comprobante_estado in {"pendiente", "monto_inconsistente"}:
        estado_etiqueta, progreso, pago_estado = "Pago enviado — en verificación", 25, "en_revision"
    elif not sena_confirmada and comprobante_estado == "rechazado":
        estado_etiqueta, progreso, pago_estado = "Esperando un nuevo comprobante", 10, "rechazado"
    elif not sena_confirmada:
        estado_etiqueta, progreso, pago_estado = "Esperando la seña del 50%", 10, "pendiente"
    elif diseno_estado == "enviado":
        estado_etiqueta, progreso, pago_estado = "Esperando aprobación del diseño", 42, "acreditado"
    elif diseno_estado == "cambios_solicitados":
        estado_etiqueta, progreso, pago_estado = "Corrigiendo el diseño", 45, "acreditado"
    elif diseno_estado == "pendiente":
        estado_etiqueta, progreso, pago_estado = "Preparando el diseño", 38, "acreditado"
    elif estado == "en_proceso":
        estado_etiqueta, progreso, pago_estado = "En producción", 60, "acreditado"
    else:
        estado_etiqueta, progreso, pago_estado = "Seña acreditada — esperando producción", 40, "acreditado"

    pago_etiquetas = {
        "pendiente": "Seña pendiente", "en_revision": "Comprobante en revisión",
        "rechazado": "Comprobante rechazado", "acreditado": "Seña acreditada",
        "cancelado": "Pedido cancelado",
    }
    total = max(0.0, float(precio_total or 0))
    sena = max(0.0, float(sena_pagada or 0))
    return {
        "pedido_id": pedido_id, "producto": producto or "Pedido personalizado",
        "estado": estado or "pendiente", "estado_etiqueta": estado_etiqueta,
        "tareas": tareas or "El equipo de SubliZen está trabajando en tu pedido.",
        "actualizado_en": actualizado or "", "cliente": cliente_publico, "progreso": progreso,
        "pago_estado": pago_estado, "pago_etiqueta": pago_etiquetas[pago_estado],
        "precio_total": total, "sena_pagada": sena, "saldo_pendiente": max(0.0, total - sena),
        "fecha_entrega": fecha_entrega or "A confirmar",
        "diseno_estado": diseno_estado or "pendiente",
        "diseno_etiqueta": {
            "pendiente": "Pendiente de preparación", "enviado": "Esperando tu aprobación",
            "cambios_solicitados": "Cambios solicitados", "aprobado": "Aprobado",
            "no_requiere": "No requiere diseño",
        }.get(diseno_estado or "pendiente", diseno_estado or "Pendiente"),
        "modalidad_entrega": modalidad_entrega or "a_confirmar",
        "entrega_etiqueta": {
            "retiro": "Retiro por el local", "envio": "Envío a domicilio",
            "a_confirmar": "A confirmar",
        }.get(modalidad_entrega or "a_confirmar", "A confirmar"),
        "turno_entrega": turno_entrega or "A coordinar",
    }


@app.get("/api/seguimiento/{token}")
async def api_seguimiento_pedido(token: str):
    pedido = datos_seguimiento_publico(token)
    if not pedido:
        raise HTTPException(status_code=404, detail="No encontramos ese seguimiento.")
    return pedido


@app.get("/seguimiento/{token}", response_class=HTMLResponse)
async def ver_seguimiento_pedido(token: str):
    pedido = datos_seguimiento_publico(token)

    if not pedido:
        raise HTTPException(status_code=404, detail="No encontramos ese seguimiento.")
    token_seguro = escape(str(token))
    html_content = f"""
    <!doctype html><html lang="es">
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>SubliZen - Estado del Pedido</title>
            <style>
                * {{ box-sizing: border-box; }}
                body {{ margin:0; min-height:100vh; background:#0f172a; color:#f8fafc; font-family:Arial,sans-serif; display:grid; place-items:center; padding:20px; }}
                .caja {{ width:min(520px,100%); border:1px solid #334155; border-radius:20px; padding:26px; background:#111827; box-shadow:0 24px 60px #0008; }}
                .marca {{ color:#34d399; font-weight:800; letter-spacing:.12em; }}
                .estado {{ color:#6ee7b7; font-size:28px; font-weight:800; margin:8px 0; }}
                .detalle,.actualizado {{ color:#94a3b8; font-size:14px; }}
                .barra {{ height:12px; background:#243044; border-radius:999px; overflow:hidden; margin:18px 0; }}
                .avance {{ height:100%; width:0; background:linear-gradient(90deg,#10b981,#22d3ee); transition:width .5s ease; }}
                .datos {{ background:#0b1220; padding:16px; border-radius:12px; text-align:left; line-height:1.6; }}
                .vivo {{ color:#34d399; font-size:12px; }}
            </style>
        </head>
        <body>
            <div class="caja">
                <div class="marca">SUBLIZEN</div>
                <h2>Seguimiento del pedido <span id="pedido"></span></h2>
                <p class="vivo">● Actualización automática</p>
                <p class="detalle">ESTADO ACTUAL</p>
                <div class="estado" id="estado">Cargando...</div>
                <div class="barra"><div class="avance" id="avance"></div></div>
                 <div class="datos">
                     <div><b>Cliente:</b> <span id="cliente"></span></div>
                     <div><b>Producto:</b> <span id="producto"></span></div>
                     <div><b>Pago:</b> <span id="pago"></span></div>
                     <div><b>Total:</b> <span id="total"></span></div>
                     <div><b>Seña acreditada:</b> <span id="sena"></span></div>
                     <div><b>Saldo pendiente:</b> <span id="saldo"></span></div>
                     <div><b>Entrega estimada:</b> <span id="entrega"></span></div>
                     <div><b>Diseño:</b> <span id="diseno"></span></div>
                     <div><b>Modalidad:</b> <span id="modalidad"></span></div>
                     <div><b>Turno:</b> <span id="turno"></span></div>
                     <div><b>Avance:</b> <span id="tareas"></span></div>
                </div>
                <p class="actualizado" id="actualizado"></p>
            </div>
            <script>
                const endpoint = '/api/seguimiento/{token_seguro}';
                async function actualizar() {{
                    try {{
                        const r = await fetch(endpoint, {{ cache: 'no-store' }});
                        if (!r.ok) throw new Error('Seguimiento no disponible');
                        const d = await r.json();
                        document.getElementById('pedido').textContent = '#' + d.pedido_id;
                        document.getElementById('estado').textContent = d.estado_etiqueta;
                         document.getElementById('cliente').textContent = d.cliente;
                         document.getElementById('producto').textContent = d.producto;
                         const pesos = valor => new Intl.NumberFormat('es-AR', {{ style:'currency', currency:'ARS' }}).format(valor || 0);
                         document.getElementById('pago').textContent = d.pago_etiqueta;
                         document.getElementById('total').textContent = pesos(d.precio_total);
                         document.getElementById('sena').textContent = pesos(d.sena_pagada);
                         document.getElementById('saldo').textContent = pesos(d.saldo_pendiente);
                         document.getElementById('entrega').textContent = d.fecha_entrega;
                         document.getElementById('diseno').textContent = d.diseno_etiqueta;
                         document.getElementById('modalidad').textContent = d.entrega_etiqueta;
                         document.getElementById('turno').textContent = d.turno_entrega;
                        document.getElementById('tareas').textContent = d.tareas;
                        document.getElementById('avance').style.width = d.progreso + '%';
                        document.getElementById('actualizado').textContent = d.actualizado_en ? 'Última actualización: ' + d.actualizado_en : '';
                    }} catch (_) {{
                        document.getElementById('actualizado').textContent = 'No pudimos actualizar. Reintentando...';
                    }}
                }}
                actualizar(); setInterval(actualizar, 15000);
            </script>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/estado/{pedido_id}")
async def ver_estado_pedido_legacy(pedido_id: int):
    token = obtener_token_seguimiento(pedido_id)
    if not token:
        raise HTTPException(status_code=404, detail="Este pedido no existe en SubliZen.")
    return RedirectResponse(url=f"/seguimiento/{token}", status_code=307)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
