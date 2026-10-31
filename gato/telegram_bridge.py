"""El gato en el celular: un bot de Telegram que corre en tu PC (sin servidores ni puertos abiertos)."""
import logging
import threading
import time

import requests

from . import config, voice

log = logging.getLogger(__name__)


class TelegramBridge(threading.Thread):
    def __init__(self, app, token: str):
        super().__init__(daemon=True, name="telegram")
        self.app = app
        self.api = f"https://api.telegram.org/bot{token}/"
        self.file_api = f"https://api.telegram.org/file/bot{token}/"
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()

    def _call(self, method, **kwargs):
        r = requests.post(self.api + method, timeout=kwargs.pop("timeout", 30), **kwargs)
        return r.json()

    def run(self):
        offset = None
        log.info("Bot de Telegram escuchando")
        while not self.stopped.is_set():
            try:
                data = self._call("getUpdates", data={"timeout": 25, "offset": offset}, timeout=35)
            except requests.RequestException:
                time.sleep(5)
                continue
            if not data.get("ok"):
                log.error("Telegram: %s", data.get("description"))
                time.sleep(15)
                continue
            for update in data["result"]:
                offset = update["update_id"] + 1
                msg = update.get("message")
                if msg and not self.stopped.is_set():
                    threading.Thread(target=self._handle, args=(msg,), daemon=True).start()

    def _handle(self, msg):
        chat_id = str(msg["chat"]["id"])
        allowed = str(self.app.cfg.get("telegram_chat_id") or "").strip()
        if not allowed:
            self._call("sendMessage", data={"chat_id": chat_id, "text":
                f"¡Hola! Para que te haga caso, poné este número en la configuración del gato, en 'Tu chat id': {chat_id}"})
            return
        if chat_id != allowed:
            log.warning("Mensaje ignorado de chat no autorizado %s", chat_id)
            return
        try:
            text = msg.get("text") or ""
            if "voice" in msg or "audio" in msg:
                self._call("sendChatAction", data={"chat_id": chat_id, "action": "typing"})
                file_id = (msg.get("voice") or msg.get("audio"))["file_id"]
                path = self._call("getFile", data={"file_id": file_id})["result"]["file_path"]
                audio = requests.get(self.file_api + path, timeout=30).content
                text = voice.transcribe(self.app.cfg, audio, "voz.ogg")
            if not text or text == "/start":
                text = "Hola, presentate en una oración."
            self._call("sendChatAction", data={"chat_id": chat_id, "action": "typing"})
            reply = self.app.remote_turn(text)
            self._call("sendMessage", data={"chat_id": chat_id, "text": reply})
            self._call("sendChatAction", data={"chat_id": chat_id, "action": "record_voice"})
            cfg = self.app.cfg
            pcm = voice.synth_pcm(voice.clean_for_speech(reply), config.voice_name(cfg), cfg["velocidad_voz"], cfg["tono_voz"])
            self._call("sendVoice", data={"chat_id": chat_id}, files={"voice": ("gato.ogg", voice.pcm_to_ogg_opus(*pcm), "audio/ogg")})
        except Exception as e:
            log.exception("Error atendiendo Telegram")
            self._call("sendMessage", data={"chat_id": chat_id, "text": f"Se me trabó algo: {e}"})
