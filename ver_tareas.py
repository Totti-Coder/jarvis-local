"""Mira qué hay guardado en la base de datos del asistente.

Uso:
    python ver_tareas.py            solo las pendientes
    python ver_tareas.py --todas    también las hechas
    python ver_tareas.py --borrar   vacía la lista (pregunta antes)
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime

import memoria

memoria.preparar()
todas = "--todas" in sys.argv
borrar = "--borrar" in sys.argv

print(f"base de datos: {memoria.BASE}")

if borrar:
    with memoria._conectar() as con:
        n = con.execute("SELECT COUNT(*) c FROM tareas").fetchone()["c"]
    if n == 0:
        print("Ya está vacía.")
        sys.exit(0)
    print(f"\nEsto borra {n} tareas y NO se puede deshacer.")
    if input("Escribe BORRAR para confirmar: ").strip() != "BORRAR":
        print("Cancelado, no se ha tocado nada.")
        sys.exit(0)
    with memoria._conectar() as con:
        con.execute("DELETE FROM tareas")
    print(f"Borradas {n} tareas.")
    sys.exit(0)

consulta = "SELECT * FROM tareas"
if not todas:
    consulta += " WHERE hecha = 0"
consulta += " ORDER BY cuando_iso IS NULL, cuando_iso"

with memoria._conectar() as con:
    filas = con.execute(consulta).fetchall()

if not filas:
    print("\nNo hay nada guardado.")
    sys.exit(0)

ahora = datetime.now()
print(f"\n{'id':>3}  {'':<6} {'qué':<36} {'cuándo':<26} dijiste")
print("-" * 96)
for f in filas:
    estado = "hecha" if f["hecha"] else ""
    cuando = memoria.en_palabras(f["cuando_iso"], f["tiene_hora"], ahora) or "sin fecha"
    # marca lo que ya se ha pasado y sigue pendiente
    if not f["hecha"] and f["cuando_iso"] and datetime.fromisoformat(f["cuando_iso"]) < ahora:
        cuando += "  (pasado)"
    print(f"{f['id']:>3}  {estado:<6} {f['texto'][:36]:<36} {cuando:<26} {f['cuando_texto'] or ''}")

print(f"\n{len(filas)} tareas" + ("" if todas else " pendientes (usa --todas para ver las hechas)"))
