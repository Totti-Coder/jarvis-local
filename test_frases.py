"""¿Parte bien las frases para la voz, sin cortar horas ni decimales?

Simula la llegada de tokens uno a uno, como hace el streaming de verdad.

Uso:  python test_frases.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")
import servidor

fallos = 0

# (texto completo, cómo debe quedar troceado para la voz)
CASOS = [
    ("Tienes que ir al tren a las 20.40 de hoy. Nada más. ",
     ["Tienes que ir al tren a las 20.40 de hoy.", "Nada más."]),

    ("Son las 20.21 y tienes cita. Te queda poco. ",
     ["Son las 20.21 y tienes cita.", "Te queda poco."]),

    ("El pintor fue Velázquez. Es su obra maestra. ",
     ["El pintor fue Velázquez.", "Es su obra maestra."]),

    ("El Sr. García llamó ayer. Dijo que volvería. ",
     ["El Sr. García llamó ayer.", "Dijo que volvería."]),

    ("Cuesta 12.50 euros en total. Es barato. ",
     ["Cuesta 12.50 euros en total.", "Es barato."]),

    ("¿Qué tal estás? Yo bien. ",
     ["¿Qué tal estás?", "Yo bien."]),
]

print("=" * 70)
print("TROCEADO DE FRASES PARA LA VOZ")
print("=" * 70)

for texto, esperado in CASOS:
    # Se alimenta carácter a carácter, como llegan los tokens
    buf = ""
    trozos = []
    for c in texto:
        buf += c
        if servidor.frase_terminada(buf):
            trozos.append(buf.strip())
            buf = ""
    if buf.strip():
        trozos.append(buf.strip())

    ok = trozos == esperado
    fallos += not ok
    print(f"\n  {'OK ' if ok else 'MAL'} {texto.strip()[:58]}")
    for t in trozos:
        print(f"      -> {t!r}")
    if not ok:
        print(f"      esperaba: {esperado}")

print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
