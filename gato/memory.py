"""Memoria a largo plazo: datos sueltos sobre el dueño y la charla reciente, guardados en memoria.json.
Los datos viajan dentro del mensaje de sistema, así que recordar no suma llamadas extra al cerebro."""
import difflib
import json
import threading
import unicodedata

from .config import ROOT

MEM_PATH = ROOT / "memoria.json"
MAX_FACTS = 80
MAX_HISTORY = 12


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


class Memory:
    def __init__(self):
        self._lock = threading.Lock()
        self.facts: list[str] = []
        self.history: list[dict] = []
        if MEM_PATH.exists():
            try:
                data = json.loads(MEM_PATH.read_text(encoding="utf-8"))
                self.facts = data.get("datos", [])
                self.history = data.get("historial", [])[-MAX_HISTORY:]
            except json.JSONDecodeError:
                pass

    def _save(self):
        MEM_PATH.write_text(json.dumps({"datos": self.facts, "historial": self.history}, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    def add(self, fact: str, replaces: str = "") -> str:
        """replaces: dato viejo que este corrige o repite. Lo decide el cerebro, que entiende el sentido;
        por texto no se distingue "le gusta / le encanta el asado" de "hincha de Racing / de Boca"."""
        fact = fact.strip()
        with self._lock:
            if any(_norm(f) == _norm(fact) for f in self.facts):
                return "Ya lo sabía"
            if replaces:
                self.facts = [f for f in self.facts if _norm(f) != _norm(replaces)]
            self.facts = (self.facts + [fact])[-MAX_FACTS:]
            self._save()
        return "Guardado en la memoria"

    def forget(self, query: str) -> str:
        q = _norm(query)
        with self._lock:
            keep = [f for f in self.facts if q not in _norm(f)]
            if len(keep) == len(self.facts):
                close = difflib.get_close_matches(q, [_norm(f) for f in self.facts], n=1, cutoff=0.5)
                keep = [f for f in self.facts if not close or _norm(f) != close[0]]
            removed = len(self.facts) - len(keep)
            self.facts = keep
            self._save()
        return f"Olvidé {removed} dato(s)" if removed else "No encontré nada parecido en la memoria"

    def forget_exact(self, fact: str):
        with self._lock:
            self.facts = [f for f in self.facts if _norm(f) != _norm(fact)]
            self._save()

    def set_facts(self, facts: list[str]):
        with self._lock:
            self.facts = [f.strip() for f in facts if f.strip()][-MAX_FACTS:]
            self._save()

    def remember_turn(self, user: str, reply: str):
        with self._lock:
            self.history = (self.history + [{"role": "user", "content": user},
                                             {"role": "assistant", "content": reply}])[-MAX_HISTORY:]
            self._save()
