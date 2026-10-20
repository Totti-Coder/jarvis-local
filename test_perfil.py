"""Lo que Jarvis sabe de ti con forma (ver perfil.py).

Antes, lo que contabas se guardaba como frases sueltas y para sacar tu
ciudad había que buscar "vive en" con una expresión regular. Por eso el
tiempo seguía preguntando de qué ciudad. Aquí se comprueba que los datos
que el asistente USA quedan donde se pueden usar.

Base de datos temporal. Stdlib pura: corre en el CI.

Uso:  python test_perfil.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_perfil.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

import perfil  # noqa: E402

perfil.preparar()

HOY = datetime(2026, 10, 20, 12, 0)      # martes al mediodía
fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 74)
print("QUÉ FRASES RELLENAN EL PERFIL")
print("=" * 74)
for frase, esperado in [
    ("Vivo en Ponferrada", ("ciudad", "Ponferrada")),
    ("Resido en Madrid", ("ciudad", "Madrid")),
    ("Soy de Vigo", ("ciudad", "Vigo")),
    ("Me he mudado a San Sebastián", ("ciudad", "San Sebastian")),
    ("Vivo en A Coruña", ("ciudad", "A Coruna")),
    ("Me llamo Pablo", ("nombre", "Pablo")),
    ("Mi nombre es Ana", ("nombre", "Ana")),
    ("Puedes llamarme Toti", ("nombre", "Toti")),
    # "de 9 a 6" es de 9 a 18: nadie trabaja de nueve a seis de la mañana
    ("Trabajo de 9 a 6", ("horario", (9, 18))),
    ("Trabajo de nueve a seis", ("horario", (9, 18))),
    ("Mi horario es de 8 a 3", ("horario", (8, 15))),
    ("Trabajo de 10 a 19", ("horario", (10, 19))),
]:
    r = perfil.detectar(frase)
    comprueba(f"{frase!r:<34} -> {r}", r == esperado, f"esperaba {esperado}")

print("\n  y cuáles NO (son charla, o no dicen un dato del perfil):")
for frase in [
    "Vivo en casa de mis padres", "Vivo en un piso pequeño", "Vivo en la playa",
    "Soy desarrollador de software", "Trabajo en una consultora",
    "Mi hermana se llama Ana", "Me gusta el café", "Vivo tranquilo",
    "¿Qué sabes de mí?", "",
]:
    r = perfil.detectar(frase)
    comprueba(f"{frase!r:<34} -> {r}", r is None, str(r))

print("\n" + "=" * 74)
print("SE GUARDA Y SE USA")
print("=" * 74)
comprueba("al principio no sabe nada", perfil.ciudad() is None and perfil.horario() is None)

f = perfil.guardar_detectado(*perfil.detectar("Vivo en Ponferrada"), HOY)
comprueba(f"lo dice al guardarlo: {f}", "Ponferrada" in f and "ciudad" in f, f)
comprueba("y queda donde se puede usar", perfil.ciudad() == "Ponferrada")

perfil.guardar_detectado(*perfil.detectar("Me llamo Pablo"), HOY)
perfil.guardar_detectado(*perfil.detectar("Trabajo de 9 a 6"), HOY)
comprueba("el horario queda como dos horas, no como texto",
          perfil.horario() == (9, 18), perfil.horario())

perfil.guardar_detectado(*perfil.detectar("Me he mudado a Vigo"), HOY)
comprueba("mudarse cambia la ciudad, no añade otra", perfil.ciudad() == "Vigo")

print("\n  ¿es horario de trabajo?")
for cuando, esperado in [
    (datetime(2026, 10, 20, 12, 0), True),    # martes al mediodía
    (datetime(2026, 10, 20, 8, 0), False),    # antes de entrar
    (datetime(2026, 10, 20, 18, 0), False),   # justo al salir
    (datetime(2026, 10, 20, 23, 0), False),
    (datetime(2026, 10, 24, 12, 0), False),   # sábado
]:
    r = perfil.en_horario(cuando)
    comprueba(f"{cuando:%a %H:%M} -> {r}", r == esperado, f"esperaba {esperado}")

print("\n" + "=" * 74)
print("CÓMO LO CUENTA, Y QUÉ FRASES LO PIDEN")
print("=" * 74)
f = perfil.frase_resumen()
comprueba(f"{f}", "Pablo" in f and "Vigo" in f and "9 a 18" in f, f)

for frase, esperado in [
    ("¿Qué sabes de mí?", True), ("¿Qué datos tienes?", True),
    ("Dime mi perfil", True), ("¿Qué recuerdas de mí?", True),
    ("¿Qué tengo mañana?", False), ("¿Qué sabes de Madrid?", False),
]:
    comprueba(f"{frase!r:<28} -> {perfil.preguntan_por_el_perfil(frase)}",
              perfil.preguntan_por_el_perfil(frase) == esperado)

print("\n" + "=" * 74)
print("LO QUE YA SE HABÍA CONTADO ANTES NO SE PIERDE")
print("=" * 74)
perfil.olvidar("ciudad")
perfil.olvidar("nombre")
memoria.recordar_dato("vive en Ponferrada")
memoria.recordar_dato("se llama Pablo")
rescatados = perfil.migrar_de_datos()
comprueba(f"se rescatan de las frases sueltas ({rescatados})",
          perfil.ciudad() == "Ponferrada" and perfil.nombre() == "Pablo",
          perfil.todo())
comprueba("y no se duplica al repetir la migración",
          perfil.migrar_de_datos() == 0)

comprueba("un campo que no existe no se guarda",
          not perfil.guardar("color favorito", "azul"))
comprueba("y se puede olvidar lo que se quiera",
          perfil.olvidar("ciudad") and perfil.ciudad() is None)

os.remove(memoria.BASE)

print("\n" + "=" * 74)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 74)
sys.exit(1 if fallos else 0)
