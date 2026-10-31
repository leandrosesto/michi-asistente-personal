"""Acciones que el cerebro puede ejecutar en la PC."""
import difflib
import email
import imaplib
import json
import logging
import os
import re
import subprocess
import threading
import unicodedata
import urllib.parse
import webbrowser
import winreg
from email.header import decode_header, make_header

import keyboard
import requests

from . import config

log = logging.getLogger(__name__)


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []},
    }}


SCHEMAS = [
    _fn("abrir_web", "Abre una página web en el navegador.",
        {"url": {"type": "string", "description": "Dirección, ej. https://www.mercadolibre.com.ar"}}, ["url"]),
    _fn("buscar_en_google", "Abre una búsqueda de Google en el navegador para que el usuario la vea.",
        {"consulta": {"type": "string"}}, ["consulta"]),
    _fn("buscar_info", "Busca en internet y devuelve resultados para que vos respondas con datos actuales (noticias, clima, precios, dólar, resultados, etc.).",
        {"consulta": {"type": "string"}}, ["consulta"]),
    _fn("poner_musica", "Busca y reproduce música o un video en YouTube (canción, artista, playlist, género).",
        {"busqueda": {"type": "string"}}, ["busqueda"]),
    _fn("control_multimedia", "Controla la reproducción y el volumen de la PC.",
        {"accion": {"type": "string", "enum": ["pausa_play", "siguiente", "anterior", "subir_volumen", "bajar_volumen", "silenciar"]}}, ["accion"]),
    _fn("abrir_aplicacion", "Abre un programa instalado (Chrome, WhatsApp, Telegram, VLC, Calculadora, Word, Spotify, etc.).",
        {"nombre": {"type": "string"}}, ["nombre"]),
    _fn("abrir_carpeta", "Abre el Explorador de archivos en una carpeta: descargas, documentos, escritorio, imagenes, musica, videos, este equipo o una ruta.",
        {"carpeta": {"type": "string"}}, ["carpeta"]),
    _fn("revisar_mails", "Lee remitente y asunto de los mails no leídos de Gmail.",
        {"cantidad": {"type": "integer", "description": "Cuántos mails como máximo (default 5)"}}),
    _fn("redactar_mail", "Abre un borrador en Gmail para que el usuario lo revise y lo envíe él mismo. Nunca envía.",
        {"para": {"type": "string"}, "asunto": {"type": "string"}, "cuerpo": {"type": "string"}}, ["asunto", "cuerpo"]),
    _fn("esconderse", "Esconde al gato de la pantalla (cuando te piden 'escondete', 'andate', 'desaparecé'). Vuelve con el atajo de teclado o pidiéndolo por Telegram."),
    _fn("aparecer", "Vuelve a mostrar al gato en la pantalla si estaba escondido."),
    _fn("consultar_claude", "Le pasa a Claude (la IA de Anthropic) un pedido que necesita más cabeza: redactar textos o mails, "
        "explicar algo a fondo, planificar, revisar o escribir código, analizar. Usalo cuando te lo piden ('preguntale a Claude') "
        "o cuando la tarea supera una respuesta corta. Devuelve la respuesta; resumila en voz alta en una o dos oraciones.",
        {"pedido": {"type": "string", "description": "El pedido completo y bien explicado, con todo el contexto necesario"}},
        ["pedido"]),
    _fn("recordar", "Guarda en la memoria a largo plazo un dato duradero sobre el usuario.",
        {"dato": {"type": "string", "description": "Frase corta en tercera persona, ej. 'Le gusta el rock nacional'"},
         "reemplaza": {"type": "string", "description": "Si corrige o repite un dato que ya sabés, ese dato viejo tal cual figura en tu memoria"}},
        ["dato"]),
    _fn("olvidar", "Borra de la memoria a largo plazo un dato que el usuario pide olvidar.",
        {"que": {"type": "string", "description": "Palabras del dato a borrar"}}, ["que"]),
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def abrir_web(app, url):
    if not re.match(r"^https?://", url):
        url = "https://" + url.lstrip("/")
    webbrowser.open(url)
    return f"Abierto {url}"


def buscar_en_google(app, consulta):
    webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(consulta))
    return "Búsqueda abierta en el navegador"


def buscar_info(app, consulta):
    from ddgs import DDGS
    results = DDGS().text(consulta, region="ar-es", max_results=5)
    if not results:
        return "Sin resultados"
    return "\n".join(f"- {r.get('title')}: {r.get('body')}" for r in results)


def poner_musica(app, busqueda):
    html = requests.get(
        "https://www.youtube.com/results", params={"search_query": busqueda},
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "es-AR,es;q=0.9"}, timeout=10,
    ).text
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html)
    if not m:
        webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(busqueda))
        return "No encontré un video exacto, abrí la búsqueda en YouTube"
    title = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', html[m.end():m.end() + 3000])
    webbrowser.open(f"https://www.youtube.com/watch?v={m.group(1)}")
    return "Reproduciendo: " + (json.loads(f'"{title.group(1)}"') if title else busqueda)


MEDIA_KEYS = {
    "pausa_play": ("play/pause media", 1), "siguiente": ("next track", 1), "anterior": ("previous track", 1),
    "subir_volumen": ("volume up", 5), "bajar_volumen": ("volume down", 5), "silenciar": ("volume mute", 1),
}


def control_multimedia(app, accion):
    key, times = MEDIA_KEYS[accion]
    for _ in range(times):
        keyboard.send(key)
    return "Hecho"


_apps: dict[str, str] = {}
_apps_ready = threading.Event()


def preload_apps():
    """Lista de programas del menú Inicio (nombre -> AppID), igual que los que ves en Windows."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.decode("utf-8", "replace")
        for item in json.loads(out or "[]"):
            _apps[item["Name"]] = item["AppID"]
        log.info("%d aplicaciones del menú Inicio", len(_apps))
    except Exception:
        log.exception("No pude listar las aplicaciones")
    _apps_ready.set()


def abrir_aplicacion(app, nombre):
    _apps_ready.wait(30)
    by_norm = {_norm(n): n for n in _apps}
    q = _norm(nombre)
    match = next((by_norm[k] for k in by_norm if k == q), None)
    match = match or next((by_norm[k] for k in sorted(by_norm, key=len) if q in k), None)
    if not match:
        close = difflib.get_close_matches(q, by_norm.keys(), n=1, cutoff=0.55)
        match = by_norm[close[0]] if close else None
    if not match:
        similar = difflib.get_close_matches(q, by_norm.keys(), n=4, cutoff=0.3)
        return f"No encontré '{nombre}'. Parecidos: {', '.join(by_norm[s] for s in similar) or 'ninguno'}"
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{_apps[match]}"])
    return f"Abriendo {match}"


SHELL_FOLDERS = {
    "descargas": "{374DE290-123F-4565-9164-39C4925E467B}", "documentos": "Personal", "escritorio": "Desktop",
    "imagenes": "My Pictures", "fotos": "My Pictures", "musica": "My Music", "videos": "My Video",
}


def abrir_carpeta(app, carpeta):
    q = _norm(carpeta)
    if q in ("explorador", "este equipo", "mi pc", "explorador de archivos", ""):
        subprocess.Popen(["explorer.exe"])
        return "Explorador abierto"
    for name, value in SHELL_FOLDERS.items():
        if name in q:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                path = os.path.expandvars(winreg.QueryValueEx(k, value)[0])
            subprocess.Popen(["explorer.exe", path])
            return f"Abierta la carpeta {name}"
    if os.path.isdir(carpeta):
        subprocess.Popen(["explorer.exe", carpeta])
        return f"Abierta {carpeta}"
    return f"No conozco la carpeta '{carpeta}'"


def _header(value):
    return str(make_header(decode_header(value or ""))).strip()


def revisar_mails(app, cantidad=5):
    user = app.cfg.get("gmail_usuario")
    password = config.get_secret("GMAIL_APP_PASSWORD")
    if not user or not password:
        return "Gmail no está configurado: falta el mail o la contraseña de aplicación en la configuración."
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(user, password.replace(" ", ""))
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "UNSEEN")
        ids = data[0].split()
        if not ids:
            return "No hay mails sin leer"
        lines = [f"{len(ids)} sin leer. Los más recientes:"]
        for mid in reversed(ids[-max(1, min(int(cantidad), 10)):]):
            _, msg_data = imap.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            msg = email.message_from_bytes(msg_data[0][1])
            sender = re.sub(r"\s*<.*?>", "", _header(msg["From"])).strip('" ')
            lines.append(f"- De {sender}: {_header(msg['Subject'])}")
        return "\n".join(lines)


def redactar_mail(app, asunto, cuerpo, para=""):
    params = urllib.parse.urlencode({"view": "cm", "fs": "1", "to": para, "su": asunto, "body": cuerpo})
    webbrowser.open("https://mail.google.com/mail/?" + params)
    return "Borrador abierto en Gmail, falta que el usuario lo revise y lo envíe"


def esconderse(app):
    # Se esconde recién después de contestar, así se escucha la despedida
    app.hide_after_reply = True
    return f"Listo, me escondo al terminar de hablar. Para volver: {app.cfg['atajo']} o pedirlo por Telegram"


def aparecer(app):
    app.hide_after_reply = False
    app.show()
    return "Visible otra vez en la pantalla"


def consultar_claude(app, pedido):
    from . import claude_link
    return claude_link.ask(pedido)


def recordar(app, dato, reemplaza=""):
    return app.brain.memory.add(dato, reemplaza)


def olvidar(app, que):
    return app.brain.memory.forget(que)


FUNCS = {f.__name__: f for f in (abrir_web, buscar_en_google, buscar_info, poner_musica, control_multimedia,
                                 abrir_aplicacion, abrir_carpeta, revisar_mails, redactar_mail, esconderse, aparecer,
                                 consultar_claude, recordar, olvidar)}


def run(name: str, args_json: str, app) -> str:
    fn = FUNCS.get(name)
    if not fn:
        return f"Error: no existe la herramienta {name}"
    try:
        args = json.loads(args_json or "{}") or {}
    except json.JSONDecodeError:
        args = {}
    try:
        result = str(fn(app, **args))
    except Exception as e:
        log.exception("Herramienta %s falló", name)
        result = f"Error: {e}"
    log.info("tool %s(%s) -> %s", name, args, result[:200])
    return result
