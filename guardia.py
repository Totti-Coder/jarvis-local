"""Quién puede hablar con Jarvis.

EL FALLO QUE TAPA ESTO

Los navegadores NO aplican la regla del mismo origen a los WebSockets. Si
tienes Jarvis en marcha y abres cualquier web, esa web puede conectarse a
ws://localhost:8000/ws por su cuenta: grabar con tu micrófono y recibir la
transcripción, pedir que te lea los correos y quedarse con ellos, o llevar
paso a paso un envío desde tu Gmail (la confirmación no frena nada: la
pregunta pendiente va por conexión, y la conexión es suya).

Es la misma clase de fallo que el CVE-2026-25253 de OpenClaw (CVSS 8.8):
visitar una página bastaba para tomar el control del agente.

TRES DEFENSAS, CADA UNA CONTRA UN ATAQUE

  1. Origin. El navegador dice desde qué página sale la conexión, y la
     página no puede mentir en eso. Solo vale la propia interfaz.
     -> contra la web maliciosa.

  2. Host en lista blanca. El truco para saltarse el punto 1 es el DNS
     rebinding: malo.com empieza apuntando a su servidor y luego a
     127.0.0.1. Para el navegador la página y el socket son el mismo
     origen (malo.com), así que Origin coincide. Pero el Host que llega es
     "malo.com", y ese no está en la lista.
     -> contra el rebinding.

  3. PIN en modo --red, para cualquiera que no sea este mismo PC. Sin él,
     cualquiera en tu wifi abría la interfaz y usaba Jarvis entero.
     -> contra el vecino de la cafetería.

Lo que NO pretende: un programa ya instalado en tu PC puede falsificar
cualquier cabecera. Pero ese ya tiene tu PC; no hay nada que proteger.

Stdlib pura: corre en el CI.
"""

import hmac
import ipaddress
import secrets
from urllib.parse import urlsplit

LOCALES = {"localhost", "127.0.0.1", "::1"}

# Los nombres por los que se puede llegar a este servidor. Se amplía al
# arrancar con --red (las IPs del equipo en la red local).
HOSTS = set(LOCALES)

INTENTOS_PIN = 5


def _nombre_de(netloc):
    """"localhost:8000" -> "localhost"; "[::1]:8000" -> "::1"."""
    try:
        return (urlsplit("//" + (netloc or "")).hostname or "").lower()
    except ValueError:
        return ""


def es_local(ip):
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def motivo_rechazo(origin, host, hosts=None):
    """Por qué se rechaza la conexión, o None si vale.

    Devuelve el motivo y no un booleano: en el registro del servidor
    "origen ajeno: https://malo.com" dice mucho más que "rechazada".
    """
    hosts = HOSTS if hosts is None else hosts
    if _nombre_de(host) not in hosts:
        return f"host desconocido: {host!r}"
    if not origin:
        # Un navegador siempre lo manda en un WebSocket
        return "sin origen"
    try:
        partes = urlsplit(origin)
    except ValueError:
        return f"origen ilegible: {origin!r}"
    # Mismo origen: la página y el socket en el mismo nombre y puerto. Y
    # ese nombre también de la lista (si no, el rebinding pasaría por aquí)
    if partes.netloc.lower() != (host or "").lower():
        return f"origen ajeno: {origin}"
    if (partes.hostname or "").lower() not in hosts:
        return f"origen desconocido: {origin}"
    return None


class Llave:
    """El PIN de --red. Se genera al arrancar y se ve en la pantalla del PC.

    Seis cifras son un millón de combinaciones, que a fuerza bruta caen en
    minutos. Por eso hay un tope: tras cinco fallos se bloquea hasta
    reiniciar el servidor, y quien lo tenga delante del PC lo verá.
    """

    def __init__(self, pin=None):
        self.pin = pin or f"{secrets.randbelow(10**6):06d}"
        self.fallos = 0

    @property
    def bloqueada(self):
        return self.fallos >= INTENTOS_PIN

    def comprobar(self, intento):
        if self.bloqueada:
            return False
        # compare_digest tarda lo mismo acierte o no: el tiempo de
        # respuesta no chiva cuántas cifras van bien
        ok = hmac.compare_digest(str(intento or "").strip(), self.pin)
        if not ok and intento:
            self.fallos += 1
        return ok


LLAVE = None      # solo existe con --red
