"""Tests del intérprete de fechas y de la memoria.

Uso:  python test_memoria.py
La fecha "ahora" se fija a un lunes concreto para que los tests no
dependan del día en que se ejecuten.
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime

import memoria

# Lunes 31 de agosto de 2026, 19:38
AHORA = datetime(2026, 8, 31, 19, 38)

# (texto, día esperado dd/mm, hora esperada HH:MM o None si no había hora)
CASOS = [
    ("hoy",                        "31/08", None),
    ("mañana",                     "01/09", None),
    ("pasado mañana",              "02/09", None),
    ("el jueves",                  "03/09", None),
    ("jueves",                     "03/09", None),
    ("el lunes",                   "07/09", None),      # hoy es lunes: el que viene
    ("el lunes que viene",         "07/09", None),
    ("mañana a las 8",             "01/09", "08:00"),
    ("mañana a las 8 de la tarde", "01/09", "20:00"),
    ("el jueves a las 19:30",      "03/09", "19:30"),
    ("hoy a las 21",               "31/08", "21:00"),
    ("esta tarde",                 "31/08", "17:00"),
    ("esta noche",                 "31/08", "21:00"),
    # "a las 5" sin más es por la tarde; de 8 a 11 se entiende por la mañana
    ("el jueves a las 5",          "03/09", "17:00"),
    ("el jueves a las 5 de la mañana", "03/09", "05:00"),
    ("el viernes a las 7 y media", "04/09", "19:30"),
    ("mañana a las 9",             "01/09", "09:00"),
    ("mañana a las 11",            "01/09", "11:00"),
    ("el 15 de septiembre",        "15/09", None),
    # "mañana" es ambigua: día siguiente, o franja horaria
    ("mañana por la mañana",       "01/09", "09:00"),
    ("mañana por la tarde",        "01/09", "17:00"),
    ("esta mañana",                "31/08", "09:00"),
    ("el jueves por la noche",     "03/09", "21:00"),
    ("a las 23",                   "31/08", "23:00"),   # solo hora: hoy, aún no ha pasado
    ("a las 6",                    "01/09", "18:00"),   # las 6 = 18:00, y ya pasaron: mañana
    ("",                           None,    None),
    ("cuando pueda",               None,    None),      # no se entiende, y está bien
]

fallos = 0
print("=" * 62)
print("INTERPRETAR CUÁNDO   (ahora: lunes 31/08/2026 19:38)")
print("=" * 62)

for texto, dia_esp, hora_esp in CASOS:
    d, tiene_hora = memoria.interpretar_cuando(texto, ahora=AHORA)
    if d is None:
        dia_real, hora_real = None, None
    else:
        dia_real = d.strftime("%d/%m")
        hora_real = d.strftime("%H:%M") if tiene_hora else None

    ok = (dia_real == dia_esp) and (hora_real == hora_esp)
    fallos += not ok
    marca = "OK " if ok else "MAL"
    esperado = f"{dia_esp} {hora_esp or ''}".strip() or "sin fecha"
    obtenido = f"{dia_real} {hora_real or ''}".strip() if dia_real else "sin fecha"
    linea = f"  {marca} {texto!r:<32} -> {obtenido}"
    if not ok:
        linea += f"   (esperaba {esperado})"
    print(linea)

print("\n" + "=" * 62)
print("DECIRLO EN PALABRAS")
print("=" * 62)
for texto in ("hoy a las 21", "mañana a las 8", "el jueves", "el 15 de septiembre"):
    d, th = memoria.interpretar_cuando(texto, ahora=AHORA)
    print(f"  {texto!r:<24} -> {memoria.en_palabras(d.isoformat(), th, AHORA)!r}")

print("\n" + "=" * 62)
print("LA HORA COMO SE DICE EN VOZ ALTA")
print("=" * 62)
# Todo esto lo lee el sintetizador: "20:21" sonaría a símbolos
HORAS = [
    ((20, 21), "a las 8 y 21 de la tarde"),
    ((20, 0),  "a las 8 de la tarde"),
    ((20, 30), "a las 8 y media de la tarde"),
    ((8, 15),  "a las 8 y cuarto de la mañana"),
    ((8, 45),  "a las 9 menos cuarto de la mañana"),
    ((13, 0),  "a la una de la tarde"),
    ((1, 30),  "a la una y media de la madrugada"),
    ((12, 0),  "al mediodía"),
    ((0, 0),   "a medianoche"),
    ((23, 50), "a las 11 y 50 de la noche"),
]
for (h, mi), esperado in HORAS:
    real = memoria.hora_hablada(h, mi)
    ok = real == esperado
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {h:02d}:{mi:02d} -> {real}"
          + ("" if ok else f"   (esperaba {esperado!r})"))

print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
