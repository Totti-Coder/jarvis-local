"""Tests de la memoria de datos personales.

Salió de un fallo real: "mi novia se llama Verónica" se guardaba como
"se llama Verónica", y al leerlo el prompt decía "El usuario se llama
Verónica". Jarvis acababa contestando "no tengo información sobre tu novia,
pero sé que hay una Verónica".

Uso:  python test_datos.py
"""

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_datos.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

fallos = 0


def comprobar(titulo, obtenido, esperado):
    global fallos
    ok = obtenido == esperado
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {titulo}")
    print(f"      {obtenido!r}")
    if not ok:
        print(f"      esperaba: {esperado!r}")


print("=" * 70)
print("UN DATO SE TIENE QUE ENTENDER SOLO")
print("=" * 70)

# Formato nuevo: la frase ya trae su sujeto y se deja tal cual
comprobar("dato sobre otra persona",
          memoria.como_frase("su novia se llama Verónica"),
          "Su novia se llama Verónica")

comprobar("otro sobre su entorno",
          memoria.como_frase("su hermana vive en Vigo"),
          "Su hermana vive en Vigo")

# Formato viejo: son trozos sin sujeto, y hay que ponérselo
comprobar("trozo antiguo, sin sujeto",
          memoria.como_frase("se llama Toti"),
          "El usuario se llama Toti")

comprobar("otro trozo antiguo",
          memoria.como_frase("vive en Madrid"),
          "El usuario vive en Madrid")

print()
print("=" * 70)
print("GUARDAR Y RECUPERAR")
print("=" * 70)

memoria.recordar_dato("se llama Toti")
memoria.recordar_dato("su novia se llama Verónica")
memoria.recordar_dato("es desarrollador de software")

datos = memoria.datos_conocidos()
frases = [memoria.como_frase(d) for d in datos]
for f in frases:
    print(f"      - {f}")

# Lo que de verdad importa: que no se pueda confundir quién es quién
comprobar("la novia no se confunde con el usuario",
          any("Su novia se llama Verónica" == f for f in frases),
          True)

comprobar("el nombre del usuario sigue siendo suyo",
          any("El usuario se llama Toti" == f for f in frases),
          True)

# Nadie debe leer nunca "El usuario se llama Verónica"
malinterpretado = [f for f in frases
                   if f.startswith("El usuario se llama")
                   and "Verónica" in f]
comprobar("no hay ninguna frase que diga que el usuario es Verónica",
          malinterpretado, [])

print()
print("=" * 70)
print("NO SE REPITEN NI SE GUARDAN VACÍOS")
print("=" * 70)

comprobar("guardar dos veces lo mismo",
          memoria.recordar_dato("se llama Toti"),
          "Eso ya lo tenía apuntado.")

comprobar("guardar algo vacío",
          memoria.recordar_dato("   "),
          "No he entendido qué querías que recordara.")

print()
print("=" * 70)
print("OLVIDAR")
print("=" * 70)

r = memoria.olvidar_dato("la novia")
print(f"      {r}")
comprobar("después de olvidar, ya no está",
          any("Verónica" in d for d in memoria.datos_conocidos()),
          False)

try:
    os.remove(memoria.BASE)
except OSError:
    pass

print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
