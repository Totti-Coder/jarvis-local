"""La copia diaria de la base de datos (ver respaldo.py).

Base de datos de mentira en una carpeta temporal y fechas a mano: no toca
la tuya ni espera siete días de verdad. Stdlib pura: corre en el CI.

Uso:  python test_respaldo.py
"""

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import respaldo

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


def base_de_prueba(carpeta):
    ruta = Path(carpeta) / "asistente.db"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE tareas (id INTEGER PRIMARY KEY, texto TEXT)")
    con.executemany("INSERT INTO tareas (texto) VALUES (?)",
                    [("comprar pan",), ("dentista",)])
    con.commit()
    con.close()
    return ruta


HOY = datetime(2026, 9, 25, 10, 0)

with tempfile.TemporaryDirectory() as tmp:
    base = base_de_prueba(tmp)

    print("=" * 72)
    print("UNA COPIA AL DÍA")
    print("=" * 72)
    primera = respaldo.hacer(base, ahora=HOY)
    comprueba(f"se copia al arrancar: {primera.name}",
              primera and primera.exists())
    con = sqlite3.connect(primera)
    cuantas = con.execute("SELECT COUNT(*) FROM tareas").fetchone()[0]
    con.close()          # en Windows, dejarla abierta bloquea el fichero
    comprueba("y la copia tiene los datos, no está vacía", cuantas == 2)
    comprueba("arrancar otra vez el mismo día NO hace otra copia",
              respaldo.hacer(base, ahora=HOY.replace(hour=23)) is None)
    comprueba("mañana sí",
              respaldo.hacer(base, ahora=HOY + timedelta(days=1)) is not None)

    print("\n" + "=" * 72)
    print("SE GUARDAN SIETE DÍAS")
    print("=" * 72)
    for d in range(2, 20):
        respaldo.hacer(base, ahora=HOY + timedelta(days=d))
    nombres = [f.name for f in respaldo.copias(base)]
    comprueba(f"quedan 7, no 20: {len(nombres)}", len(nombres) == 7, nombres)
    comprueba("y son las siete últimas",
              nombres[0].endswith("2026-10-14.db") and nombres[-1].endswith("2026-10-08.db"),
              nombres)

    print("\n" + "=" * 72)
    print("LO QUE NO DEBE PASAR")
    print("=" * 72)
    ajeno = respaldo.carpeta_de(base) / "asistente-copia-mia.db"
    ajeno.write_text("no me toques", encoding="utf-8")
    respaldo.hacer(base, ahora=HOY + timedelta(days=30))
    comprueba("un fichero con otro nombre no se borra", ajeno.exists())

    comprueba("si no hay base de datos, no revienta ni inventa copias",
              respaldo.hacer(Path(tmp) / "no-existe.db", ahora=HOY) is None)

    # La base sigue intacta después de todas las copias
    con = sqlite3.connect(base)
    comprueba("la base original queda intacta",
              con.execute("SELECT COUNT(*) FROM tareas").fetchone()[0] == 2)
    con.close()

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
