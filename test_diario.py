"""El diario de lo que Jarvis no entendió (ver diario.py).

Lo importante es que apunte los fallos DE VERDAD y no las respuestas que
suenan parecido pero son correctas ("no tienes nada apuntado para hoy" es
la respuesta buena a una pregunta cuya respuesta es "nada").

Base de datos temporal. Stdlib pura: corre en el CI.

Uso:  python test_diario.py
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_diario.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

import diario  # noqa: E402

diario.preparar()

HOY = datetime(2026, 10, 20, 12, 0)
fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 74)
print("QUÉ CUENTA COMO FALLO")
print("=" * 74)
for respuesta in [
    "No encuentro esa tarea en tu lista.",
    "No te he entendido bien. Prueba con: empieza el cronómetro.",
    "No conozco ese atajo. Tienes: copia de seguridad.",
    "No veo Spotify abierto ahora mismo.",
    "No encuentro un sitio que se llame Chiquitistán.",
    "No he podido consultar el tiempo en Vigo.",
    "Esa hora no me cuadra: la sesión de Acme empezó a las 4.",
]:
    comprueba(f"{respuesta[:56]!r}", diario.parece_fallo(respuesta))

print("\n  y qué NO (son la respuesta correcta a algo que no tiene nada):")
for respuesta in [
    "No tienes nada apuntado para hoy.",
    "No has apuntado horas este mes.",
    "No tengo nada reciente que deshacer.",
    "No tienes horas con Iberdrola este mes.",
    "Hecho, quito comprar pan de la lista.",
    "En Bilbao hay 21 grados, cielo despejado.",
    "Tienes: dentista a las 5 de la tarde.",
    "",
]:
    comprueba(f"{respuesta[:56]!r}", not diario.parece_fallo(respuesta))

print("\n" + "=" * 74)
print("SE APUNTA LO QUE SE DIJO, Y SE CUENTAN LAS VECES")
print("=" * 74)
diario.apuntar("pon el crono a tope", "No te he entendido bien.", HOY)
diario.apuntar("abre el chiringuito", "No encuentro eso instalado.", HOY)
diario.apuntar("pon el crono a tope", "No te he entendido bien.",
               HOY + timedelta(minutes=5))
diario.apuntar("pon el crono a tope", "No te he entendido bien.",
               HOY + timedelta(minutes=9))

filas = diario.ultimos(dias=7, ahora=HOY + timedelta(minutes=10))
comprueba("se guardan las dos frases distintas", len(filas) == 2, filas)
comprueba("la que más se repite va primero, con su cuenta",
          filas[0]["dijiste"] == "pon el crono a tope" and filas[0]["veces"] == 3,
          filas[0])
comprueba("y se guarda qué contestó, para saber por dónde falló",
          "entendido" in filas[0]["contesto"], filas[0]["contesto"])
comprueba("una frase vacía no se apunta", not diario.apuntar("", "No encuentro eso."))

print("\n" + "=" * 74)
print("CÓMO LO CUENTA EN VOZ ALTA")
print("=" * 74)
f = diario.frase_resumen(7, HOY + timedelta(minutes=10))
comprueba(f"{f}", "pon el crono a tope" in f and "3 veces" in f, f)
comprueba("sin nada, lo dice sin alarmar",
          diario.frase_resumen(7, HOY + timedelta(days=60)).startswith("No me he quedado"))

print("\n  y qué frases preguntan por él:")
for frase, esperado in [
    ("¿Qué no has entendido?", True),
    ("¿Qué frases no has entendido esta semana?", True),
    ("¿Qué se te ha quedado a medias?", True),
    ("Dime qué no has entendido", True),
    ("¿Qué no supiste hacer?", True),
    ("¿Qué tengo mañana?", False),
    ("¿Qué has hecho hoy?", False),
    ("No has entendido lo que te he dicho", False),
]:
    r = diario.preguntan_por_el_diario(frase)
    comprueba(f"{frase!r:<44} -> {r}", r == esperado)

print("\n" + "=" * 74)
print("LO VIEJO SE VA SOLO")
print("=" * 74)
diario.apuntar("algo de hace meses", "No encuentro eso.", HOY - timedelta(days=120))
comprueba("lo de hace cuatro meses no sale en la última semana",
          len(diario.ultimos(dias=7, ahora=HOY)) == 2)
quitadas = diario.limpiar(ahora=HOY)
comprueba(f"y se limpia a los 90 días ({quitadas})", quitadas == 1, quitadas)
comprueba("vaciarlo deja el diario a cero",
          diario.vaciar() == 2 and diario.ultimos(dias=365, ahora=HOY) == [])

os.remove(memoria.BASE)

print("\n" + "=" * 74)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 74)
sys.exit(1 if fallos else 0)
