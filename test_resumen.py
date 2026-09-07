"""¿Sobrevive una tarea al reinicio y me la cuenta al entrar al día siguiente?

Simula el paso del tiempo moviendo el "ahora", no el reloj del sistema.

Uso:  python test_resumen.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

# Base de datos aparte para no tocar la de verdad
memoria.BASE = Path(__file__).parent / "_test_resumen.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

HOY = datetime(2026, 8, 31, 20, 0)      # lunes por la noche
MANANA = datetime(2026, 9, 1, 8, 30)    # martes por la mañana
PASADO = datetime(2026, 9, 2, 9, 0)     # miércoles

fallos = 0


def comprobar(titulo, obtenido, debe_contener):
    global fallos
    ok = all(t.lower() in obtenido.lower() for t in debe_contener)
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {titulo}")
    print(f"      {obtenido!r}")
    if not ok:
        faltan = [t for t in debe_contener if t.lower() not in obtenido.lower()]
        print(f"      falta: {faltan}")


print("=" * 70)
print("LUNES POR LA NOCHE: apunto cosas")
print("=" * 70)

# Se fija el "ahora" para que "mañana" se calcule desde el lunes
_original = memoria.interpretar_cuando
memoria.interpretar_cuando = lambda t, ahora=None: _original(t, ahora or HOY)

print(" ", memoria.anadir_tarea("comprar pan", "mañana"))
print(" ", memoria.anadir_tarea("dentista", "mañana a las 5"))
print(" ", memoria.anadir_tarea("llamar al banco", "hoy"))
print(" ", memoria.anadir_tarea("mirar lo del coche", ""))

print("\n" + "=" * 70)
print("ENTRO EL MARTES POR LA MAÑANA (proceso nuevo, base de datos en disco)")
print("=" * 70)

# Se relee de disco: es lo que pasaría al reiniciar el ordenador
comprobar("resumen del martes",
          memoria.resumen_del_dia(ahora=MANANA),
          ["buenos días", "comprar pan", "dentista", "a las 5 de la tarde"])

comprobar("lo del lunes aparece como atrasado",
          memoria.resumen_del_dia(ahora=MANANA),
          ["llamar al banco"])

print("\n" + "=" * 70)
print("ENTRO EL MIÉRCOLES SIN HABER HECHO NADA")
print("=" * 70)

comprobar("ya son varias atrasadas",
          memoria.resumen_del_dia(ahora=PASADO),
          ["atrasadas"])

print("\n" + "=" * 70)
print("CON TODO HECHO, NO DEBE DECIR NADA")
print("=" * 70)

with memoria._conectar() as con:
    con.execute("UPDATE tareas SET hecha = 1")
vacio = memoria.resumen_del_dia(ahora=MANANA)
ok = vacio == ""
fallos += not ok
print(f"  {'OK ' if ok else 'MAL'} no saluda si no hay nada -> {vacio!r}")

memoria.interpretar_cuando = _original
try:
    os.remove(memoria.BASE)
except OSError:
    pass

print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
