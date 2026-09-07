"""Comprueba que las credenciales de Google estan bien, sin mostrarlas.

Uso:  python comprobar_google.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")
import calendario

env = calendario.leer_env()
cid = env.get("GOOGLE_CLIENT_ID", "")
sec = env.get("GOOGLE_CLIENT_SECRET", "")

print(f"Fichero .env: {'existe' if calendario.ENV.exists() else 'NO existe'}")
print(f"GOOGLE_CLIENT_ID: {'presente (' + str(len(cid)) + ' caracteres)' if cid else 'AUSENTE'}")
print(f"GOOGLE_CLIENT_SECRET: {'presente (' + str(len(sec)) + ' caracteres)' if sec else 'AUSENTE'}")
print()

problemas = []
if not calendario.ENV.exists() and not calendario.CREDENCIALES.exists():
    problemas.append("no hay .env. Copia .env.example como .env y rellenalo")
if not cid:
    problemas.append("falta GOOGLE_CLIENT_ID")
elif "tu-id" in cid:
    problemas.append("GOOGLE_CLIENT_ID sigue con el valor de ejemplo")
elif not cid.endswith(".apps.googleusercontent.com"):
    problemas.append("GOOGLE_CLIENT_ID no acaba en .apps.googleusercontent.com; "
                     "revisa que lo copiaste entero")
if not sec:
    problemas.append("falta GOOGLE_CLIENT_SECRET")
elif "tu-secreto" in sec:
    problemas.append("GOOGLE_CLIENT_SECRET sigue con el valor de ejemplo")
elif len(sec) < 20:
    problemas.append("GOOGLE_CLIENT_SECRET parece demasiado corto")

if problemas:
    print("PROBLEMAS:")
    for p in problemas:
        print(f"  - {p}")
    sys.exit(1)

print("Las credenciales tienen buena pinta.")
print(f"Token de acceso: {'ya concedido' if calendario.TOKEN.exists() else 'falta'}")
if not calendario.TOKEN.exists():
    print("\nSiguiente paso:  python calendario.py")
else:
    print(f"\nEstado: {calendario.estado()}")
