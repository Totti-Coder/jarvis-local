"""Horas por cliente (ver horas.py).

Base de datos temporal y reloj fijo. Stdlib pura: corre en el CI.

Uso:  python test_horas_cliente.py
"""

import csv
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_horas_cliente.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

import horas

horas.preparar()

HOY = datetime(2026, 9, 16, 10, 0)     # miércoles
fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 72)
print("QUÉ PIDE")
print("=" * 72)
for frase, abierta, esperado in [
    ("Empiezo con Acme",                          False, ("empezar", "acme")),
    ("Empiezo a trabajar para el cliente García", False, ("empezar", "garcia")),
    ("Vale, me pongo a trabajar con Acme",        False, ("empezar", "acme")),
    ("Ponme a trabajar con la empresa Iberdrola", False, ("empezar", "iberdrola")),
    ("Empieza a contar horas para Acme",          False, ("empezar", "acme")),
    ("Ficho para Acme",                           False, ("empezar", "acme")),
    ("He terminado",                              True,  ("parar", None)),
    ("Termino por hoy",                           True,  ("parar", None)),
    ("He terminado con Acme",                     True,  ("parar", None)),
    ("Deja de contar horas",                      True,  ("parar", None)),
    ("¿Cuántas horas llevo este mes?",            False, ("consultar", None)),
    ("¿Cuántas horas llevo con Acme?",            False, ("consultar", "acme")),
    ("¿Cuántas horas de trabajo llevo con Acme?", False, ("consultar", "acme")),
    ("¿Cuántas horas he hecho para García en septiembre?", False, ("consultar", "garcia")),
    ("¿Cuánto he trabajado esta semana?",         False, ("consultar", None)),
    ("¿Cuántas horas llevo en septiembre?",       False, ("consultar", None)),
    ("¿Cuánto llevo?",                            True,  ("actual", None)),
    ("Exporta las horas de este mes",             False, ("exportar", None)),
]:
    r = horas.orden(frase, abierta, {"acme": "Acme", "garcia": "García"})
    comprueba(f"{frase!r:<52} -> {r}", r == esperado, f"esperaba {esperado}")

print("\n  lo que NO es fichar:")
for frase, abierta in [
    ("He terminado", False),                    # sin sesión: puede ser una tarea
    ("He terminado el informe", True),          # es una tarea, no la jornada
    ("Ponme a Spotify", False),                 # "ponme a" no es un cliente
    ("Ponme música", False),
    ("¿Cuánto llevo?", False),                  # sin sesión, no hay de qué
    ("¿Qué hora es?", False),
    ("Empieza el cronómetro", False),
    ("Apunta que tengo dentista mañana", False),
]:
    r = horas.orden(frase, abierta, {})
    comprueba(f"{frase!r:<52} abierta={abierta} -> nada", r is None, str(r))

print("\n" + "=" * 72)
print("NOMBRES PARECIDOS: \"AKME\" ES ACME")
print("=" * 72)
CONOCIDOS = {horas.clave_de(n): n for n in
             ["Acme", "García", "Iberdrola", "BBVA", "Mercadona", "Telefónica",
              "Ana López", "Marta", "Carlos", "Luis"]}
for dicho, esperado in [
    # Lo que Whisper hace con un nombre propio: el mismo cliente
    ("Acme", "acme"), ("acmé", "acme"), ("Akme", "acme"),
    ("Garsía", "garcia"), ("Iberdola", "iberdrola"), ("Mercadonna", "mercadona"),
    ("Telefonika", "telefonica"), ("B B V A", "bbva"), ("ana lópez", "ana lopez"),
    ("García López", "garcia"),
    # Personas o cosas DISTINTAS: se pregunta, no se juntan
    ("Marina", None),      # no es Marta
    ("Carla", None),       # no es Carlos
    ("Lucas", None),       # no es Luis
    ("Ana", None),         # no es Ana López
    ("Eva", None),         # nombres cortos: "eba" contra "ba" (BBVA) daba 0,8
    ("Bea", None),
    ("Luiz", "luis"),      # corto, pero suena igual
    ("informe", None),     # "empiezo con el informe"
    ("gimnasio", None),
    ("", None),
]:
    r = horas.cliente_parecido(dicho, CONOCIDOS)
    comprueba(f"{dicho!r:<16} -> {r}", r == esperado, f"esperaba {esperado}")
comprueba("sin clientes, todo es nuevo", horas.cliente_parecido("Acme", {}) is None)

print("\n" + "=" * 72)
print("DÓNDE SE GUARDA EL CSV")
print("=" * 72)
carpeta = horas.carpeta_documentos()
comprueba("cuelga de la carpeta del usuario",
          Path.home() in carpeta.parents or carpeta == Path.home(), carpeta)
# En el CI (Linux) ~/Documents puede no existir: exportar_csv la crea. Lo
# que se comprueba de verdad es que en Windows sea la que el usuario mira
if sys.platform == "win32":
    comprueba("la carpeta Documentos existe", carpeta.is_dir(), carpeta)
    # La de verdad, que con OneDrive NO es ~/Documents. Se compara con lo
    # que dice .NET, que pregunta a Windows por el mismo camino
    import subprocess
    net = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetFolderPath('MyDocuments')"],
        capture_output=True, text=True).stdout.strip()
    comprueba(f"es la que dice Windows ({net})",
              Path(net).resolve() == carpeta.resolve(), carpeta)

print("\n" + "=" * 72)
print("DE QUÉ PERIODO HABLA")
print("=" * 72)
for frase, esperado in [
    ("¿Cuántas horas llevo?",                 ("01/09 00:00", "01/10 00:00", "este mes")),
    ("¿cuánto he trabajado hoy?",             ("16/09 00:00", "17/09 00:00", "hoy")),
    ("horas de ayer",                         ("15/09 00:00", "16/09 00:00", "ayer")),
    ("esta semana",                           ("14/09 00:00", "21/09 00:00", "esta semana")),
    ("la semana pasada",                      ("07/09 00:00", "14/09 00:00", "la semana pasada")),
    ("el mes pasado",                         ("01/08 00:00", "01/09 00:00", "el mes pasado")),
    ("en agosto",                             ("01/08 00:00", "01/09 00:00", "en agosto")),
    # un mes que aún no ha llegado es del año pasado
    ("en diciembre",                          ("01/12 00:00", "01/01 00:00", "en diciembre")),
]:
    d, h, dicho = horas.periodo(frase, HOY)
    r = (f"{d:%d/%m %H:%M}", f"{h:%d/%m %H:%M}", dicho)
    comprueba(f"{frase!r:<36} -> {r}", r == esperado, f"esperaba {esperado}")
d, _, _ = horas.periodo("en diciembre", HOY)
comprueba("diciembre es el de 2025", d.year == 2025, d)

print("\n" + "=" * 72)
print("CÓMO SE DICE")
print("=" * 72)
for seg, esperado in [(20, "menos de un minuto"), (60, "un minuto"),
                      (45 * 60, "45 minutos"), (3600, "una hora"),
                      (90 * 60, "una hora y media"), (150 * 60, "2 horas y media"),
                      (125 * 60, "2 horas y 5 minutos"), (61 * 60, "una hora y un minuto")]:
    r = horas.duracion_hablada(seg)
    comprueba(f"{seg:>6} s -> {r}", r == esperado, esperado)

print("\n" + "=" * 72)
print("UNA JORNADA")
print("=" * 72)
t = HOY
f = horas.empezar("acme", t)
comprueba("empieza y lo nombra", f == "Empiezo a contar para Acme.", f)
comprueba("queda abierta", horas.abierta()["cliente"] == "Acme")

t += timedelta(minutes=10)
f = horas.empezar("Acme", t)
comprueba("empezar otra vez lo mismo no duplica",
          f.startswith("Ya estás con Acme") and len(horas.sesiones(HOY, HOY + timedelta(days=1), t)) == 1, f)

t = HOY + timedelta(hours=2)
f = horas.empezar("García", t)
comprueba("cambiar de cliente cierra el anterior y lo dice",
          f == "Cierro Acme: 2 horas. Empiezo a contar para García.", f)

t += timedelta(minutes=45)
f = horas.parar(t)
comprueba("terminar dice cuánto", f == "Terminado con García: 45 minutos.", f)
comprueba("y no queda nada abierto", horas.abierta() is None)
comprueba("terminar sin nada abierto no rompe",
          horas.parar(t) == "No estabas contando horas para nadie.")

# Otro día, más Acme; y una sesión que cruza la medianoche de fin de mes
horas.empezar("acme", datetime(2026, 9, 17, 9, 0))
horas.parar(datetime(2026, 9, 17, 10, 30))
horas.empezar("acme", datetime(2026, 8, 31, 23, 0))
horas.parar(datetime(2026, 9, 1, 1, 0))

AHORA = datetime(2026, 9, 18, 12, 0)
f = horas.responder("consultar", "acme", "¿cuántas horas llevo con Acme?", AHORA)
# 2 h + 1,5 h + 1 h (la parte de septiembre de la que cruza) = 4,5 h
comprueba("con un cliente, este mes (partiendo la que cruza de mes)",
          f == "Con Acme llevas 4 horas y media este mes.", f)
f = horas.responder("consultar", "acme", "¿y en agosto?", AHORA)
comprueba("y la otra hora cae en agosto", f == "Con Acme llevas una hora en agosto.", f)
f = horas.responder("consultar", None, "¿cuántas horas llevo este mes?", AHORA)
comprueba("todos, de más a menos, con el total",
          f == "Este mes: Acme, 4 horas y media; García, 45 minutos. En total, 5 horas y 15 minutos.", f)
f = horas.responder("consultar", "iberdrola", "¿cuántas horas con Iberdrola?", AHORA)
comprueba("un cliente sin horas", f == "No tienes horas con Iberdrola este mes.", f)
f = horas.responder("consultar", None, "¿cuánto he trabajado hoy?", AHORA)
comprueba("un día sin horas", f == "No has apuntado horas hoy.", f)

horas.empezar("acme", datetime(2026, 9, 18, 11, 0))
f = horas.responder("consultar", "acme", "este mes", AHORA)
comprueba("la sesión abierta cuenta hasta ahora (4,5 h + 1 h)",
          f == "Con Acme llevas 5 horas y media este mes.", f)
f = horas.responder("actual", None, "¿cuánto llevo?", AHORA)
comprueba("¿cuánto llevo? habla de la de ahora", f == "Llevas una hora con Acme.", f)

f = horas.parar(datetime(2026, 9, 19, 11, 0))
comprueba("una sesión olvidada abierta toda la noche se avisa", "Ojo" in f, f)

print("\n" + "=" * 72)
print("EXPORTAR A CSV")
print("=" * 72)
with tempfile.TemporaryDirectory() as tmp:
    ruta = Path(tmp) / "sub" / "horas.csv"
    n = horas.exportar_csv(datetime(2026, 9, 1), datetime(2026, 10, 1), ruta, AHORA)
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        filas = list(csv.reader(fh, delimiter=";"))
    comprueba("una fila por sesión, con cabecera", n == 5 and len(filas) == 6, (n, len(filas)))
    comprueba("formato español: fecha dd/mm/aaaa y coma decimal",
              filas[1] == ["Acme", "01/09/2026", "00:00", "01:00", "1,00"], filas[1])

    # Inyección de fórmulas: un nombre que Excel ejecutaría
    with memoria._conectar() as con:
        con.execute("INSERT INTO sesiones (cliente, clave, inicio, fin) VALUES "
                    "('=HYPERLINK(\"http://malo\")', 'x', '2026-09-20T10:00:00', "
                    "'2026-09-20T11:00:00')")
    horas.exportar_csv(datetime(2026, 9, 20), datetime(2026, 9, 21), ruta, AHORA)
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        celda = list(csv.reader(fh, delimiter=";"))[1][0]
    comprueba("una celda que empieza por = se neutraliza", celda.startswith("'="), celda)

os.remove(memoria.BASE)

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
