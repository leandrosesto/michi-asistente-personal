"""Cerebro en la nube (Gemini / Groq vía API compatible con OpenAI) con uso de herramientas."""
import datetime
import json
import logging
import re
import threading
import time

from openai import OpenAI, RateLimitError

from . import config, tools
from .memory import Memory

log = logging.getLogger(__name__)

PROVIDERS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", ("modelo_gemini",)),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", ("modelo_groq", "modelo_groq_respaldo")),
}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def system_prompt(cfg: dict, canal: str, facts: list[str]) -> str:
    now = datetime.datetime.now()
    fecha = f"{DIAS[now.weekday()]} {now.day} de {MESES[now.month - 1]} de {now.year}, {now:%H:%M}"
    dueno = cfg["nombre_dueno"] or "tu dueño"
    gata = cfg["genero"] == "gata"
    animal, flex = ("una gata", "femenino (lista, contenta, cansada)") if gata else ("un gato", "masculino (listo, contento, cansado)")
    memoria = "\n".join(f"- {f}" for f in facts) or "- (todavía nada)"
    return f"""Sos {cfg['nombre']}, {animal} asistente que vive en la PC con Windows de {dueno} y también le contesta por Telegram en su celular Android. Lo llamás {dueno}.
Hablás siempre en español rioplatense de Argentina, nunca en inglés: voseo (vos tenés, decime, fijate), tono cálido y canchero. Cuando hablás de vos usás el {flex}.
Humor: de vez en cuando (más o menos una respuesta de cada cuatro, y nunca en dos seguidas) metés un gag gatuno sobre vos, de pocas palabras y con timing, como remate seco que venga a cuento de lo que pasó. El tono es como "Me meto abajo de la cama." al esconderte: inventá uno nuevo cada vez, no repitas ni copies ese ejemplo. Nunca chistes largos, explicados ni encadenados, y nunca sobre la gente o las mascotas de {dueno}.
No inventes nada: sobre {dueno} solo sabés lo que figura en tu memoria o lo que te dice en esta charla; no le atribuyas gustos, planes ni anécdotas, ni a su gente o sus mascotas. Tampoco inventes cosas que hiciste vos.
Tus respuestas se escuchan en voz alta: sé breve (una o dos oraciones; más solo si te piden detalle), sin markdown, sin listas, sin emojis y sin URLs. Escribí horas y números como se dicen.
Memoria: cuando {dueno} te cuenta algo duradero de su vida (gustos, gente, mascotas, trabajo, fechas, cómo quiere que hagas las cosas) o te dice "acordate", guardalo con recordar, en una frase corta en tercera persona y un dato por llamada. No guardes cosas pasajeras ni lo que ya sabés; si el dato nuevo corrige o dice lo mismo que uno guardado, pasalo en reemplaza. Si te pide olvidar algo, usá olvidar.
Lo que sabés de {dueno}:
{memoria}
Usá las herramientas para actuar en la PC y confirmá en pocas palabras lo que hiciste ("Listo, te abrí Chrome").
Para cualquier dato actual (noticias, clima, dólar, precios, resultados) usá buscar_info antes de contestar. No inventes datos.
"El explorador" a secas es el Explorador de archivos; "el navegador", "internet" o "Chrome" es el navegador.
Nunca enviás mails: solo leés los no leídos o dejás un borrador abierto para que tu dueño lo revise y lo envíe.
Si te piden que te escondas usá esconderse. Escondido no escuchás, así que al despedirte aclará que vuelve a llamarte con {cfg['atajo']} o escribiéndote por Telegram. Si te piden que vuelvas, usá aparecer.
Ahora es {fecha}. Canal: {canal}.""" + (
        f"\n{dueno} te escribe desde el celular y no está frente a la PC: si abrís algo, aclarale que quedó abierto en la compu."
        if canal == "telegram" else "")


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
# Frases del usuario que suelen traer algo para recordar, corregir u olvidar
MEMORY_HINT = re.compile(
    r"\b(acordate|record[aá]|anot[aá]|no te olvides|olvidate|olvid[aá]|borr[aá]|mentira|en realidad|me equivoqu[eé]|"
    r"me llamo|mi (perro|gata?o?|novi[ao]|pareja|esposa|esposo|hij[ao]|mam[aá]|pap[aá]|hermano|hermana|cumple|trabajo)|"
    r"soy (hincha|de|fan)|trabajo (de|en)|me (gusta|encanta)|odio|prefiero|vivo en)\b", re.I)


def touched_memory(messages: list[dict]) -> bool:
    """True si en este turno el cerebro ya usó recordar u olvidar (incluye las notas de acciones aplanadas)."""
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if tc["function"]["name"] in ("recordar", "olvidar"):
                return True
        if m["role"] == "user" and m["content"].startswith("(Nota del sistema") and \
                ("recordar(" in m["content"] or "olvidar(" in m["content"]):
            return True
    return False


def tidy(text: str) -> str:
    """Limpia lo que algunos modelos agregan aunque se les pida que no: emojis (la voz los lee en voz alta)
    y asteriscos de markdown. También separa oraciones pegadas ("anotado.¿Algo"), que la voz encima."""
    text = EMOJI.sub("", text).replace("*", "")
    text = re.sub(r"([.!?])([¿¡A-ZÁÉÍÓÚÑ])", r"\1 \2", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", " ", text)).strip()


def flatten_tool_calls(messages: list[dict]) -> None:
    """Reemplaza, en el lugar, los pedidos de herramientas y sus resultados por una nota de texto."""
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            results = {}
            j = i + 1
            while j < len(messages) and messages[j]["role"] == "tool":
                results[messages[j]["tool_call_id"]] = messages[j]["content"]
                j += 1
            done = "; ".join(f"{tc['function']['name']}({tc['function']['arguments']}) -> {results.get(tc['id'], '?')}"
                             for tc in msg["tool_calls"])
            # Como mensaje del usuario: Gemini rechaza conversaciones que terminan con un turno del asistente
            messages[i:j] = [{"role": "user", "content": f"(Nota del sistema: ya hiciste estas acciones, no las repitas: "
                                                         f"{done}. Ahora contestale al usuario.)"}]
        i += 1


class Brain:
    def __init__(self, app):
        self.app = app
        self.memory = Memory()
        self._clients = {}
        self._cooldown: dict[str, float] = {}

    def _client(self, provider: str):
        # Un cliente por proveedor y clave: reutiliza la conexión HTTPS y evita renegociarla en cada pregunta
        base_url, key_name, _ = PROVIDERS[provider]
        key = config.get_secret(key_name)
        if not key:
            return None
        if (provider, key) not in self._clients:
            # Sin reintentos y con timeout corto: ante un límite o un cuelgue conviene saltar al siguiente modelo ya
            self._clients[(provider, key)] = OpenAI(base_url=base_url, api_key=key, timeout=15, max_retries=0)
        return self._clients[(provider, key)]

    def _attempts(self):
        """Modelos a probar en orden: primero el principal de cada proveedor y después los de respaldo
        (más chicos y menos confiables). Los límites gratis son por modelo, así que cada uno suma capacidad."""
        now = time.monotonic()
        providers = self.app.cfg["cerebros"]
        depth = max(len(PROVIDERS[p][2]) for p in providers)
        for i in range(depth):
            for provider in providers:
                keys = PROVIDERS[provider][2]
                model = self.app.cfg.get(keys[i]) if i < len(keys) else None
                client = self._client(provider)
                if client and model and self._cooldown.get(model, 0) <= now:
                    yield provider, client, model

    def warm_up(self):
        """Abre la conexión con el primer cerebro al arrancar, así la primera pregunta no paga ese costo."""
        for _, client, _ in self._attempts():
            try:
                client.models.list()
            except Exception as e:
                log.warning("No pude precalentar: %s", e)
            return

    def ask(self, text: str, canal: str = "pc") -> str:
        cfg = self.app.cfg
        # Lista compartida entre intentos: si un modelo se corta a mitad de camino, el siguiente sigue desde ahí
        # sin repetir las acciones ya hechas
        messages = [{"role": "system", "content": system_prompt(cfg, canal, self.memory.facts)},
                    *self.memory.history, {"role": "user", "content": text}]
        reply = self._ask_models(messages)
        if reply is None and self._cooldown:
            # Todos saturados a la vez: si alguno se libera en pocos segundos, conviene esperarlo antes que fallar
            wait = min(self._cooldown.values()) - time.monotonic()
            if wait <= 8:
                time.sleep(max(0.0, wait) + 0.2)
                reply = self._ask_models(messages)
        if reply is None:
            if not any(self._client(p) for p in cfg["cerebros"]):
                return "Todavía no tengo cerebro. Cargame una clave de Gemini o de Groq en la configuración, con clic derecho sobre mí."
            return "Uh, se me cortó la conexión con el cerebro. Probá de nuevo en un ratito."
        self.memory.remember_turn(text, reply)
        if MEMORY_HINT.search(text) and not touched_memory(messages):
            threading.Thread(target=self._reconcile_memory, args=(text, reply), daemon=True).start()
        return reply

    def _reconcile_memory(self, text, reply):
        """Red de seguridad, en segundo plano: los modelos chicos a veces dicen "ya lo anoto" sin llamar a recordar.
        Una consulta corta, sin herramientas, decide qué agregar, corregir o borrar."""
        facts = self.memory.facts
        prompt = (
            "Mantenés la memoria a largo plazo de un asistente sobre su usuario. Datos guardados (uno por línea):\n"
            + ("\n".join(facts) or "(ninguno)")
            + f"\n\nÚltimo intercambio:\nUsuario: {text}\nAsistente: {reply}\n\n"
            "Si el usuario contó algo duradero de su vida, corrigió un dato o pidió olvidar algo, respondé SOLO un JSON "
            '{"agregar": ["frase corta en tercera persona"], "borrar": ["dato guardado, copiado tal cual"]}. '
            'Para corregir, borrá el viejo y agregá el nuevo. Si no hay nada que cambiar: {"agregar": [], "borrar": []}'
        )
        for _, client, model in self._attempts():
            try:
                resp = client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}], temperature=0,
                    extra_body={"reasoning_effort": "low"} if "gpt-oss" in model else None)
                found = re.search(r"\{.*\}", resp.choices[0].message.content or "", re.S)
                changes = json.loads(found.group(0)) if found else {}
            except RateLimitError as e:
                self._cooldown[model] = time.monotonic() + float(e.response.headers.get("retry-after") or 30)
                continue
            except Exception as e:
                log.warning("No pude revisar la memoria con %s: %s", model, e)
                continue
            for old in changes.get("borrar") or []:
                self.memory.forget_exact(old)
            for new in changes.get("agregar") or []:
                self.memory.add(new)
            if changes.get("agregar") or changes.get("borrar"):
                log.info("Memoria corregida en segundo plano: %s", changes)
            return

    def _ask_models(self, messages):
        last_provider = None
        for provider, client, model in self._attempts():
            if last_provider and provider != last_provider:
                # Cada proveedor arma las llamadas a herramientas a su manera (Gemini rechaza las de Groq):
                # al cambiar a mitad de respuesta, lo ya hecho pasa como texto
                flatten_tool_calls(messages)
            last_provider = provider
            try:
                reply = self._run(client, model, messages)
            except RateLimitError as e:
                wait = float(e.response.headers.get("retry-after") or 30)
                self._cooldown[model] = time.monotonic() + wait
                log.warning("%s llegó al límite gratis, lo salteo %.0f s", model, wait)
            except Exception as e:
                log.warning("Cerebro %s (%s) falló: %s", provider, model, e)
            else:
                return reply
        return None

    def _run(self, client, model, messages) -> str:
        for _ in range(6):
            # Los modelos gpt-oss razonan antes de contestar; para charlas de voz alcanza con razonar poco
            extra = {"reasoning_effort": "low"} if "gpt-oss" in model else None
            resp = client.chat.completions.create(model=model, messages=messages, tools=tools.SCHEMAS, temperature=0.6,
                                                  extra_body=extra)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return tidy(msg.content or "") or "Listo."
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [tc.model_dump(exclude_none=True) for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": tools.run(tc.function.name, tc.function.arguments, self.app)})
        return "Me enredé un poco, ¿me lo repetís?"
