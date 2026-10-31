"""Hablar (edge-tts en la nube o Piper local) y entender (Whisper en Groq o local)."""
import asyncio
import io
import logging
import queue
import re
import subprocess
import threading
import time
import wave

import edge_tts
import miniaudio
import numpy as np
import sounddevice as sd
from openai import OpenAI

from . import config

log = logging.getLogger(__name__)
TTS_RATE = 24000
MIC_RATE = 16000
PIPER_PREFIX = "piper:"
VOICES_DIR = config.ROOT / "voces"


def clean_for_speech(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>\[\]]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def synth_mp3(text: str, voice: str, rate: str, pitch: str = "+0Hz") -> bytes:
    async def run():
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    return asyncio.run(run())


_piper_voices = {}
_piper_lock = threading.Lock()


def load_piper(voice: str):
    """Carga (y precalienta) una voz local. La primera síntesis es lenta, así que se hace una de prueba."""
    with _piper_lock:
        if voice not in _piper_voices:
            from piper import PiperVoice
            model = PiperVoice.load(VOICES_DIR / f"{voice.removeprefix(PIPER_PREFIX)}.onnx")
            for _ in model.synthesize("Hola."):
                pass
            _piper_voices[voice] = model
        return _piper_voices[voice]


def synth_pcm(text: str, voice: str, rate: str, pitch: str = "+0Hz") -> tuple[np.ndarray, int]:
    """Audio int16 mono y su frecuencia de muestreo, con el motor que corresponda a la voz."""
    if voice.startswith(PIPER_PREFIX):
        from piper import SynthesisConfig
        model = load_piper(voice)
        speed = 1 + int(rate.rstrip("%")) / 100
        # Piper no cambia el tono; la velocidad va como duración (más chica = más rápido)
        chunks = model.synthesize(text, syn_config=SynthesisConfig(length_scale=1 / speed))
        return np.concatenate([c.audio_int16_array for c in chunks]), model.config.sample_rate
    mp3 = synth_mp3(text, voice, rate, pitch)
    decoded = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=TTS_RATE)
    return np.frombuffer(decoded.samples, dtype=np.int16), TTS_RATE


def pcm_to_ogg_opus(samples: np.ndarray, sample_rate: int) -> bytes:
    """Telegram solo muestra como nota de voz audios OGG/Opus."""
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
         "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        input=samples.tobytes(), capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return proc.stdout


class Speaker:
    def __init__(self, on_level):
        self._on_level = on_level
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()
        sd.stop()

    def say(self, text: str, voice: str, rate: str, pitch: str = "+0Hz") -> None:
        """Habla bloqueando hasta terminar; manda el nivel de audio para que brillen los ojos.
        Sintetiza oración por oración: empieza a hablar apenas está lista la primera y prepara
        las siguientes mientras tanto, en vez de esperar el audio completo."""
        parts = split_sentences(clean_for_speech(text))
        if not parts:
            return
        self._stop.clear()
        ready = queue.Queue()

        def synth_all():
            for part in parts:
                if self._stop.is_set():
                    break
                try:
                    ready.put(synth_pcm(part, voice, rate, pitch))
                except Exception:
                    log.exception("No pude sintetizar: %s", part)
            ready.put(None)

        threading.Thread(target=synth_all, daemon=True).start()
        while not self._stop.is_set():
            item = ready.get()
            if item is None:
                break
            self._play(*item)
        self._on_level(0.0)

    def _play(self, samples, sample_rate):
        floats = samples.astype(np.float32) / 32768
        win = sample_rate // 30
        sd.play(samples, sample_rate)
        start = time.perf_counter()
        duration = len(samples) / sample_rate
        while not self._stop.is_set():
            elapsed = time.perf_counter() - start
            if elapsed >= duration + 0.1:
                break
            pos = int(elapsed * sample_rate)
            seg = floats[pos:pos + win]
            level = float(np.sqrt(np.mean(seg ** 2))) * 5 if len(seg) else 0.0
            self._on_level(min(1.0, level))
            time.sleep(1 / 30)
        sd.stop()


def split_sentences(text: str, min_len: int = 25) -> list[str]:
    """Corta en oraciones, juntando las muy cortas con la siguiente para no hacer pausas raras."""
    parts, buf = [], ""
    for piece in re.split(r"(?<=[.!?…])\s+", text.strip()):
        buf = f"{buf} {piece}".strip()
        if len(buf) >= min_len:
            parts.append(buf)
            buf = ""
    if buf:
        if parts and len(buf) < min_len:
            parts[-1] += " " + buf
        else:
            parts.append(buf)
    return parts


def record_utterance(stop_event: threading.Event, on_level, max_s=20.0, silence_s=0.7, wait_s=6.0):
    """Graba desde que empezás a hablar hasta 0,7 s de silencio. Devuelve WAV o None."""
    block = int(MIC_RATE * 0.03)
    block_s = block / MIC_RATE
    chunks, preroll, noise = [], [], []
    started, silent, n = False, 0.0, 0
    threshold = 0.02
    t0 = time.monotonic()
    with sd.InputStream(samplerate=MIC_RATE, channels=1, dtype="int16", blocksize=block) as stream:
        while True:
            data, _ = stream.read(block)
            x = data[:, 0].astype(np.float32) / 32768
            rms = float(np.sqrt(np.mean(x ** 2)))
            n += 1
            if n <= 8:
                noise.append(rms)
                threshold = min(0.04, max(0.008, float(np.median(noise)) * 3.5))
            if n % 2 == 0:
                on_level(min(1.0, rms * 10))
            if started:
                chunks.append(data.copy())
                silent = silent + block_s if rms < threshold else 0.0
                if silent >= silence_s:
                    break
            else:
                preroll = (preroll + [data.copy()])[-12:]
                if rms > threshold and n > 3:
                    started = True
                    chunks = preroll
            elapsed = time.monotonic() - t0
            if stop_event.is_set() or elapsed > max_s:
                break
            if not started and elapsed > wait_s:
                break
    on_level(0.0)
    if not started or len(chunks) * block_s < 0.4:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(MIC_RATE)
        w.writeframes(np.concatenate(chunks).tobytes())
    return buf.getvalue()


def record_while(stop_event: threading.Event, on_level, max_s=120.0):
    """Graba mientras no se active stop_event (dictado: mientras la tecla está apretada). Devuelve WAV o None."""
    block = int(MIC_RATE * 0.03)
    chunks, n = [], 0
    t0 = time.monotonic()
    with sd.InputStream(samplerate=MIC_RATE, channels=1, dtype="int16", blocksize=block) as stream:
        while not stop_event.is_set() and time.monotonic() - t0 < max_s:
            data, _ = stream.read(block)
            chunks.append(data.copy())
            n += 1
            if n % 2 == 0:
                x = data[:, 0].astype(np.float32) / 32768
                on_level(min(1.0, float(np.sqrt(np.mean(x ** 2))) * 10))
    on_level(0.0)
    if len(chunks) * block / MIC_RATE < 0.4:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(MIC_RATE)
        w.writeframes(np.concatenate(chunks).tobytes())
    return buf.getvalue()


_local_model = None
_stt_clients: dict[str, OpenAI] = {}


def transcribe(cfg: dict, audio: bytes, filename: str, hint: str = "") -> str:
    """hint orienta a Whisper (vocabulario, puntuación); por defecto, una charla con el gato."""
    key = config.get_secret("GROQ_API_KEY")
    if key:
        # Cliente reutilizado: mantiene viva la conexión HTTPS entre una frase y la siguiente
        client = _stt_clients.setdefault(key, OpenAI(base_url="https://api.groq.com/openai/v1", api_key=key,
                                                     timeout=15, max_retries=1))
        result = client.audio.transcriptions.create(
            model=cfg["modelo_groq_stt"], file=(filename, audio), language="es",
            prompt=hint or f"Charla en español rioplatense con {cfg['nombre']}, un gato asistente.",
        )
        return result.text.strip()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("Para entender tu voz necesito la clave de Groq (o instalar el modo local).")
    global _local_model
    if _local_model is None:
        _local_model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = _local_model.transcribe(io.BytesIO(audio), language="es", vad_filter=True,
                                          initial_prompt=hint or None)
    return " ".join(s.text for s in segments).strip()
