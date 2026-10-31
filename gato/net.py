"""Red: preferir IPv4.

En esta conexión, abrir una conexión IPv6 a Groq a veces tarda 1 a 20 segundos tras un rato sin uso, mientras que
IPv4 responde siempre en milisegundos. Python prueba las direcciones en el orden que da el DNS (IPv6 primero),
así que un IPv6 trabado demoraba cada respuesta. Reordenando, IPv6 queda solo como plan B."""
import socket

_getaddrinfo = socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    return sorted(_getaddrinfo(*args, **kwargs), key=lambda info: info[0] != socket.AF_INET)


def prefer_ipv4():
    socket.getaddrinfo = _ipv4_first
