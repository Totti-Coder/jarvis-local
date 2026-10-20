"""Deshacer lo que se borró por error (ver papelera.py).

La confianza no viene de preguntar mucho antes de borrar: viene de poder
equivocarse. Aquí se comprueba que lo borrado vuelve, incluido lo que en
Google se borra de verdad y hay que recrear.

Base de datos temporal y Google de mentira. Stdlib pura: corre en el CI.

Uso:  python test_papelera.py
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_papelera.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

import horas  # noqa: E402
import papelera  # noqa: E402

papelera.preparar()
horas.preparar()
memoria.al_borrar = papelera.apuntar_tareas
horas.al_borrar = papelera.apuntar_sesion

HOY = datetime(2026, 10, 20, 12, 0)
fallos = []
creados = []          # Google de mentira: lo que se le pide recrear


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


def calendario_falso(texto, cuando_iso, tiene_hora):
    creados.append(texto)
    return f"evento-nuevo-{len(creados)}"


def pendientes():
    with memoria._conectar() as con:
        return [f["texto"] for f in con.execute(
            "SELECT texto FROM tareas WHERE hecha = 0 ORDER BY id")]


print("=" * 74)
print("UNA TAREA BORRADA VUELVE")
print("=" * 74)
memoria.anadir_tarea("comprar pan", "mañana a las 10")
memoria.anadir_tarea("dentista", "mañana a las 5")
memoria.completar_tarea("comprar pan")
comprueba("se quita de la lista", pendientes() == ["dentista"], pendientes())
comprueba("y queda apuntada en la papelera",
          papelera.ultima(HOY)["resumen"] == "comprar pan", papelera.ultima(HOY))

frase = papelera.deshacer(HOY)
comprueba(f"deshacer la devuelve: {frase}", frase == "Recuperado: comprar pan.", frase)
comprueba("y está otra vez en la lista",
          pendientes() == ["comprar pan", "dentista"], pendientes())
comprueba("deshacer dos veces no revuelve lo mismo",
          papelera.deshacer(HOY) == "No tengo nada reciente que deshacer.")

print("\n" + "=" * 74)
print("VARIAS A LA VEZ, Y LO QUE ESTABA EN GOOGLE")
print("=" * 74)
# Una de ellas tenía evento en Google: en Google el borrado es de verdad
with memoria._conectar() as con:
    con.execute("UPDATE tareas SET evento_id = 'evento-viejo' WHERE texto = 'dentista'")
with memoria._conectar() as con:
    todas = [dict(f) for f in con.execute("SELECT * FROM tareas WHERE hecha = 0")]
memoria.completar_varias(todas)
comprueba("se quitan las dos", pendientes() == [], pendientes())

papelera.crear_externo = calendario_falso
frase = papelera.deshacer(HOY)
comprueba(f"vuelven las dos: {frase}", "Recuperadas 2 cosas" in frase, frase)
comprueba("y el evento de Google se vuelve a crear, no se da por perdido",
          creados == ["dentista"], creados)
with memoria._conectar() as con:
    nuevo = con.execute("SELECT evento_id FROM tareas WHERE texto = 'dentista'").fetchone()
comprueba("con su id NUEVO apuntado (el viejo ya no existe en Google)",
          nuevo["evento_id"] == "evento-nuevo-1", dict(nuevo))

print("\n" + "=" * 74)
print("UN EVENTO QUE SOLO ESTABA EN GOOGLE")
print("=" * 74)
creados.clear()
papelera.apuntar_eventos([{"texto": "comida con Ana",
                           "cuando_iso": "2026-10-21T14:00:00", "tiene_hora": True}], HOY)
frase = papelera.deshacer(HOY)
comprueba(f"se devuelve al calendario: {frase}",
          "comida con Ana" in frase and creados == ["comida con Ana"], (frase, creados))

papelera.crear_externo = None
papelera.apuntar_eventos([{"texto": "algo", "cuando_iso": None, "tiene_hora": False}], HOY)
frase = papelera.deshacer(HOY)
comprueba(f"sin Google conectado se dice, no se miente: {frase}",
          "no está conectado" in frase, frase)
papelera.crear_externo = calendario_falso

print("\n" + "=" * 74)
print("UNA SESIÓN DE HORAS BORRADA")
print("=" * 74)
horas.empezar("Acme", datetime(2026, 10, 20, 9, 0))
horas.parar(datetime(2026, 10, 20, 11, 0))
sesion = horas.ultima()
horas.borrar_sesion(sesion["id"])
comprueba("se borra", horas.ultima() is None)
frase = papelera.deshacer(HOY)
comprueba(f"y vuelve con sus horas: {frase}", "Acme" in frase, frase)
comprueba("con el mismo rato contado",
          horas.totales(datetime(2026, 10, 20), datetime(2026, 10, 21),
                        HOY)["acme"][1] == 2 * 3600)

print("\n" + "=" * 74)
print("LO ÚLTIMO ES LO ÚLTIMO, Y LO VIEJO SE VA")
print("=" * 74)
papelera.apuntar("tareas", "lo primero", {"tareas": []}, HOY)
papelera.apuntar("tareas", "lo segundo", {"tareas": []}, HOY)
comprueba("se deshace lo más reciente primero",
          papelera.ultima(HOY)["resumen"] == "lo segundo")
papelera.deshacer(HOY)
comprueba("y luego lo anterior", papelera.ultima(HOY)["resumen"] == "lo primero")

papelera.apuntar("tareas", "de hace dos meses", {"tareas": []},
                 HOY - timedelta(days=60))
comprueba("lo de hace dos meses ya no se ofrece",
          papelera.ultima(HOY)["resumen"] == "lo primero")
quitadas = papelera.limpiar(HOY)
comprueba(f"y se limpia solo ({quitadas} entradas)", quitadas == 1, quitadas)

os.remove(memoria.BASE)

print("\n" + "=" * 74)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 74)
sys.exit(1 if fallos else 0)
