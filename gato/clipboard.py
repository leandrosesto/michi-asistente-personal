"""Portapapeles de Windows (texto) sin dependencias extra, y pegar donde esté el cursor."""
import ctypes
import time
from ctypes import wintypes

import keyboard

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)


def _open():
    # Otra app puede tener el portapapeles abierto un instante: reintentar un poco
    for _ in range(20):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_text() -> str | None:
    if not _open():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> bool:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    if not _open():
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, size)
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
    finally:
        user32.CloseClipboard()


def paste_text(text: str):
    """Escribe el texto en la ventana activa pegándolo, y después devuelve el portapapeles como estaba."""
    previous = get_text()
    set_text(text)
    keyboard.send("ctrl+v")
    time.sleep(0.4)  # dar tiempo a que la app lea el portapapeles antes de restaurarlo
    if previous is not None:
        set_text(previous)
