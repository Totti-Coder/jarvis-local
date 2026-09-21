"""Rutinas (ver rutinas.py): qué se acepta en atajos.json y qué frase las lanza.

Fichero de rutinas temporal: nunca lee el tuyo. Stdlib pura: corre en el CI.

Uso:  python test_rutinas.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import rutinas

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


DATOS = {
    "atajos": [
        {"nombre": "copia de seguridad", "comando": ["robocopy", "a", "b"]},
        {"nombre": "limpiar temporales", "comando": ["cmd"], "confirma": True},
    ],
    "rutinas": [
        {"nombre": "modo trabajo", "alias": ["empezamos", "a currar"],
         "pasos": [{"abrir": "vs code"}, {"cerrar": "discord"},
                   {"cronometro": "empezar"}, {"horas": "Acme"}],
         "dice": "A por ello."},
        {"nombre": "la hora de comer",
         "pasos": [{"horas": "parar"}, {"cronometro": "pausar"}]},
        {"nombre": "fin del día", "pasos": [{"atajo": "copia de seguridad"}]},
        {"nombre": "foco", "pasos": [{"temporizador": "25"}, {"cronometro": "empezar"}]},
        # --- mal definidas: se saltan ENTERAS ---
        {"nombre": "con comando suelto", "pasos": [{"comando": ["del", "/s", "C:\\"]}]},
        {"nombre": "salta la confirmacion", "pasos": [{"atajo": "limpiar temporales"}]},
        {"nombre": "dos cosas en un paso", "pasos": [{"abrir": "a", "cerrar": "b"}]},
        {"nombre": "valor que no es texto", "pasos": [{"abrir": ["cmd", "/c"]}]},
        {"nombre": "sin pasos", "pasos": []},
        {"nombre": "temporizador en palabras", "pasos": [{"temporizador": "veinte"}]},
        {"nombre": "temporizador eterno", "pasos": [{"temporizador": "99999"}]},
        {"pasos": [{"abrir": "spotify"}]},
    ],
}

with tempfile.TemporaryDirectory() as tmp:
    fichero = Path(tmp) / "atajos.json"
    fichero.write_text(json.dumps(DATOS), encoding="utf-8")
    cargadas = rutinas.cargar(fichero)

print("=" * 72)
print("QUÉ SE ACEPTA")
print("=" * 72)
nombres = [r["nombre"] for r in cargadas]
comprueba("las cuatro bien definidas se cargan",
          nombres == ["modo trabajo", "la hora de comer", "fin del día", "foco"], nombres)
for n in ["con comando suelto", "salta la confirmacion", "dos cosas en un paso",
          "valor que no es texto", "sin pasos", "temporizador en palabras",
          "temporizador eterno"]:
    comprueba(f"se rechaza: {n}", n not in nombres)
comprueba("los pasos quedan como (tipo, valor), en orden",
          cargadas[0]["pasos"] == [("abrir", "vs code"), ("cerrar", "discord"),
                                   ("cronometro", "empezar"), ("horas", "Acme")],
          cargadas[0]["pasos"])
comprueba("sin 'dice', una frase por defecto",
          cargadas[1]["dice"] == "La hora de comer, listo.", cargadas[1]["dice"])
comprueba("sin fichero, ninguna rutina (y no revienta)",
          rutinas.cargar(Path(tmp) / "no-existe.json") == [])

print("\n" + "=" * 72)
print("QUÉ FRASE LAS LANZA")
print("=" * 72)
for frase, esperada in [
    ("Modo trabajo", "modo trabajo"),
    ("Pon el modo trabajo", "modo trabajo"),
    ("Jarvis, activa modo trabajo, por favor", "modo trabajo"),
    ("Empezamos", "modo trabajo"),
    ("Vale, a currar", "modo trabajo"),
    # el nombre empieza por "la": no se lo come el filtro de delante
    ("La hora de comer", "la hora de comer"),
    ("Pon la hora de comer", "la hora de comer"),
    ("Fin del día", "fin del día"),
    # hablar DE la rutina no la lanza: hace varias cosas de golpe
    ("¿Qué es el modo trabajo?", None),
    ("No quiero el modo trabajo", None),
    ("Modo trabajo mañana a las 9", None),
    ("Empezamos con Acme", None),
    ("Cuéntame un chiste", None),
    ("", None),
]:
    r = rutinas.buscar(frase, cargadas)
    obtenida = r["nombre"] if r else None
    comprueba(f"{frase!r:<44} -> {obtenida}", obtenida == esperada, f"esperaba {esperada}")

print("\n" + "=" * 72)
print("CÓMO SE ENSEÑA CADA PASO EN EL PANEL")
print("=" * 72)
for (tipo, valor), esperado in [
    (("abrir", "vs code"), "Abrir vs code"),
    (("cerrar", "discord"), "Cerrar discord"),
    (("atajo", "abre github"), "Atajo · abre github"),
    (("cronometro", "empezar"), "Cronómetro · empezar"),
    (("horas", "Acme"), "Horas · Acme"),
    (("horas", "parar"), "Horas · parar"),
    (("temporizador", "25"), "Pomodoro · 25:00"),
    (("temporizador", "10"), "Temporizador · 10:00"),
]:
    r = rutinas.etiqueta(tipo, valor)
    comprueba(f"{tipo}={valor!r:<14} -> {r}", r == esperado, f"esperaba {esperado!r}")
comprueba("todos los pasos de una rutina cargada tienen etiqueta",
          all(rutinas.etiqueta(t, v) for r in cargadas for t, v in r["pasos"]))

print("\n" + "=" * 72)
print("QUÉ PROCESO CIERRA \"CERRAR ROBLOX\" O \"CERRAR SMITE\"")
print("=" * 72)
import sistema  # noqa: E402

# Lo que devuelve tasklist con los dos juegos abiertos (sin tocar ninguno)
ABIERTOS = {sistema._sin_tildes(e.rsplit(".", 1)[0]): e for e in [
    "RobloxCrashHandler.exe", "RobloxPlayerBeta.exe",       # este orden: el
    "start_protected_game.exe", "Hemingway.exe",           # informe primero
    "Hemingway-Win64-Shipping.exe", "Discord.exe", "chrome.exe"]}
for dicho, esperado in [
    ("roblox", "RobloxPlayerBeta.exe"),          # el juego, no el CrashHandler
    ("smite", "Hemingway-Win64-Shipping.exe"),   # SMITE 2 por dentro es Hemingway
    ("smitegame", "Hemingway-Win64-Shipping.exe"),
    ("smite 2", "Hemingway-Win64-Shipping.exe"),
    ("discord", "Discord.exe"),
]:
    r = sistema.proceso_a_cerrar(dicho, ABIERTOS)
    comprueba(f"{dicho!r:<12} -> {r and r[1]}", r and r[1] == esperado, f"esperaba {esperado}")
comprueba("un juego que no está abierto: nada que cerrar",
          sistema.proceso_a_cerrar("smite", {"chrome": "chrome.exe"}) is None)

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
