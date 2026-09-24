"""Qué URLs se pueden abrir al leer la web (ver url_segura.py).

Las URLs vienen del buscador, no del usuario: si una apunta a tu red, se
leería desde tu PC. Aquí se comprueba que no.

El DNS es de mentira, así que el test no sale a internet y corre en el CI.

Uso:  python test_url_segura.py
"""

import http.client
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

import url_segura

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


# DNS de mentira: qué responde cada nombre
DNS = {
    "www.rtve.es": ["193.110.128.11"],
    "open-meteo.com": ["104.21.3.4"],
    "ipv6.example": ["2606:4700:4700::1111"],
    "localhost": ["127.0.0.1", "::1"],
    "interno.local": ["10.0.0.5"],
    # El truco clásico: un nombre público que resuelve a una dirección privada
    "malo.com": ["192.168.1.50"],
    # Y el mixto: una pública y una privada. Basta una mala para no abrirlo
    "mixto.com": ["1.2.3.4", "127.0.0.1"],
    "nube.com": ["169.254.169.254"],      # metadatos de una nube: el clásico
}


def dns(host):
    return DNS.get(host, [])


print("=" * 74)
print("LO QUE SÍ SE PUEDE LEER")
print("=" * 74)
for url in ["https://www.rtve.es/noticias", "http://www.rtve.es/",
            "https://open-meteo.com/en/docs", "https://www.rtve.es:443/",
            "http://ipv6.example/", "https://1.2.3.4/pagina"]:
    m = url_segura.motivo_rechazo(url, dns)
    comprueba(f"{url:<40} -> se abre", m is None, m)

print("\n" + "=" * 74)
print("LO QUE NO (Y POR QUÉ)")
print("=" * 74)
for url, esperado in [
    ("http://127.0.0.1:8000/", "puerto"),            # el propio Jarvis
    ("http://127.0.0.1/", "red local"),
    ("http://localhost/admin", "red local"),
    ("http://[::1]/", "red local"),
    ("http://192.168.1.1/", "red local"),            # el router de casa
    ("http://10.0.0.5/panel", "red local"),
    ("http://172.16.4.4/", "red local"),
    ("http://interno.local/", "red local"),
    ("http://malo.com/", "red local"),               # nombre público, IP privada
    ("http://mixto.com/", "red local"),              # una de las dos es mala
    ("http://nube.com/latest/meta-data/", "red local"),
    ("http://0.0.0.0/", "red local"),
    ("file:///C:/Windows/win.ini", "esquema"),
    ("ftp://archivos.com/x", "esquema"),
    ("gopher://viejo.net/", "esquema"),
    ("https://sitio.com:8080/panel", "puerto"),
    ("https://sitio.com:22/", "puerto"),
    ("https://usuario:clave@sitio.com/", "usuario"),
    ("https://no-existe-esto.com/", "no se resuelve"),
    ("http://", "sin destino"),
]:
    m = url_segura.motivo_rechazo(url, dns) or ""
    comprueba(f"{url:<40} -> {m}", esperado in m, f"esperaba algo de {esperado!r}")

print("\n" + "=" * 74)
print("Y TAMPOCO SALTANDO: UNA PÚBLICA QUE REDIRIGE A UNA PRIVADA")
print("=" * 74)
salto = url_segura._SinSaltosPeligrosos(dns)
peticion = urllib.request.Request("https://www.rtve.es/")
cabeceras = http.client.HTTPMessage()

try:
    salto.redirect_request(peticion, None, 302, "Found", cabeceras,
                           "http://127.0.0.1:80/admin")
    comprueba("una redirección a tu propio PC se corta", False, "la dejó pasar")
except urllib.error.HTTPError as e:
    comprueba(f"una redirección a tu propio PC se corta: {e.reason[:46]}", True)

try:
    salto.redirect_request(peticion, None, 302, "Found", cabeceras,
                           "http://malo.com/")
    comprueba("y a un nombre que apunta a tu red, también", False, "la dejó pasar")
except urllib.error.HTTPError:
    comprueba("y a un nombre que apunta a tu red, también", True)

r = salto.redirect_request(peticion, None, 302, "Found", cabeceras,
                           "https://open-meteo.com/otra")
comprueba("una redirección normal sigue funcionando", r is not None)

print("\n" + "=" * 74)
print("ABRIR DE VERDAD FALLA ANTES DE CONECTAR")
print("=" * 74)
try:
    url_segura.abrir(urllib.request.Request("http://192.168.1.1/"), 2, dns)
    comprueba("abrir() bloquea antes de tocar la red", False, "no lanzó nada")
except PermissionError as e:
    comprueba(f"abrir() bloquea antes de tocar la red: {str(e)[:50]}", True)

print("\n" + "=" * 74)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 74)
sys.exit(1 if fallos else 0)
