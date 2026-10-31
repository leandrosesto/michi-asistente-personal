"""Arranque: ventana transparente con la cabeza del gato, atajo global, voz, cerebro y Telegram."""
import ctypes
import json
import logging
import os
import threading
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path

import keyboard
import webview

from . import autostart, clipboard, config, net, tools, voice

net.prefer_ipv4()
from .brain import Brain
from .telegram_bridge import TelegramBridge

logging.basicConfig(
    filename=config.ROOT / "gato.log", level=logging.INFO, encoding="utf-8",
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gato")

UI = Path(__file__).resolve().parent / "ui"
# Tamaño lógico fijo (cambiarlo rompe la transparencia de WebView2). Lo visible se recorta con una región de Windows.
WIN_SIZE = (300, 300)  # alcanza para el tamaño máximo (160 %) más el globo de texto
TAMANO_MIN, TAMANO_MAX = 0.6, 1.6
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
gdi32.CombineRgn.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
user32.SetWindowRgn.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.BOOL)
# HWND_TOPMOST es (HWND)-1: sin argtypes ctypes lo pasaría como int de 32 bits
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT)
GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SPI_GETWORKAREA = 0x30
RGN_OR = 2


class MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def make_glass(window, hwnd):
    """Transparencia real por píxel: el formulario pinta negro y DWM lo trata como vidrio sin fondo.
    Sin esto WebView2 deja ver el gris del formulario apenas la ventana se repinta."""
    from System import Action
    from System.Drawing import Color

    form = window.native
    form.Invoke(Action(lambda: setattr(form, "BackColor", Color.Black)))
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(-1, -1, -1, -1)))
    for attr in (38, 2):  # DWMWA_SYSTEMBACKDROP_TYPE = NONE, DWMWA_NCRENDERING_POLICY = DISABLED
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), 4)


def clamp_size(value) -> float:
    try:
        return round(min(TAMANO_MAX, max(TAMANO_MIN, float(value))), 2)
    except (TypeError, ValueError):
        return 1.0


def key_to_vk(name: str) -> int | None:
    """Código de tecla virtual de Windows para un nombre en inglés ("right ctrl", "f9", "caps lock")."""
    from keyboard import _canonical_names, _winkeyboard
    wanted = _canonical_names.normalize_name(name)
    for vk, (official, _) in _winkeyboard.official_virtual_keys.items():
        if _canonical_names.normalize_name(official) == wanted:
            return vk
    return None


def cursor_pos():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def move_window(hwnd, x, y):
    user32.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


class Api:
    """Métodos que llaman las páginas (cat.html y settings.html)."""

    def __init__(self, app):
        self._app = app

    def ready(self, dpr=None):
        if dpr:
            self._app._scale = float(dpr)
            threading.Thread(target=self._app.on_page_ready, daemon=True).start()
        return {k: self._app.cfg[k] for k in ("nombre", "skin", "aura", "tamano")}

    def preview_look(self, aura, tamano):
        """Muestra en Michi el aura y el tamaño mientras se mueven los controles, sin guardar todavía."""
        look = {**self.ready(), "aura": bool(aura), "tamano": clamp_size(tamano)}
        self._app.preview_size = look["tamano"]
        self._app.js(f"cat.applyConfig({json.dumps(look)})")

    def drag_start(self):
        self._app.drag_start()

    def drag_end(self):
        return self._app.drag_end()

    def set_regions(self, rects):
        self._app.set_regions(rects)

    def on_click(self):
        self._app.activate()

    def menu(self, action):
        if action == "hablar":
            self._app.activate()
        elif action == "configurar":
            self._app.open_settings()
        elif action == "salir":
            self._app.quit()

    def get_settings(self):
        return {**self._app.cfg, "voz": config.voice_name(self._app.cfg), "voces": config.VOICE_GROUPS,
                "memoria": self._app.brain.memory.facts,
                "secretos": config.secrets_status(), "inicio_windows": autostart.is_enabled()}

    def save_settings(self, data):
        return self._app.save_settings(data)

    def test_voice(self, voz, velocidad, tono, nombre):
        threading.Thread(target=self._app.speak, args=(f"Hola, soy {nombre or 'tu gato'}. ¿Te gusta cómo hablo?",),
                         kwargs={"voice": voz, "rate": velocidad, "pitch": tono}, daemon=True).start()

    def open_url(self, url):
        if url.startswith("https://"):
            webbrowser.open(url)


class Assistant:
    def __init__(self):
        self.cfg = config.load()
        self.api = Api(self)
        self.brain = Brain(self)
        self.speaker = voice.Speaker(lambda a: self.js(f"cat.setAmp({a:.3f})"))
        self.busy = threading.Lock()
        self.state = "idle"
        self.window = None
        self.settings_window = None
        self.hwnd = None
        self.telegram = None
        self._stop_listen = threading.Event()
        self._drag = None
        self._hotkey = None
        self._scale = 1.0
        self.preview_size = None  # tamaño de prueba mientras se mueve el control en Configurar
        self.hidden = False
        self.hide_after_reply = False
        self._dict_hook = None
        self._dictating = False
        self._dict_other_key = False
        self._dict_stop = threading.Event()

    # ---- visibilidad ----
    def apply_on_top(self):
        if self.hwnd and not self.hidden:
            after = HWND_TOPMOST if self.cfg["siempre_visible"] else HWND_NOTOPMOST
            user32.SetWindowPos(self.hwnd, after, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def hide(self):
        self.hidden = True
        user32.ShowWindowAsync(self.hwnd, SW_HIDE)

    def show(self):
        self.hidden = False
        user32.ShowWindowAsync(self.hwnd, SW_SHOWNOACTIVATE)
        self.apply_on_top()

    # ---- comunicación con la cara ----
    def js(self, code):
        if self.window:
            try:
                self.window.run_js(code)
            except Exception:
                pass

    def set_state(self, state):
        self.state = state
        self.js(f"cat.setState({json.dumps(state)})")

    def bubble(self, text, kind="cat"):
        self.js(f"cat.say({json.dumps(text)}, {json.dumps(kind)})")

    def speak(self, text, voice=None, rate=None, pitch=None):
        self.set_state("speaking")
        try:
            self.speaker.say(text, voice or config.voice_name(self.cfg), rate or self.cfg["velocidad_voz"],
                             pitch or self.cfg["tono_voz"])
        except Exception:
            log.exception("No pude hablar")
        finally:
            self.set_state("idle")

    # ---- arrastrar y click ----
    def drag_start(self):
        if not self.hwnd:
            return
        cx, cy = cursor_pos()
        left, top, *_ = window_rect(self.hwnd)
        self._drag = {"cursor": (cx, cy), "win": (left, top), "moved": False}
        threading.Thread(target=self._drag_loop, daemon=True).start()

    def _drag_loop(self):
        while self._drag and user32.GetAsyncKeyState(0x01) & 0x8000:
            d = self._drag
            cx, cy = cursor_pos()
            dx, dy = cx - d["cursor"][0], cy - d["cursor"][1]
            if d["moved"] or abs(dx) + abs(dy) > 5:
                d["moved"] = True
                move_window(self.hwnd, d["win"][0] + dx, d["win"][1] + dy)
            time.sleep(1 / 120)

    def drag_end(self):
        d, self._drag = self._drag, None
        if d and d["moved"]:
            left, top, right, _ = window_rect(self.hwnd)
            self.cfg["posicion"] = [(left + right) // 2, top]
            config.save(self.cfg)
            return True
        return False

    def set_regions(self, rects):
        """Recorta la ventana a los rectángulos visibles (gato, globo, menú) en píxeles CSS [x, y, w, h, radio].
        Fuera de la región la ventana no existe: no oscurece el fondo y los clics pasan al escritorio."""
        if not self.hwnd:
            return
        s, rgn = self._scale, None
        for x, y, w, h, r in rects:
            part = gdi32.CreateRoundRectRgn(int(x * s), int(y * s), int((x + w) * s) + 1, int((y + h) * s) + 1,
                                            int(r * s), int(r * s))
            if rgn is None:
                rgn = part
            else:
                gdi32.CombineRgn(rgn, rgn, part, RGN_OR)
                gdi32.DeleteObject(part)
        user32.SetWindowRgn(self.hwnd, rgn, True)

    def _look_loop(self):
        last, tick = None, 0
        while True:
            time.sleep(0.05)
            if not self.hwnd or self.hidden:
                continue
            tick += 1
            # Otras ventanas "siempre encima" o apps a pantalla completa pueden taparlo: cada 2 s recupera su lugar
            if tick % 40 == 0 and self.cfg["siempre_visible"]:
                self.apply_on_top()
            cx, cy = cursor_pos()
            left, top, right, bottom = window_rect(self.hwnd)
            # Centro de los ojos: 4 px de margen + 72 de los 110 px del dibujo, escalado por el tamaño
            size = self.preview_size or self.cfg["tamano"]
            hx, hy = (left + right) / 2, top + (4 + 72 * size) * self._scale
            dx, dy = cx - hx, cy - hy
            look = (round(dx / (abs(dx) + 250) * 1.6, 2), round(dy / (abs(dy) + 250) * 1.6, 2))
            if look != last:
                last = look
                self.js(f"cat.setLook({look[0]}, {look[1]})")

    # ---- conversación ----
    def activate(self):
        if self.hidden:
            # Escondido: el atajo lo hace volver y ya se pone a escuchar
            self.show()
            if not self.busy.locked():
                threading.Thread(target=self._voice_turn, daemon=True).start()
        elif self.state == "speaking":
            self.speaker.stop()
        elif self.state == "listening":
            self._stop_listen.set()
        elif not self.busy.locked():
            threading.Thread(target=self._voice_turn, daemon=True).start()

    def _voice_turn(self):
        with self.busy:
            self._stop_listen.clear()
            self.speaker.stop()
            self.set_state("listening")
            try:
                audio = voice.record_utterance(self._stop_listen, lambda a: self.js(f"cat.setAmp({a:.3f})"))
            except Exception:
                log.exception("Micrófono")
                self.set_state("sad")
                self.bubble("No puedo usar el micrófono.")
                return
            if not audio:
                self.set_state("idle")
                self.bubble("No te escuché, tocame de nuevo cuando quieras.")
                return
            self.set_state("thinking")
            try:
                text = voice.transcribe(self.cfg, audio, "voz.wav")
            except Exception as e:
                log.exception("Transcripción")
                self.set_state("sad")
                self.bubble(str(e) if isinstance(e, RuntimeError) else "No pude entender el audio.")
                return
            if not text:
                self.set_state("idle")
                return
            self.bubble(text, "user")
            reply = self.brain.ask(text, "pc")
            self.bubble(reply)
            self.speak(reply)
            if self.hide_after_reply:
                self.hide_after_reply = False
                self.hide()

    # ---- dictado ----
    def register_dictation(self):
        # La tecla se vigila con GetAsyncKeyState y su código de Windows: la librería keyboard no distingue
        # Ctrl derecho de izquierdo (mismo scan code) y en Windows en español los nombres cambian ("ctrl derecha")
        if not self._dict_hook:
            self._dict_hook = keyboard.hook(self._on_any_key)
            threading.Thread(target=self._dictation_loop, daemon=True).start()

    def _dictation_loop(self):
        was_down = False
        while True:
            vk = key_to_vk(self.cfg["tecla_dictado"])
            down = bool(vk and user32.GetAsyncKeyState(vk) & 0x8000)
            if down and not was_down and not self._dictating and not self.busy.locked():
                self._dictating = True
                self._dict_other_key = False
                self._dict_stop.clear()
                threading.Thread(target=self._dictate, daemon=True).start()
            elif was_down and not down:
                self._dict_stop.set()
            was_down = down
            time.sleep(0.02)

    def _on_any_key(self, e):
        # Otra tecla mientras se sostiene la de dictado: era un atajo (Ctrl+C, Ctrl+V...), no un dictado
        vk = key_to_vk(self.cfg["tecla_dictado"])
        if self._dictating and e.event_type == keyboard.KEY_DOWN and e.scan_code != user32.MapVirtualKeyW(vk, 0):
            self._dict_other_key = True
            self._dict_stop.set()

    def _dictate(self):
        with self.busy:
            t0 = time.monotonic()
            shown = False

            def level(a):
                nonlocal shown
                # Recién se muestra después de un ratito, para no reaccionar a cada Ctrl suelto
                if not shown and time.monotonic() - t0 > 0.35:
                    shown = True
                    self.set_state("listening")
                    self.bubble("Dictando… soltá la tecla para escribir.", "user")
                if shown:
                    self.js(f"cat.setAmp({a:.3f})")

            try:
                audio = voice.record_while(self._dict_stop, level)
            except Exception:
                log.exception("Micrófono (dictado)")
                audio = None
            finally:
                self._dictating = False
            held = time.monotonic() - t0
            if self._dict_other_key or held < 0.6 or not audio:
                if shown:
                    self.set_state("idle")
                return
            self.set_state("thinking")
            try:
                text = voice.transcribe(self.cfg, audio, "dictado.wav",
                                        hint="Texto dictado en español rioplatense, con puntuación y mayúsculas correctas.")
            except Exception:
                log.exception("Transcripción (dictado)")
                text = ""
            if text:
                clipboard.paste_text(text)
                self.set_state("happy")
                time.sleep(0.8)
            self.set_state("idle")

    def remote_turn(self, text):
        """Mensaje que llega desde el celular: el gato reacciona en pantalla pero contesta por Telegram."""
        with self.busy:
            self.set_state("thinking")
            self.bubble("Celular: " + text, "user")
            try:
                reply = self.brain.ask(text, "telegram")
            finally:
                self.set_state("happy")
                threading.Timer(1.5, lambda: self.state == "happy" and self.set_state("idle")).start()
            self.bubble(reply)
            if self.hide_after_reply:
                self.hide_after_reply = False
                self.hide()
            return reply

    # ---- configuración ----
    def open_settings(self):
        if self.settings_window:
            try:
                self.settings_window.restore()
                self.settings_window.show()
                return
            except Exception:
                self.settings_window = None
        self.settings_window = webview.create_window(
            f"Configurar a {self.cfg['nombre']}", url=(UI / "settings.html").as_uri(), js_api=self.api,
            width=600, height=760, min_size=(480, 500), background_color="#151515")
        self.settings_window.events.closed += self._settings_closed

    def _settings_closed(self):
        # Si se cerró sin guardar, Michi vuelve al aura y tamaño guardados
        self.settings_window = None
        self.preview_size = None
        self.js(f"cat.applyConfig({json.dumps(self.api.ready())})")

    def save_settings(self, data):
        old_token = config.get_secret("TELEGRAM_BOT_TOKEN")
        for name, value in (data.pop("secretos", None) or {}).items():
            config.set_secret(name, value)
        if "memoria" in data:
            self.brain.memory.set_facts(data.pop("memoria"))
        for k in ("nombre", "nombre_dueno", "genero", "skin", "siempre_visible", "voz", "velocidad_voz", "tono_voz", "atajo",
                  "tecla_dictado", "cerebros", "aura", "tamano",
                  "modelo_gemini", "modelo_groq", "gmail_usuario", "telegram_chat_id"):
            if k in data:
                self.cfg[k] = data[k]
        self.cfg["tamano"] = clamp_size(self.cfg["tamano"])
        self.preview_size = None
        config.save(self.cfg)
        if "inicio_windows" in data:
            autostart.set_enabled(bool(data["inicio_windows"]))
        self.apply_on_top()
        self.js(f"cat.applyConfig({json.dumps(self.api.ready())})")
        self.register_hotkey()
        self.preload_voice()
        if config.get_secret("TELEGRAM_BOT_TOKEN") != old_token or not self.telegram:
            self.start_telegram()
        return {"secretos": config.secrets_status()}

    def preload_voice(self):
        # La voz local tarda unos segundos en cargar: mejor ahora que en la primera respuesta
        name = config.voice_name(self.cfg)
        if name.startswith(voice.PIPER_PREFIX):
            threading.Thread(target=voice.load_piper, args=(name,), daemon=True).start()

    def register_hotkey(self):
        if self._hotkey:
            keyboard.remove_hotkey(self._hotkey)
            self._hotkey = None
        try:
            self._hotkey = keyboard.add_hotkey(self.cfg["atajo"], self.activate)
        except (ValueError, KeyError):
            log.warning("Atajo inválido: %s", self.cfg["atajo"])

    def start_telegram(self):
        if self.telegram:
            self.telegram.stop()
            self.telegram = None
        token = config.get_secret("TELEGRAM_BOT_TOKEN")
        if token:
            self.telegram = TelegramBridge(self, token)
            self.telegram.start()

    # ---- ciclo de vida ----
    def place_window(self):
        left, top, right, bottom = window_rect(self.hwnd)
        w, h = right - left, bottom - top
        pos = self.cfg.get("posicion")
        if pos:
            x, y = pos[0] - w / 2, pos[1]
        else:
            work = wintypes.RECT()
            user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
            # Abajo a la derecha, con lugar debajo del gato para el globo de texto
            x = work.right - w - 8
            y = work.bottom - h - 4
        move_window(self.hwnd, x, y)

    def on_page_ready(self):
        for _ in range(50):
            if self.hwnd:
                break
            time.sleep(0.1)
        if not self.hwnd:
            return
        self.place_window()
        self.js("updateRegion()")
        secrets = config.secrets_status()
        if not (secrets["GEMINI_API_KEY"] or secrets["GROQ_API_KEY"]):
            self.set_state("happy")
            self.bubble("¡Hola! Para despertarme del todo, hacé clic derecho y cargá mis claves en Configurar.")
            time.sleep(1.5)
            self.set_state("idle")
        else:
            self.bubble(f"Acá estoy. Tocame o apretá {self.cfg['atajo']} para hablarme.")

    def on_started(self):
        title = self.window.title
        for _ in range(50):
            self.hwnd = user32.FindWindowW(None, title)
            if self.hwnd:
                break
            time.sleep(0.1)
        if self.hwnd:
            # Sin botón en la barra de tareas, como un widget
            style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
            try:
                make_glass(self.window, self.hwnd)
            except Exception:
                log.exception("No pude activar la transparencia")
            self.apply_on_top()
        threading.Thread(target=self._look_loop, daemon=True).start()
        threading.Thread(target=tools.preload_apps, daemon=True).start()
        threading.Thread(target=self.brain.warm_up, daemon=True).start()
        self.preload_voice()
        self.register_hotkey()
        self.register_dictation()
        self.start_telegram()

    def quit(self):
        if self.telegram:
            self.telegram.stop()
        for w in list(webview.windows):
            w.destroy()
        os._exit(0)


def main():
    app = Assistant()
    app.window = webview.create_window(
        f"{app.cfg['nombre']} - gato asistente", url=(UI / "cat.html").as_uri(), js_api=app.api,
        width=WIN_SIZE[0], height=WIN_SIZE[1], min_size=WIN_SIZE, frameless=True, transparent=True,
        on_top=app.cfg["siempre_visible"], resizable=False,
        easy_drag=False, shadow=False)
    webview.start(app.on_started, gui="edgechromium", private_mode=False,
                  storage_path=str(config.ROOT / ".webview"))


if __name__ == "__main__":
    main()
