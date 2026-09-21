"""Quién puede conectarse a Jarvis (ver guardia.py).

Cada caso es un ataque concreto, o una conexión legítima que NO puede
romperse al taparlo. Stdlib pura: corre en el CI.

Uso:  python test_guardia.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import guardia

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


LOCAL = set(guardia.LOCALES)
RED = LOCAL | {"192.168.1.40", "mi-pc"}       # como queda con --red

print("=" * 72)
print("LO LEGÍTIMO SIGUE ENTRANDO")
print("=" * 72)
for origin, host, hosts in [
    ("http://localhost:8000", "localhost:8000", LOCAL),
    ("http://127.0.0.1:8000", "127.0.0.1:8000", LOCAL),
    ("http://[::1]:8000", "[::1]:8000", LOCAL),
    ("https://192.168.1.40:8000", "192.168.1.40:8000", RED),     # el móvil
    ("https://mi-pc:8000", "mi-pc:8000", RED),
    ("http://LOCALHOST:8000", "localhost:8000", LOCAL),            # mayúsculas
]:
    r = guardia.motivo_rechazo(origin, host, hosts)
    comprueba(f"{origin:<28} -> entra", r is None, r)

print("\n" + "=" * 72)
print("LOS ATAQUES SE QUEDAN FUERA")
print("=" * 72)
for titulo, origin, host, hosts in [
    # 1. Una web cualquiera abierta en tu PC (el CVE de OpenClaw)
    ("web maliciosa -> localhost", "https://malo.com", "localhost:8000", LOCAL),
    ("web maliciosa, http", "http://malo.com", "127.0.0.1:8000", LOCAL),
    # Otra cosa que corre en tu PC, en otro puerto (un dev server, otra app)
    ("otra app local en otro puerto", "http://localhost:3000", "localhost:8000", LOCAL),
    # 2. DNS rebinding: malo.com pasa a apuntar a 127.0.0.1. Origin y Host
    #    coinciden entre sí; lo que falla es que el nombre no es nuestro
    ("DNS rebinding", "http://malo.com:8000", "malo.com:8000", LOCAL),
    ("rebinding con subdominio", "http://localhost.malo.com:8000",
     "localhost.malo.com:8000", LOCAL),
    # Sin cabecera: un navegador siempre la manda en un WebSocket
    ("sin Origin", None, "localhost:8000", LOCAL),
    ("Origin 'null' (iframe con sandbox, file://)", "null", "localhost:8000", LOCAL),
    ("sin Host", "http://localhost:8000", None, LOCAL),
    # En --red, una IP de la lista no autoriza un origen de fuera
    ("--red: web ajena contra la IP de la red", "https://malo.com",
     "192.168.1.40:8000", RED),
    # Sin --red, la IP de la red ni siquiera está en la lista
    ("IP de la red sin --red", "https://192.168.1.40:8000",
     "192.168.1.40:8000", LOCAL),
]:
    r = guardia.motivo_rechazo(origin, host, hosts)
    comprueba(f"{titulo:<44} -> {r}", r is not None)

print("\n" + "=" * 72)
print("QUIÉN ES ESTE MISMO PC")
print("=" * 72)
for ip, esperado in [("127.0.0.1", True), ("::1", True), ("127.0.0.5", True),
                     ("192.168.1.33", False), ("10.0.0.2", False), ("", False),
                     ("no-es-una-ip", False)]:
    comprueba(f"{ip!r:<16} local={esperado}", guardia.es_local(ip) == esperado)

print("\n" + "=" * 72)
print("EL PIN DE --red")
print("=" * 72)
llave = guardia.Llave()
comprueba("seis cifras", len(llave.pin) == 6 and llave.pin.isdigit(), llave.pin)
comprueba("cambia en cada arranque",
          len({guardia.Llave().pin for _ in range(20)}) > 1)

llave = guardia.Llave("123456")
comprueba("el bueno entra", llave.comprobar("123456"))
comprueba("con espacios alrededor también (teclado del móvil)",
          llave.comprobar(" 123456 "))
comprueba("sin PIN no entra, pero NO cuenta como fallo",
          not llave.comprobar(None) and not llave.comprobar("") and llave.fallos == 0)
for i in range(guardia.INTENTOS_PIN - 1):
    llave.comprobar(f"00000{i}")
comprueba("cuatro fallos: todavía no se bloquea", not llave.bloqueada, llave.fallos)
llave.comprobar("999999")
comprueba("al quinto se bloquea", llave.bloqueada, llave.fallos)
comprueba("y bloqueada, ni el bueno entra (fuerza bruta frenada)",
          not llave.comprobar("123456"))

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
