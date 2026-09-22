"""El cronómetro: qué orden se entiende y cómo cuenta el tiempo.

Reloj de mentira que se adelanta a mano: nada de esperar de verdad.
Stdlib pura: corre en el CI.

Uso:  python test_cronometro.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from cronometro import Cronometro, orden, tiempo_hablado

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 70)
print("QUÉ ORDEN ES, DICIENDO \"CRONÓMETRO\"")
print("=" * 70)
for frase, esperada in [
    ("Abre el cronómetro",                   "abrir"),
    ("El cronómetro",                        "abrir"),
    ("Jarvis, cronómetro",                   "abrir"),
    ("Empieza el cronómetro",                "empezar"),
    ("Arranca el cronómetro",                "empezar"),
    ("Pon el cronómetro",                    "empezar"),
    ("Enciende el cronómetro",               "empezar"),
    ("Pon en marcha el cronómetro",          "empezar"),
    # "para" es preposición aquí: manda el primer verbo
    ("Pon un cronómetro para la pasta",      "empezar"),
    ("Abre el cronómetro y empiézalo",       "empezar"),
    ("Para el cronómetro",                   "pausar"),
    ("Pausa el cronómetro",                  "pausar"),
    ("Detén el cronómetro",                  "pausar"),
    ("Reanuda el cronómetro",                "reanudar"),
    ("Continúa con el cronómetro",           "reanudar"),
    ("Reinicia el cronómetro",               "reiniciar"),
    # empieza por "pon", pero es ponerlo a cero
    ("Pon el cronómetro a cero",             "reiniciar"),
    ("¿Cuánto lleva el cronómetro?",         "consultar"),
    ("Dime el tiempo exacto del cronómetro", "consultar_exacto"),
    ("Cierra el cronómetro",                 "cerrar"),
    ("Quita el cronómetro",                  "cerrar"),
    # hablar DEL cronómetro no es una orden
    ("¿Qué es un cronómetro?",               None),
    ("Mi abuelo tenía un cronómetro de oro", None),
]:
    r = orden(frase)
    comprueba(f"{frase!r:<42} -> {r}", r == esperada, f"esperaba {esperada}")

print("\n  sueltas, CON el panel abierto:")
for frase, esperada in [
    ("Para", "pausar"), ("Pausa", "pausar"), ("¡Stop!", "pausar"),
    ("Vale, para", "pausar"), ("Sigue", "reanudar"), ("Continúa", "reanudar"),
    ("Empieza", "empezar"), ("Dale", "empezar"), ("Venga", "empezar"),
    ("Ponlo en marcha", "empezar"), ("Ponlo", "empezar"),
    ("Enciéndelo", "empezar"), ("Actívalo", "empezar"), ("Arráncalo", "empezar"),
    ("Ponlo a contar", "empezar"),
    ("Reinicia", "reiniciar"), ("A cero", "reiniciar"),
    ("¿Cuánto llevo?", "consultar"), ("Ciérralo", "cerrar"),
    ("Páralo", "pausar"),
    ("¿Cuánto tiempo llevamos?", "consultar"),
    ("Dime cuánto tiempo llevamos actualmente", "consultar"),
    ("¿Qué tiempo lleva?", "consultar"),
    ("¿Cuánto va?", "consultar"),
    ("Dime el tiempo exacto", "consultar_exacto"),
    ("¿Cuánto tiempo llevamos exactamente?", "consultar_exacto"),
]:
    r = orden(frase, visible=True)
    comprueba(f"{frase!r:<42} -> {r}", r == esperada, f"esperaba {esperada}")

print("\n  y lo que NO es una orden aunque el panel esté abierto:")
for frase in ["Para mañana apunta el dentista", "Sigue lloviendo en Madrid",
              "¿Qué tengo para hoy?", "Cuéntame un chiste", "Empieza a llover",
              # preguntan "cuánto", pero no por el cronómetro
              "¿Cuánto cuesta el pan?", "¿Qué tiempo hace en Madrid?",
              "¿Cuánto tiempo tarda el tren a Sevilla?"]:
    r = orden(frase, visible=True)
    comprueba(f"{frase!r:<42} -> nada", r is None, str(r))

print("\n  sin el panel, \"para\" suelto no toca nada:")
for frase in ["Para", "Sigue", "Dale", "Reinicia"]:
    r = orden(frase, visible=False)
    comprueba(f"{frase!r:<42} -> nada", r is None, str(r))


print("\n  lo que Whisper escribe mal, y aun así es una orden:")
for frase, esperada in [
    ("Jarvis en 100 el cronómetro", "empezar"),      # "enciende" -> "en-cien-de"
    ("En cien de el cronómetro", "empezar"),
    ("Encienda el cronómetro", "empezar"),
    ("En pieza el cronómetro", "empezar"),
    ("Lo empiezo al cronómetro", "empezar"),          # en primera persona
    ("Jarvis, cronómetro ya", "abrir"),               # corta y sin verbo: abrirlo
]:
    r = orden(frase)
    comprueba(f"{frase!r:<42} -> {r}", r == esperada, f"esperaba {esperada}")

print("\n  hablar DEL cronómetro, aunque lleve un verbo, no es una orden:")
for frase in ["¿Para qué sirve un cronómetro?", "¿Cómo funciona el cronómetro?",
              "Me regalaron un cronómetro", "¿Cuál es el mejor cronómetro?"]:
    r = orden(frase)
    comprueba(f"{frase!r:<42} -> nada", r is None, str(r))

print("\n  varias órdenes en una frase:")
from cronometro import es_orden_sin_contenido, trozos  # noqa: E402
for frase, esperado in [
    ("Lo empiezo al cronómetro y avísame los 25 minutos.",
     ["Lo empiezo al cronómetro", "avísame los 25 minutos."]),
    ("Para el cronómetro, y luego dime cuánto lleva",
     ["Para el cronómetro", "dime cuánto lleva"]),
    ("Empieza el cronómetro", ["Empieza el cronómetro"]),
]:
    r = trozos(frase)
    comprueba(f"{frase!r:<52} -> {len(r)} trozos", r == esperado, r)

print("\n  una \"tarea\" que en realidad era una orden mal entendida:")
for texto, esperado in [
    ("empiezo al cronómetro", True), ("cronómetro", True),
    ("avisar a los 25 minutos que hayan pasado el cronómetro", True),
    ("comprar un cronómetro nuevo", False),           # esta SÍ es una tarea
    ("devolver el temporizador a Ana", False),
    ("llamar al banco", False),
]:
    r = es_orden_sin_contenido(texto)
    comprueba(f"{texto!r:<56} -> {r}", r == esperado)

print("\n" + "=" * 70)
print("CÓMO SE DICE UN TIEMPO")
print("=" * 70)
for s, esperado in [
    (0.4, "menos de un segundo"), (1, "un segundo"), (7.9, "7 segundos"),
    (60, "un minuto"), (61, "un minuto y un segundo"),
    (125, "2 minutos y 5 segundos"), (3600, "una hora"),
    (3725, "una hora y 2 minutos"), (7384, "2 horas y 3 minutos"),
]:
    r = tiempo_hablado(s)
    comprueba(f"{s:>7} s -> {r}", r == esperado, f"esperaba {esperado!r}")

print("\n  exacto, con décimas:")
for s, esperado in [
    (83.7, "un minuto, 23 segundos y 7 décimas"), (5.1, "5 segundos y una décima"),
    (0.4, "4 décimas"), (60.0, "un minuto"),
]:
    r = tiempo_hablado(s, decimas=True)
    comprueba(f"{s:>7} s -> {r}", r == esperado, f"esperaba {esperado!r}")


print("\n" + "=" * 70)
print("CÓMO CUENTA")
print("=" * 70)
t = [1000.0]
c = Cronometro(reloj=lambda: t[0])

comprueba("empieza oculto y a cero",
          c.estado() == {"visible": False, "corriendo": False, "segundos": 0.0},
          c.estado())
f = c.aplicar("abrir")
comprueba("abrir lo enseña pero NO lo arranca",
          c.visible and not c.corriendo and "empieza" in f, f)

c.aplicar("empezar")
t[0] += 83
comprueba("en marcha, cuenta el tiempo", c.transcurrido() == 83, c.transcurrido())
f = c.aplicar("pausar")
comprueba("pausar dice dónde se quedó", f == "Parado en un minuto y 23 segundos.", f)
t[0] += 500
comprueba("parado, el tiempo no corre", c.transcurrido() == 83, c.transcurrido())
f = c.aplicar("pausar")
comprueba("pausar dos veces no rompe nada", "Ya estaba parado" in f, f)

f = c.aplicar("empezar")
comprueba("empezar tras una pausa SIGUE, no empieza de cero",
          f.startswith("Sigue desde") and c.corriendo, f)
t[0] += 17
comprueba("y suma los dos tramos", c.transcurrido() == 100, c.transcurrido())
comprueba("consultar en marcha", c.aplicar("consultar") == "Lleva un minuto y 40 segundos.")
t[0] += 0.7
f = c.aplicar("consultar_exacto")
comprueba("exacto, con décimas", f == "Lleva un minuto, 40 segundos y 7 décimas.", f)
t[0] -= 0.7

f = c.aplicar("reiniciar")
comprueba("reiniciar en marcha: a cero y sigue",
          c.transcurrido() == 0 and c.corriendo, f)
t[0] += 5
c.aplicar("pausar")
c.aplicar("reiniciar")
comprueba("reiniciar parado: a cero y quieto",
          c.transcurrido() == 0 and not c.corriendo)

c.aplicar("empezar")
t[0] += 42
f = c.aplicar("cerrar")
comprueba("cerrar dice el tiempo y lo deja todo a cero",
          "42 segundos" in f and c.estado() == {"visible": False, "corriendo": False,
                                               "segundos": 0.0}, f)
comprueba("con él cerrado, empezar lo vuelve a abrir",
          c.aplicar("empezar") == "En marcha." and c.visible)

print("\n" + "=" * 70)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 70)
sys.exit(1 if fallos else 0)
