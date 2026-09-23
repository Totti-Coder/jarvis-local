"""Mira qué hay guardado en la base de datos del asistente.

Uso:
    python ver_tareas.py            solo las pendientes
    python ver_tareas.py --todas    también las hechas
    python ver_tareas.py --borrar   vacía la lista (pregunta antes)
    python ver_tareas.py --quitar 26 27 28    solo esas, por su id
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime

import memoria

memoria.preparar()
todas = "--todas" in sys.argv
borrar = "--borrar" in sys.argv

print(f"base de datos: {memoria.BASE}")

if "--quitar" in sys.argv:
    # Quitar UNAS tareas concretas, no vaciar la lista entera. Hizo falta
    # para las que dejó una orden mal entendida ("cronómetro", "empiezo al
    # cronómetro"): borrarlo todo se llevaría por delante lo que sí vale.
    ids = [int(a) for a in sys.argv[sys.argv.index("--quitar") + 1:] if a.isdigit()]
    if not ids:
        print("Dime qué ids: python ver_tareas.py --quitar 26 27 28")
        sys.exit(1)
    marcas = ",".join("?" * len(ids))
    with memoria._conectar() as con:
        filas = con.execute(f"SELECT id, texto, evento_id FROM tareas "
                            f"WHERE id IN ({marcas})", ids).fetchall()
    if not filas:
        print("Ninguna de esas existe ya.")
        sys.exit(0)
    print("\nSe van a borrar:")
    for f in filas:
        print(f"  {f['id']:>3}  {f['texto']}")
    print("\nNO se puede deshacer.")
    if input("Escribe SI para confirmar: ").strip().upper() not in ("SI", "SÍ"):
        print("Cancelado, no se ha tocado nada.")
        sys.exit(0)

    # Igual que arriba: primero Google Calendar, que necesita el evento_id
    eventos = [f["evento_id"] for f in filas if f["evento_id"]]
    if eventos:
        import calendario
        quitados = sum(bool(calendario.borrar_evento(e)) for e in eventos)
        print(f"  {quitados} de {len(eventos)} borrados también del calendario")
    with memoria._conectar() as con:
        n = con.execute(f"DELETE FROM tareas WHERE id IN ({marcas})", ids).rowcount
    print(f"Borradas {n} tareas.")
    sys.exit(0)

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

    # Primero el calendario, y LUEGO la base de datos. El orden importa:
    # el evento_id solo existe en SQLite, asi que borrando la fila primero
    # se pierde la unica forma de encontrar el evento. Paso de verdad, y
    # dejo 45 eventos huerfanos en el calendario que Jarvis ya no podia
    # quitar porque, para el, esas tareas no existian.
    with memoria._conectar() as con:
        eventos = [f["evento_id"] for f in
                   con.execute("SELECT evento_id FROM tareas "
                               "WHERE evento_id IS NOT NULL").fetchall()]
    if eventos:
        import calendario
        print(f"Quitando {len(eventos)} eventos del calendario...")
        quitados = sum(bool(calendario.borrar_evento(e)) for e in eventos)
        print(f"  {quitados} de {len(eventos)} borrados del calendario")
        if quitados < len(eventos):
            print("  (los que fallaron ya no estaban, o no hay permiso)")

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
