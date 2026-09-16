"""Borrar por fecha: "quita lo del 1 de septiembre".

Tres cosas que salieron mal antes de escribir esto:

  - La fecha se ignoraba: "lo del 1 de septiembre" se buscaba como si
    fuera el NOMBRE de una tarea, y no encontraba nada.
  - "Borra todo lo que tenia el 31 de agosto" contiene "todo", y se
    entendia como "borra todo": proponia vaciar la agenda entera.
  - Las fechas pasadas se mandaban al año siguiente. Para apuntar es lo
    correcto; para borrar, "lo del 1 de septiembre" dicho el 16 es de hace
    quince dias, no de 2027.

Base de datos temporal y reloj fijo. Stdlib pura: corre en el CI.

Uso:  python test_borrar_fecha.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_borrar_fecha.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

HOY = datetime(2026, 9, 16, 17, 0)        # miercoles 16 de septiembre
fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 72)
print("QUE DIA Y QUE COSA PIDE QUITAR")
print("=" * 72)
CASOS = [
    # (frase, ventana esperada, palabras que quedan)
    ("Quita lo del 1 de septiembre",                 "01/09/2026 00:00-23:59", set()),
    ("Borra todo lo que tenía el 31 de agosto",      "31/08/2026 00:00-23:59", set()),
    ("Elimina las tareas del 1 de septiembre",       "01/09/2026 00:00-23:59", set()),
    # con nombre: solo eso, ese dia
    ("Quita el dentista del 1 de septiembre",        "01/09/2026 00:00-23:59", {"dentista"}),
    ("Borra lo de comprar pan del 1 de septiembre",  "01/09/2026 00:00-23:59", {"comprar", "pan"}),
    ("Cancela la reunión de mañana",                 "17/09/2026 00:00-23:59", {"reunion"}),
    # la ocurrencia MAS CERCANA, hacia delante o hacia atras
    ("Quita lo del 20 de septiembre",                "20/09/2026 00:00-23:59", set()),
    ("Quita lo del 15 de septiembre",                "15/09/2026 00:00-23:59", set()),
    ("Quita lo del 10 de enero",                     "10/01/2027 00:00-23:59", set()),
    ("Quita lo del 1 de agosto",                     "01/08/2026 00:00-23:59", set()),
    # dias con nombre propio
    ("Borra todo lo de mañana",                      "17/09/2026 00:00-23:59", set()),
    ("Quita lo de ayer",                             "15/09/2026 00:00-23:59", set()),
    ("Quita lo de anteayer",                         "14/09/2026 00:00-23:59", set()),
    ("Quita lo de pasado mañana",                    "18/09/2026 00:00-23:59", set()),
    ("Quita lo de hoy",                              "16/09/2026 00:00-23:59", set()),
    # con franja: sin estrechar, se llevaria tambien la otra mitad del dia
    ("Quita lo de mañana por la mañana",             "17/09/2026 06:00-12:59", set()),
    ("Borra lo de esta tarde",                       "16/09/2026 13:00-20:59", set()),
    ("Quita lo del 1 de septiembre por la tarde",    "01/09/2026 13:00-20:59", set()),
]
for frase, esperada, palabras in CASOS:
    r = memoria.borrado_por_fecha(frase, HOY)
    obtenida = f"{r[0]:%d/%m/%Y %H:%M}-{r[1]:%H:%M}" if r else None
    ok = obtenida == esperada and r[2] == palabras
    comprueba(f"{frase!r:<46} {obtenida} {sorted(r[2]) if r and r[2] else ''}", ok,
              f"esperaba {esperada} {sorted(palabras)}")

print("\n  sin fecha, NO es un borrado por dia:")
for frase in ["Quita el dentista", "Borra las atrasadas", "Quita lo del pan",
              "Borra todas mis tareas", "Cancela la reunión"]:
    r = memoria.borrado_por_fecha(frase, HOY)
    comprueba(f"{frase!r:<46} -> nada", r is None, str(r))

comprueba("un dia que no existe no revienta",
          memoria.borrado_por_fecha("quita lo del 31 de febrero", HOY) is None)

print("\n" + "=" * 72)
print("LAS TAREAS PROPIAS DE ESE DIA")
print("=" * 72)


def tarea(texto, cuando, hecha=0):
    with memoria._conectar() as con:
        return con.execute(
            "INSERT INTO tareas (texto, cuando_iso, tiene_hora, hecha, creada) "
            "VALUES (?, ?, 1, ?, ?)", (texto, cuando, hecha, HOY.isoformat())).lastrowid


tarea("dentista", "2026-09-01T09:00:00")
tarea("comprar pan", "2026-09-01T18:00:00")
tarea("dentista", "2026-09-02T09:00:00")          # otro dia
tarea("gimnasio", "2026-09-01T20:00:00", hecha=1) # ya hecha

d, h, _ = memoria.borrado_por_fecha("lo del 1 de septiembre", HOY)
nombres = sorted(f["texto"] for f in memoria.tareas_del_dia(d, h))
comprueba("solo las pendientes de ese dia", nombres == ["comprar pan", "dentista"], nombres)
nombres = [f["texto"] for f in memoria.tareas_del_dia(d, h, {"dentista"})]
comprueba("y con nombre, solo esa", nombres == ["dentista"], nombres)
d, h, _ = memoria.borrado_por_fecha("lo del 1 de septiembre por la tarde", HOY)
nombres = [f["texto"] for f in memoria.tareas_del_dia(d, h)]
comprueba("con franja, solo las de la tarde", nombres == ["comprar pan"], nombres)

ids = [f["id"] for f in memoria.tareas_del_dia(*memoria.borrado_por_fecha(
    "lo del 1 de septiembre", HOY)[:2])]
comprueba("se recuperan por id", len(memoria.tareas_por_ids(ids)) == 2)
comprueba("sin ids, lista vacia sin tocar la base", memoria.tareas_por_ids([]) == [])

print("\n" + "=" * 72)
print("COINCIDIR POR NOMBRE")
print("=" * 72)
for titulo, palabras, esperado in [
    ("dentista", set(), True),                     # sin palabras: todo vale
    ("Dentista", {"dentista"}, True),
    ("Cita en el dentista", {"dentista"}, True),
    ("comprar pan", {"dentista"}, False),
    ("¡Feliz cumpleaños!", {"reunion"}, False),
    ("Reunión de equipo", {"reunion"}, True),      # sin tildes
]:
    r = memoria.coincide_nombre(titulo, palabras)
    comprueba(f"{titulo!r:<24} {sorted(palabras)!s:<14} -> {r}", r == esperado)

os.remove(memoria.BASE)

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
