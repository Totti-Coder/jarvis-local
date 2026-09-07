"""Tests de tiempo relativo y de ventanas: "¿qué tengo en 30 minutos?".

Uso:  python test_ventanas.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_ventanas.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

# Lunes 31 de agosto de 2026, las 20:00
AHORA = datetime(2026, 8, 31, 20, 0)

fallos = 0

print("=" * 68)
print("TIEMPO RELATIVO   (ahora: lunes 31/08 20:00)")
print("=" * 68)

RELATIVOS = [
    ("en 30 minutos",        "31/08 20:30"),
    ("dentro de 30 minutos", "31/08 20:30"),
    ("en media hora",        "31/08 20:30"),
    ("en 15 min",            "31/08 20:15"),
    ("en una hora",          "31/08 21:00"),
    ("dentro de dos horas",  "31/08 22:00"),
    ("en tres dias",         "03/09 20:00"),
    ("ahora",                "31/08 20:00"),
]
for texto, esperado in RELATIVOS:
    d, _ = memoria.interpretar_cuando(texto, ahora=AHORA)
    real = d.strftime("%d/%m %H:%M") if d else "NO"
    ok = real == esperado
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {texto:<24} -> {real}"
          + ("" if ok else f"   (esperaba {esperado})"))

print("\n" + "=" * 68)
print("EL CASO DE VERDAD: tarea a las 20:30, pregunto por los proximos 30 min")
print("=" * 68)

# Se fija el "ahora" para que las tareas se guarden con la fecha correcta
_orig = memoria.interpretar_cuando
memoria.interpretar_cuando = lambda t, ahora=None: _orig(t, ahora or AHORA)

memoria.anadir_tarea("sacar la basura", "hoy a las 20:30")
memoria.anadir_tarea("ver la peli", "hoy a las 22:30")
memoria.anadir_tarea("dentista", "mañana a las 9 de la mañana")
memoria.anadir_tarea("comprar pan", "mañana")

CASOS = [
    # franjas del dia: no es lo mismo "mañana" que "mañana por la tarde"
    ("mañana por la mañana", ["dentista"], ["ver la peli", "sacar la basura"]),
    ("mañana por la tarde",  [], ["dentista", "sacar la basura"]),
    ("en 30 minutos",  ["sacar la basura"], ["ver la peli", "dentista"]),
    ("en una hora",    ["sacar la basura"], ["ver la peli"]),
    ("en tres horas",  ["sacar la basura", "ver la peli"], ["dentista"]),
    ("hoy",            ["sacar la basura", "ver la peli"], ["dentista", "comprar pan"]),
    ("mañana",         ["dentista", "comprar pan"], ["sacar la basura"]),
    ("",               ["sacar la basura", "dentista", "comprar pan"], []),
]

for cuando, deben_estar, no_deben in CASOS:
    r = memoria.listar_tareas(cuando, ahora=AHORA)
    ok = (all(t in r for t in deben_estar) and
          all(t not in r for t in no_deben))
    fallos += not ok
    print(f"\n  {'OK ' if ok else 'MAL'} \"¿qué tengo {cuando or '(todo)'}?\"")
    print(f"      {r}")
    if not ok:
        faltan = [t for t in deben_estar if t not in r]
        sobran = [t for t in no_deben if t in r]
        if faltan:
            print(f"      FALTA: {faltan}")
        if sobran:
            print(f"      SOBRA: {sobran}")

memoria.interpretar_cuando = _orig
try:
    os.remove(memoria.BASE)
except OSError:
    pass


# --------------------------------------------------------------
# TRAMOS ENTRE DOS HORAS
# --------------------------------------------------------------
print("\n" + "=" * 68)
print("TRAMOS: \"entre las 2 y las 5 de la tarde\"")
print("=" * 68)

MEDIODIA = datetime(2026, 8, 31, 10, 0)   # lunes a las 10:00
RANGOS = [
    ("entre las 2 y las 5 de la tarde",   "31/08 14:00", "31/08 17:00"),
    ("entre las 2 y las 5",               "31/08 14:00", "31/08 17:00"),
    ("de 2 a 5 de la tarde",              "31/08 14:00", "31/08 17:00"),
    ("desde las 14 hasta las 17",         "31/08 14:00", "31/08 17:00"),
    ("entre las 10 y las 2",              "31/08 10:00", "31/08 14:00"),
    ("entre las 9 y las 11 de la manana", "31/08 09:00", "31/08 11:00"),
    ("manana entre las 2 y las 5",        "01/09 14:00", "01/09 17:00"),
    ("el jueves entre las 9 y las 11",    "03/09 09:00", "03/09 11:00"),
    ("entre las 20:30 y las 22:15",       "31/08 20:30", "31/08 22:15"),
]
for texto, ei, ef in RANGOS:
    d, h = memoria.interpretar_ventana(texto, MEDIODIA)
    ri = f"{d:%d/%m %H:%M}" if d else "NO"
    rf = f"{h:%d/%m %H:%M}" if h else "NO"
    ok = ri == ei and rf == ef
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {texto:<36} {ri} -> {rf}"
          + ("" if ok else f"   (esperaba {ei} -> {ef})"))

print("\n" + "=" * 68)
print('EL FIN DE SEMANA: viernes, sabado y domingo (no solo el sabado)')
print("=" * 68)
# Preguntando "que tengo el fin de semana" no habia ventana ninguna, asi
# que se listaba TODO lo pendiente: salia el examen del martes y hasta
# una tarea basura de hacia cuatro dias.
# Se cuenta el viernes entero porque el finde empieza al salir el viernes.
FINDES = [
    # (dia desde el que se pregunta, frase, desde, hasta)
    ("2026-09-07", "el fin de semana",         "11/09 00:00", "13/09 23:59"),  # lunes
    ("2026-09-10", "el fin de semana",         "11/09 00:00", "13/09 23:59"),  # jueves
    # ya dentro del finde: es ESTE, no el de dentro de siete dias
    ("2026-09-11", "este fin de semana",       "11/09 00:00", "13/09 23:59"),  # viernes
    ("2026-09-12", "el finde",                 "11/09 00:00", "13/09 23:59"),  # sabado
    ("2026-09-13", "el fin de semana",         "11/09 00:00", "13/09 23:59"),  # domingo
    # el siguiente, dicho de varias maneras
    ("2026-09-07", "el proximo fin de semana", "18/09 00:00", "20/09 23:59"),
    ("2026-09-07", "el finde que viene",       "18/09 00:00", "20/09 23:59"),
]
for dia, texto, ei, ef in FINDES:
    hoy = datetime.fromisoformat(dia + "T12:00:00")
    d, h = memoria.interpretar_ventana(texto, hoy)
    ri = f"{d:%d/%m %H:%M}" if d else "NO"
    rf = f"{h:%d/%m %H:%M}" if h else "NO"
    ok = ri == ei and rf == ef
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {dia} {texto:<26} {ri} -> {rf}"
          + ("" if ok else f"   (esperaba {ei} -> {ef})"))

print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
