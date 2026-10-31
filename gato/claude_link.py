"""Puente con Claude (Anthropic) para lo que pide más cabeza que una charla: redactar, planificar, código, análisis.

Tres caminos, del más directo al que no necesita nada:
1. Clave de la API de Anthropic (de pago) -> respuesta directa con el SDK oficial.
2. Claude Code instalado (`claude`) -> `claude -p`, usa tu plan de Claude sin clave aparte.
3. Ninguno -> abre claude.ai con el pedido ya escrito (y copiado al portapapeles por las dudas).
"""
import logging
import shutil
import subprocess
import urllib.parse
import webbrowser

import anthropic

from . import clipboard, config

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
SYSTEM = ("Respondés pedidos que llegan desde un asistente de voz en español rioplatense. "
          "Empezá con una respuesta directa de una o dos oraciones que se pueda leer en voz alta; "
          "si hace falta más detalle, seguí después con el desarrollo completo.")

_client: anthropic.Anthropic | None = None


def backend() -> str:
    if config.get_secret("ANTHROPIC_API_KEY"):
        return "api"
    if shutil.which("claude"):
        return "cli"
    return "web"


def _ask_api(question: str) -> str:
    global _client
    key = config.get_secret("ANTHROPIC_API_KEY")
    if _client is None or _client.api_key != key:
        _client = anthropic.Anthropic(api_key=key, timeout=120.0)
    # Esfuerzo bajo: para consultas habladas importa más la latencia que la profundidad máxima.
    # "fallbacks" reintenta en otro modelo si este rechaza el pedido por sus filtros de seguridad.
    response = _client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": question}],
    )
    if response.stop_reason == "refusal":
        return "Claude no quiso responder ese pedido."
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def _ask_cli(question: str) -> str:
    proc = subprocess.run(
        ["claude", "-p", f"{SYSTEM}\n\n{question}", "--output-format", "text"],
        capture_output=True, text=True, encoding="utf-8", timeout=180, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300] or "Claude Code falló")
    return proc.stdout.strip()


def open_web(prompt: str) -> str:
    clipboard.set_text(prompt)
    webbrowser.open("https://claude.ai/new?q=" + urllib.parse.quote(prompt))
    return "Abrí Claude en el navegador con el pedido escrito (también quedó copiado, por si hay que pegarlo)."


def ask(question: str) -> str:
    """Devuelve la respuesta de Claude, o qué se hizo si solo se pudo abrir la web."""
    mode = backend()
    try:
        if mode == "api":
            answer = _ask_api(question)
        elif mode == "cli":
            answer = _ask_cli(question)
        else:
            return open_web(question)
    except anthropic.AuthenticationError:
        return "La clave de Anthropic no es válida; revisala en la configuración."
    except anthropic.RateLimitError:
        return "Claude está saturado en este momento; probá en un rato."
    except anthropic.APIStatusError as e:
        log.warning("Claude API: %s", e)
        return open_web(question)
    except (anthropic.APIConnectionError, RuntimeError, subprocess.TimeoutExpired) as e:
        log.warning("Claude (%s) falló: %s", mode, e)
        return open_web(question)
    clipboard.set_text(answer)
    return f"Respuesta de Claude (completa en el portapapeles):\n{answer}"
