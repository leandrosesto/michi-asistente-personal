"""Iniciar con Windows mediante un acceso directo en la carpeta Inicio del usuario (sin permisos de administrador)."""
import os
import subprocess
import sys
from pathlib import Path

from .config import ROOT

SHORTCUT = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup\Gato asistente.lnk"


def _pythonw() -> Path:
    return ROOT / ".venv" / "Scripts" / "pythonw.exe"


def is_enabled() -> bool:
    return SHORTCUT.exists()


def set_enabled(on: bool) -> None:
    if not on:
        SHORTCUT.unlink(missing_ok=True)
        return
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
        "$s.TargetPath = $env:TARGET; $s.Arguments = '-m gato'; $s.WorkingDirectory = $env:WORKDIR; "
        "$s.Description = 'Gato asistente'; $s.Save()"
    )
    env = {**os.environ, "LNK": str(SHORTCUT), "TARGET": str(_pythonw()), "WORKDIR": str(ROOT)}
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    set_enabled("--off" not in sys.argv)
    print("Inicio con Windows:", "activado" if is_enabled() else "desactivado")
