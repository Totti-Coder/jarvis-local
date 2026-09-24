"""Qué URLs puede abrir Jarvis al leer la web, y cuáles no.

EL PROBLEMA (SSRF)

Cuando Jarvis busca algo, abre las páginas que le devuelve el buscador.
Esas URLs no las escribe el usuario: vienen de fuera. Si una apuntara a
127.0.0.1 o a una IP de la red local, Jarvis la leería DESDE TU PC, que es
justo donde hay acceso a cosas que internet no ve: el router, una NAS, un
panel de administración sin contraseña, o el propio Jarvis.

Es la familia SSRF (Server-Side Request Forgery), y lo que la hace fea es
que el atacante no necesita entrar en tu red: le basta con que algo de
dentro lea una URL suya.

QUÉ SE COMPRUEBA

  - El esquema: solo http y https. Nada de file://, ftp:// ni gopher://.
  - El puerto: solo 80 y 443. Los paneles internos viven en otros.
  - El destino: se resuelve el nombre y se miran TODAS sus direcciones. Si
    alguna es privada, local o reservada, no se abre.
  - Los saltos: una página pública puede redirigir a una privada, así que
    cada redirección se vuelve a comprobar.

LO QUE NO CUBRE (a propósito, y conviene saberlo)

Entre que se comprueba el nombre y que se abre la conexión, un DNS podría
cambiar la respuesta (DNS rebinding). Taparlo del todo obliga a conectarse
a la IP ya comprobada y falsear la cabecera Host, que rompe muchos sitios.
Para leer una noticia no compensa; para algo que mande dinero, sí.

Stdlib pura: corre en el CI, y el DNS se puede sustituir en los tests.
"""

import ipaddress
import socket
import urllib.parse
import urllib.request

PUERTOS = {80, 443}
ESQUEMAS = {"http", "https"}


def resolver(host):
    """Todas las direcciones de un nombre. Se sustituye en los tests."""
    try:
        return [i[4][0] for i in socket.getaddrinfo(host, None)]
    except OSError:
        return []


def es_interna(ip):
    """¿Esa dirección es de tu equipo o de tu red?"""
    try:
        d = ipaddress.ip_address(ip)
    except ValueError:
        return True          # si no se entiende, no se abre
    return (d.is_private or d.is_loopback or d.is_link_local
            or d.is_reserved or d.is_multicast or d.is_unspecified)


def motivo_rechazo(url, dns=None):
    """Por qué no se puede abrir esa URL, o None si se puede."""
    dns = dns or resolver
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return "url ilegible"

    if p.scheme.lower() not in ESQUEMAS:
        return f"esquema {p.scheme!r}"
    if p.username or p.password:
        return "lleva usuario y contraseña dentro"
    try:
        puerto = p.port or (443 if p.scheme.lower() == "https" else 80)
    except ValueError:
        return "puerto ilegible"
    if puerto not in PUERTOS:
        return f"puerto {puerto}"

    host = (p.hostname or "").strip("[]")
    if not host:
        return "sin destino"

    # Una IP escrita a pelo no necesita DNS
    try:
        ipaddress.ip_address(host)
        direcciones = [host]
    except ValueError:
        direcciones = dns(host)
        if not direcciones:
            return f"no se resuelve {host!r}"

    for ip in direcciones:
        if es_interna(ip):
            return f"{host} apunta a {ip}, que es de la red local"
    return None


class _SinSaltosPeligrosos(urllib.request.HTTPRedirectHandler):
    """Una página pública puede redirigir a una privada: se revisa cada salto."""

    def __init__(self, dns=None):
        self.dns = dns

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        motivo = motivo_rechazo(newurl, self.dns)
        if motivo:
            raise urllib.error.HTTPError(
                newurl, code, f"redirección bloqueada: {motivo}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def abrir(peticion, timeout, dns=None):
    """urlopen, pero comprobando el destino y cada redirección."""
    url = peticion.full_url if hasattr(peticion, "full_url") else peticion
    motivo = motivo_rechazo(url, dns)
    if motivo:
        raise PermissionError(f"URL bloqueada ({motivo}): {url[:80]}")
    abridor = urllib.request.build_opener(_SinSaltosPeligrosos(dns))
    return abridor.open(peticion, timeout=timeout)
