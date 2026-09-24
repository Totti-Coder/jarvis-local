"""Copia de seguridad diaria de la base de datos.

POR QUÉ

En asistente.db están las tareas, las horas por cliente (lo que se factura)
y lo que Jarvis sabe de ti. Un disco que falla, un borrado mal dado o un
`--borrar` de más se lo llevan todo, y no hay copia en ninguna parte: es
local a propósito, así que tampoco hay una nube que la guarde por ti.

CÓMO

  - Una copia por día, la primera vez que arranca el servidor ese día.
    Arrancarlo diez veces no hace diez copias.
  - Con la API de copia de SQLite, no copiando el fichero: copiar a pelo
    una base de datos abierta puede dejarte una copia a medio escribir.
  - Se guardan siete días y las más viejas se borran solas. Si el fallo se
    nota a la semana, todavía hay de dónde tirar.
  - Si algo falla, se avisa y el asistente sigue. Una copia que impide
    arrancar es peor que no tener copia.

Stdlib pura: corre en el CI.

Uso por consola:
    python respaldo.py          copia ahora y enseña las que hay
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

DIAS = 7


def carpeta_de(base):
    return Path(base).parent / "copias"


def hacer(base, dias=DIAS, ahora=None):
    """Copia la base si hoy no hay copia. Devuelve la ruta, o None."""
    ahora = ahora or datetime.now()
    base = Path(base)
    if not base.exists():
        return None

    carpeta = carpeta_de(base)
    carpeta.mkdir(exist_ok=True)
    destino = carpeta / f"{base.stem}-{ahora:%Y-%m-%d}.db"
    if destino.exists():
        return None                       # ya hay copia de hoy

    origen = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    try:
        copia = sqlite3.connect(destino)
        try:
            # La API de copia de SQLite: consistente aunque alguien esté
            # escribiendo. Copiar el fichero a pelo puede dar una copia rota
            origen.backup(copia)
        finally:
            copia.close()
    finally:
        origen.close()

    limpiar(base, dias, ahora)
    return destino


def limpiar(base, dias=DIAS, ahora=None):
    """Borra las copias de más de `dias` días. Devuelve cuántas quitó."""
    ahora = ahora or datetime.now()
    limite = (ahora - timedelta(days=dias)).date()
    quitadas = 0
    for f in carpeta_de(base).glob(f"{Path(base).stem}-*.db"):
        try:
            fecha = datetime.strptime(f.stem.split("-", 1)[1], "%Y-%m-%d").date()
        except ValueError:
            continue                      # un nombre raro: no se toca
        if fecha <= limite:          # <=, para que queden exactamente `dias`
            try:
                f.unlink(missing_ok=True)
                quitadas += 1
            except OSError as e:
                # En Windows, un antivirus o una copia en la nube pueden
                # tener el fichero abierto. No poder borrar una copia vieja
                # no puede impedir arrancar
                print(f"[respaldo] no se pudo borrar {f.name}: {e}")
    return quitadas


def copias(base):
    """Las que hay ahora mismo, de más nueva a más vieja."""
    return sorted(carpeta_de(base).glob(f"{Path(base).stem}-*.db"), reverse=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import memoria

    nueva = hacer(memoria.BASE)
    print(f"copia de hoy: {nueva}" if nueva else "hoy ya había copia")
    for f in copias(memoria.BASE):
        print(f"  {f.name}  {f.stat().st_size / 1024:.0f} KB")
