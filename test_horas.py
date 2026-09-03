"""¿Entiende "a las 8.40" como la próxima vez que van a ser las 8:40?

Dicho a las 20:15, "a las 8.40" son las 20:40 de esta noche, no las 8:40
de mañana por la mañana. Es lo que haría cualquiera que te escuchara.

Uso:  python test_horas.py
"""

import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
import memoria

fallos = 0


def caso(ahora, texto, esperado, nota=""):
    global fallos
    d, th = memoria.interpretar_cuando(texto, ahora=ahora)
    real = d.strftime("%d/%m %H:%M") if d else "NO"
    ok = real == esperado
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {texto:<32} -> {real:<14}"
          + (f"  {nota}" if ok and nota else "")
          + ("" if ok else f"  (esperaba {esperado})"))


# --------------------------------------------------------------
NOCHE = datetime(2026, 8, 31, 20, 15)     # lunes, las 20:15
print("=" * 78)
print("SON LAS 20:15 DE UN LUNES")
print("=" * 78)

caso(NOCHE, "a las 8.40",   "31/08 20:40", "las 8:40 de la mañana ya pasaron")
caso(NOCHE, "a las 9",      "31/08 21:00", "dentro de 45 minutos")
caso(NOCHE, "a las 11",     "31/08 23:00", "esta noche")
caso(NOCHE, "a las 10.30",  "31/08 22:30", "esta noche")
caso(NOCHE, "hoy a las 9",  "31/08 21:00", "dijo hoy, y aún llega")
caso(NOCHE, "a las 5",      "01/09 17:00", "las dos lecturas pasaron: mañana tarde")

print("\n  con la franja dicha, manda lo que dijo el usuario:")
caso(NOCHE, "a las 8.40 de la mañana", "01/09 08:40", "mañana por la mañana")
caso(NOCHE, "a las 9 de la mañana",    "01/09 09:00")
caso(NOCHE, "a las 23",                "31/08 23:00", "formato 24h, sin duda")

print("\n  con un día futuro, el reloj no pinta nada:")
caso(NOCHE, "mañana a las 8",          "01/09 08:00")
caso(NOCHE, "mañana a las 5",          "01/09 17:00")
caso(NOCHE, "el jueves a las 5",       "03/09 17:00")
caso(NOCHE, "el jueves a las 9",       "03/09 09:00")

# --------------------------------------------------------------
MANANA = datetime(2026, 8, 31, 7, 30)     # lunes, las 7:30
print("\n" + "=" * 78)
print("SON LAS 7:30 DE LA MAÑANA")
print("=" * 78)

caso(MANANA, "a las 8.40",  "31/08 08:40", "hoy, dentro de poco")
caso(MANANA, "a las 9",     "31/08 09:00", "hoy")
caso(MANANA, "a las 5",     "31/08 17:00", "esta tarde")
caso(MANANA, "a las 7",     "31/08 19:00", "las 7 ya pasaron: las de la tarde")
caso(MANANA, "hoy a las 5", "31/08 17:00")

# --------------------------------------------------------------
MADRUGADA = datetime(2026, 8, 31, 23, 50)   # lunes, casi medianoche
print("\n" + "=" * 78)
print("SON LAS 23:50")
print("=" * 78)

caso(MADRUGADA, "a las 8.40", "01/09 08:40", "ya no llega hoy: mañana")
caso(MADRUGADA, "a las 5",    "01/09 17:00", "mañana por la tarde")

print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
