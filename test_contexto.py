"""Seguir el hilo: "bórralo", "cámbialo a las 6" (ver contexto.py).

Hablando, nadie repite el nombre entero: acabas de apuntar el dentista y
dices "cámbialo". Aquí se comprueba que se entiende contra lo último de lo
que se habló, y —más importante— que NO se entiende cuando no hay nada
reciente: borrar la tarea equivocada es peor que no entender.

Base de datos temporal y reloj a mano. Stdlib pura: corre en el CI.

Uso:  python test_contexto.py
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_contexto.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

import contexto  # noqa: E402

HOY = datetime(2026, 10, 20, 12, 0)
fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 74)
print("QUÉ FRASES HABLAN DE \"ESO\"")
print("=" * 74)
for frase, esperado in [
    ("Bórralo", ("quitar", None)),
    ("Quítalo", ("quitar", None)),
    ("Ya está hecho", ("quitar", None)),
    ("Ya lo he hecho", ("quitar", None)),
    ("Márcalo como hecho", ("quitar", None)),
    ("Cámbialo a las 6", ("cambiar", "a las 6")),
    ("Muévelo al jueves", ("cambiar", "al jueves")),
    ("Cámbialo para mañana por la tarde", ("cambiar", "para manana por la tarde")),
    ("Déjalo para el lunes", ("cambiar", "para el lunes")),
    ("¿A qué hora era?", ("cuando", None)),
    ("¿Cuándo era?", ("cuando", None)),
    ("¿Qué día era?", ("cuando", None)),
]:
    r = contexto.referencia(frase)
    comprueba(f"{frase!r:<38} -> {r}", r == esperado, f"esperaba {esperado}")

print("\n  y cuáles NO (nombran la cosa, o hablan de otra):")
for frase in [
    "Bórrame el dentista", "Quita lo del 1 de septiembre", "Cámbialo",
    "Está hecho el informe", "¿Qué tengo mañana?", "Borra la última sesión", "",
]:
    r = contexto.referencia(frase)
    comprueba(f"{frase!r:<38} -> {r}", r is None, str(r))

print("\n  lo que se captura tiene que ser una fecha de verdad:")
for frase, vale in [
    ("Cámbialo a las 6", True), ("Muévelo al jueves", True),
    ("Déjalo para el lunes", True), ("Cámbialo para mañana por la tarde", True),
    # esta es del cronómetro, no de la agenda: no debe cambiar ninguna hora
    ("Ponlo en marcha", False),
]:
    r = contexto.referencia(frase)
    fecha = memoria.interpretar_cuando(r[1], HOY)[0] if r and r[1] else None
    comprueba(f"{frase!r:<38} -> {fecha}", bool(fecha) == vale, str(fecha))

print("\n" + "=" * 74)
print("DE QUÉ SE ESTABA HABLANDO")
print("=" * 74)
hilo = contexto.Hilo()
comprueba("recién abierto, no hay hilo", hilo.tarea_reciente(HOY) is None)

hilo.apuntar_tarea({"id": 1, "texto": "dentista", "cuando_iso": "2026-10-21T17:00:00",
                    "tiene_hora": 1}, HOY)
comprueba("tras hablar de algo, ahí está",
          hilo.tarea_reciente(HOY)["texto"] == "dentista")
comprueba("un minuto después, sigue",
          hilo.tarea_reciente(HOY + timedelta(minutes=1)) is not None)
comprueba("media hora después ya no: 'bórralo' hablaría de otra cosa",
          hilo.tarea_reciente(HOY + timedelta(minutes=30)) is None)

hilo.apuntar_tarea({"id": 2, "texto": "llamar al banco"}, HOY)
comprueba("hablar de otra cosa cambia el hilo",
          hilo.tarea_reciente(HOY)["texto"] == "llamar al banco")
hilo.olvidar()
comprueba("y al borrarla, el hilo se corta", hilo.tarea_reciente(HOY) is None)

print("\n" + "=" * 74)
print("MOVER UNA TAREA DE HORA")
print("=" * 74)
memoria.anadir_tarea("dentista", "mañana a las 5")
t = memoria.ultima_apuntada()
comprueba("la última apuntada se puede recuperar",
          t and t["texto"] == "dentista", t)

f = memoria.cambiar_cuando(t["id"], "a las 6", HOY)
comprueba(f"se mueve y se dice cómo queda: {f}", "6 de la tarde" in f, f)
comprueba("y la lista ya la tiene a esa hora",
          "6 de la tarde" in memoria.listar_tareas(ahora=HOY))

f = memoria.cambiar_cuando(t["id"], "el jueves a las 9", HOY)
comprueba(f"también de día: {f}", "9 de la mañana" in f, f)
comprueba("una hora que no se entiende no toca nada",
          memoria.cambiar_cuando(t["id"], "en marcha", HOY) is None)

print("\n  decir solo la hora no puede cambiar el día:")
memoria.anadir_tarea("llamar al banco", "el jueves a las nueve")
banco = memoria.ultima_apuntada()
comprueba("se apunta con la hora dicha en letra",
          banco["cuando_iso"].startswith("2026-10-22T09:00"), banco["cuando_iso"])


def mover(cambio):
    memoria.cambiar_cuando(banco["id"], cambio, HOY)
    with memoria._conectar() as con:
        return con.execute("SELECT cuando_iso FROM tareas WHERE id = ?",
                           (banco["id"],)).fetchone()[0]


d = mover("a las diez")
comprueba(f"'a las diez' deja el jueves y pone las 10: {d}",
          d.startswith("2026-10-22T10:00"), d)
# "Las diez" son las 10 o las 22: moviendo algo que ya tenía hora, se
# queda la de al lado, que es lo que quiere decir quien lo mueve
d = mover("a las once")
comprueba(f"y no se va a las once de la noche: {d}",
          d.startswith("2026-10-22T11:00"), d)
d = mover("el viernes a las 5")
comprueba(f"si SÍ se dice el día, se mueve de día: {d}",
          d.startswith("2026-10-23T17:00"), d)
comprueba("y una tarea que no existe tampoco",
          memoria.cambiar_cuando(9999, "a las 6", HOY) is None)

os.remove(memoria.BASE)

print("\n" + "=" * 74)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 74)
sys.exit(1 if fallos else 0)
