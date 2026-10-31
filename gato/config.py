"""Configuración del gato: apariencia y voz en config.json, claves en el Administrador de credenciales de Windows."""
import json
import os
import threading
from pathlib import Path

import keyring

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
KEYRING_SERVICE = "gato-asistente"

# Claves que se guardan en el Administrador de credenciales (nunca en config.json)
SECRET_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "GMAIL_APP_PASSWORD", "ANTHROPIC_API_KEY")

# Alias de versiones anteriores de la configuración
VOICES = {
    "mujer": "es-AR-ElenaNeural",
    "varon": "es-AR-TomasNeural",
}

# Voces de edge-tts agrupadas para la configuración: (grupo, [(id, etiqueta), ...])
VOICE_GROUPS = [
    ("Local, en tu PC (la más rápida, anda sin internet)", [
        ("piper:es_AR-daniela-high", "Daniela · mujer · Argentina"),
    ]),
    ("Rioplatenses", [
        ("es-AR-ElenaNeural", "Elena · mujer · Argentina"),
        ("es-AR-TomasNeural", "Tomás · varón · Argentina"),
        ("es-UY-ValentinaNeural", "Valentina · mujer · Uruguay"),
        ("es-UY-MateoNeural", "Mateo · varón · Uruguay"),
    ]),
    ("Parecidas", [
        ("es-PY-TaniaNeural", "Tania · mujer · Paraguay"),
        ("es-PY-MarioNeural", "Mario · varón · Paraguay"),
        ("es-CL-CatalinaNeural", "Catalina · mujer · Chile"),
        ("es-CL-LorenzoNeural", "Lorenzo · varón · Chile"),
    ]),
    ("Resto de Latinoamérica", [
        ("es-MX-DaliaNeural", "Dalia · mujer · México"),
        ("es-MX-JorgeNeural", "Jorge · varón · México"),
        ("es-CO-SalomeNeural", "Salomé · mujer · Colombia"),
        ("es-CO-GonzaloNeural", "Gonzalo · varón · Colombia"),
        ("es-PE-CamilaNeural", "Camila · mujer · Perú"),
        ("es-PE-AlexNeural", "Alex · varón · Perú"),
        ("es-VE-PaolaNeural", "Paola · mujer · Venezuela"),
        ("es-VE-SebastianNeural", "Sebastián · varón · Venezuela"),
        ("es-US-PalomaNeural", "Paloma · mujer · EE. UU."),
        ("es-US-AlonsoNeural", "Alonso · varón · EE. UU."),
    ]),
    ("España", [
        ("es-ES-ElviraNeural", "Elvira · mujer · España"),
        ("es-ES-XimenaNeural", "Ximena · mujer · España"),
        ("es-ES-AlvaroNeural", "Álvaro · varón · España"),
    ]),
    ("Multilingües (acento neutro, muy expresivas)", [
        ("en-US-AvaMultilingualNeural", "Ava · mujer"),
        ("en-US-EmmaMultilingualNeural", "Emma · mujer"),
        ("en-US-AndrewMultilingualNeural", "Andrew · varón"),
        ("en-US-BrianMultilingualNeural", "Brian · varón"),
    ]),
]

DEFAULTS = {
    "nombre": "Michi",
    "nombre_dueno": "",
    "genero": "gata",
    # Skin elegido y editado en la configuración (ver ui/skins.js); None = negro clásico
    "skin": None,
    "siempre_visible": True,
    "aura": True,       # halo suave del color de los ojos alrededor de Michi
    "tamano": 1.0,      # escala del dibujo, de 0.6 a 1.6
    "voz": "es-AR-ElenaNeural",
    "velocidad_voz": "+5%",
    "tono_voz": "+0Hz",
    "atajo": "ctrl+alt+g",
    # Mantener apretada para dictar; al soltar, escribe donde esté el cursor
    "tecla_dictado": "right ctrl",
    # Orden de preferencia de cerebros; si uno falla (límite gratis, caída) se usa el siguiente
    "cerebros": ["groq", "gemini"],
    "modelo_gemini": "gemini-flash-lite-latest",
    "modelo_groq": "openai/gpt-oss-120b",
    # Mismo proveedor, otro modelo: tiene su propio límite gratis por minuto
    "modelo_groq_respaldo": "openai/gpt-oss-20b",
    "modelo_groq_stt": "whisper-large-v3-turbo",
    "gmail_usuario": "",
    "telegram_chat_id": "",
    "posicion": None,
}

_lock = threading.Lock()


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    # Configuraciones viejas solo tenían color de pelaje y de ojos
    if not cfg["skin"] and "color_pelaje" in cfg:
        eye = cfg.get("color_ojos", "#ffcc00")
        cfg["skin"] = {"pelaje": cfg["color_pelaje"], "ojo_izq": eye, "ojo_der": eye}
    cfg.pop("color_pelaje", None)
    cfg.pop("color_ojos", None)
    return cfg


def save(cfg: dict) -> None:
    with _lock:
        data = {k: v for k, v in cfg.items() if k not in SECRET_KEYS}
        CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_secret(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    try:
        return keyring.get_password(KEYRING_SERVICE, name) or ""
    except keyring.errors.KeyringError:
        return ""


def set_secret(name: str, value: str) -> None:
    if name not in SECRET_KEYS:
        raise ValueError(name)
    if value:
        keyring.set_password(KEYRING_SERVICE, name, value)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass


def secrets_status() -> dict:
    return {name: bool(get_secret(name)) for name in SECRET_KEYS}


def voice_name(cfg: dict) -> str:
    voz = cfg.get("voz") or "mujer"
    return VOICES.get(voz, voz)
