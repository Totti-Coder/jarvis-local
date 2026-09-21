"""El temporizador (ver temporizador.py): qué frase lo pone y cómo lo dice.

Que suene de verdad al acabar lo prueba test_avisos.py, con el servidor.
Stdlib pura: corre en el CI.

Uso:  python test_temporizador.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import temporizador as T

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 72)
print("QUÉ FRASE LO PONE")
print("=" * 72)
for frase, activo, esperado in [
    ("Pon un temporizador de 25 minutos",      False, ("poner", 1500)),
    ("Temporizador de media hora",             False, ("poner", 1800)),
    ("Ponme un temporizador de hora y media",  False, ("poner", 5400)),
    ("Ponme un pomodoro",                      False, ("poner", 1500)),
    ("Pomodoro",                               False, ("poner", 1500)),
    ("Otro pomodoro",                          False, ("poner", 1500)),
    ("Avísame en 10 minutos",                  False, ("poner", 600)),
    ("Avísame en diez minutos por favor",      False, ("poner", 600)),
    ("Avísame dentro de un cuarto de hora",    False, ("poner", 900)),
    ("Pon un temporizador",                    False, ("sin_duracion", None)),
    ("¿Cuánto queda?",                         True,  ("consultar", None)),
    ("¿Cuánto le queda al temporizador?",      False, ("consultar", None)),
    ("¿Cómo va el pomodoro?",                  True,  ("consultar", None)),
    ("Cancela el temporizador",                True,  ("cancelar", None)),
    ("Para el pomodoro",                       True,  ("cancelar", None)),
    ("Quita el temporizador",                  False, ("cancelar", None)),
    ("Cancélalo",                              True,  ("cancelar", None)),
]:
    r = T.orden(frase, activo)
    comprueba(f"{frase!r:<42} activo={activo!s:<5} -> {r}", r == esperado, f"esperaba {esperado}")

print("\n  lo que NO es un temporizador:")
for frase, activo in [
    # con contenido es una TAREA con hora: va a la agenda y avisa igual
    ("Recuérdame sacar la pizza en 10 minutos", False),
    ("Avísame en 10 minutos de sacar la pizza", False),
    ("Avísame en 10 minutos que tengo que llamar", False),
    # hablar DEL pomodoro no pide uno
    ("¿Qué es un pomodoro?", False),
    ("¿Qué es un temporizador?", False),
    # sin temporizador, "¿cuánto queda?" es otra pregunta
    ("¿Cuánto queda?", False),
    ("¿Cuánto queda para Navidad?", False),
    ("Cancélalo", False),
    ("Para el cronómetro", True),
    ("Apunta 2 horas a Acme", False),
]:
    r = T.orden(frase, activo)
    comprueba(f"{frase!r:<42} activo={activo!s:<5} -> nada", r is None, str(r))

print("\n" + "=" * 72)
print("CÓMO LO DICE")
print("=" * 72)
for r, esperado in [
    (T.frase_puesto(1500), "Pomodoro en marcha: veinticinco minutos. Te aviso al acabar."),
    (T.frase_puesto(600), "Temporizador de 10 minutos. Te aviso al acabar."),
    (T.frase_acabado(1500), "Pomodoro terminado. Toca descansar cinco minutos."),
    (T.frase_acabado(600), "Se acabó el tiempo: 10 minutos."),
    (T.frase_queda(125.4), "Quedan 2 minutos y 5 segundos."),
    (T.frase_queda(0), "Está a punto de sonar."),
]:
    comprueba(r, r == esperado, f"esperaba {esperado!r}")

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
