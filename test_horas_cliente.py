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
print("SESIÓN OLVIDADA: \"TERMINÉ A LAS 7\"")
print("=" * 72)
for frase, abierta, esperado in [
    ("Terminé a las 7",                                True,  ("parar_a", None)),
    ("He terminado con Acme a las 6 de la tarde",      True,  ("parar_a", None)),
    ("Pues terminé ayer a las 8",                      True,  ("parar_a", None)),
    ("He terminado",                                   True,  ("parar", None)),
    ("Terminé",                                        True,  ("parar", None)),
    ("Terminé a las 7",                                False, None),   # nada abierto
]:
    r = horas.orden(frase, abierta, {"acme": "Acme"})
    comprueba(f"{frase!r:<46} abierta={abierta} -> {r}", r == esperado, f"esperaba {esperado}")

INICIO = datetime(2026, 9, 16, 16, 0)
for frase, ahora, esperado in [
    # el mismo día: "a las 7" son las 19:00, las 7:00 fueron antes de empezar
    ("terminé a las 7",              datetime(2026, 9, 16, 21, 0), "16/09 19:00"),
    ("terminé a las 18:30",          datetime(2026, 9, 16, 21, 0), "16/09 18:30"),
    # a la mañana siguiente: la PRIMERA tras el inicio, no las 7 de hoy
    ("terminé a las 7",              datetime(2026, 9, 17, 9, 30), "16/09 19:00"),
    ("terminé ayer a las 8 de la tarde", datetime(2026, 9, 17, 9, 30), "16/09 20:00"),
    ("terminé hoy a las 7",          datetime(2026, 9, 17, 9, 30), "17/09 07:00"),
    # antes de empezar o todavía por llegar: no cuadra
    ("terminé a las 3",              datetime(2026, 9, 16, 21, 0), None),
    ("terminé a las 22:00",          datetime(2026, 9, 16, 21, 0), None),
    ("terminé",                      datetime(2026, 9, 16, 21, 0), None),
]:
    r = horas.fin_dicho(frase, INICIO, ahora)
    obtenido = f"{r:%d/%m %H:%M}" if r else None
    comprueba(f"{frase!r:<36} a las {ahora:%d/%m %H:%M} -> {obtenido}",
              obtenido == esperado, f"esperaba {esperado}")

with memoria._conectar() as con:
    con.execute("DELETE FROM sesiones")
horas.empezar("acme", INICIO)
f = horas.responder("parar_a", None, "terminé a las 7", datetime(2026, 9, 17, 9, 30))
comprueba("cierra a esa hora y la repite, con el día",
          f == "Terminado con Acme ayer a las 7 de la tarde: 3 horas.", f)
comprueba("y la sesión queda de 3 horas, no de 17",
          horas.totales(INICIO, INICIO + timedelta(days=2))["acme"][1] == 3 * 3600)

horas.empezar("acme", INICIO)
f = horas.responder("parar_a", None, "terminé a las 3", datetime(2026, 9, 16, 21, 0))
comprueba("una hora que no cuadra no cierra nada, y dice cuándo empezó",
          "no me cuadra" in f and "a las 4 de la tarde" in f and horas.abierta(), f)

print("\n  el recordatorio y el aviso al entrar:")
id_ = horas.abierta()["id"]
comprueba("a las 2 h, nada",
          horas.recordatorio(INICIO + timedelta(hours=2)) is None)
r = horas.recordatorio(INICIO + timedelta(hours=3, minutes=5))
comprueba("a las 3 h, se recuerda", r and r[0] == f"h:{id_}:1" and "terminé a las" in r[1], r)
r = horas.recordatorio(INICIO + timedelta(hours=6, minutes=10))
comprueba("a las 6 h, otro tramo (otra clave: se vuelve a decir)",
          r and r[0] == f"h:{id_}:2", r)
comprueba("al entrar el mismo día, nada",
          horas.aviso_al_entrar(datetime(2026, 9, 16, 22, 0)) is None)
f = horas.aviso_al_entrar(datetime(2026, 9, 17, 9, 0))
comprueba("al entrar al día siguiente, se avisa con la hora de inicio",
          f and "desde ayer a las 4 de la tarde" in f, f)
horas.parar(datetime(2026, 9, 16, 20, 0))
comprueba("sin sesión, ni recordatorio ni aviso",
          horas.recordatorio(datetime(2026, 9, 17, 9, 0)) is None
          and horas.aviso_al_entrar(datetime(2026, 9, 17, 9, 0)) is None)
with memoria._conectar() as con:
    con.execute("DELETE FROM sesiones")

# La jornada de antes vuelve a crearse para el CSV
horas.empezar("acme", datetime(2026, 9, 16, 10, 0))
horas.empezar("García", datetime(2026, 9, 16, 12, 0))
horas.parar(datetime(2026, 9, 16, 12, 45))
horas.empezar("acme", datetime(2026, 9, 17, 9, 0))
horas.parar(datetime(2026, 9, 17, 10, 30))
horas.empezar("acme", datetime(2026, 8, 31, 23, 0))
horas.parar(datetime(2026, 9, 1, 1, 0))
horas.empezar("acme", datetime(2026, 9, 18, 11, 0))

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

print("\n" + "=" * 72)
print("CORREGIR: APUNTAR, RESTAR, BORRAR")
print("=" * 72)
for dicho, esperado in [
    ("2 horas", 7200), ("dos horas y media", 9000), ("una hora y media", 5400),
    ("hora y media", 5400), ("media hora", 1800), ("tres cuartos de hora", 2700),
    ("cuarenta y cinco minutos", 2700), ("1 hora y 20 minutos", 4800),
    ("un cuarto de hora", 900), ("3,5 horas", 12600), ("sin duración", None),
]:
    r = horas.duracion_en(horas._normal(dicho))
    comprueba(f"{dicho!r:<28} -> {r}", r == esperado, f"esperaba {esperado}")

MIERCOLES = datetime(2026, 9, 16, 17, 0)
for dicho, esperado in [
    ("hoy", "16/09"), ("", "16/09"), ("ayer", "15/09"), ("anteayer", "14/09"),
    ("el lunes", "14/09"), ("el miercoles", "09/09"),     # dicho un miércoles: el pasado
    ("del lunes", "14/09"), ("el 3", "03/09"),
    ("el 20", "20/08"),                                  # sin mes: el del mes pasado
    ("el 20 de agosto", "20/08"), ("el 31", "31/08"),
]:
    r = horas.dia_pasado_en(horas._normal(dicho), MIERCOLES)
    comprueba(f"{dicho!r:<20} -> {r:%d/%m}", f"{r:%d/%m}" == esperado, f"esperaba {esperado}")

C2 = {"acme": "Acme", "garcia": "García"}
for frase, esperado in [
    ("Ayer trabajé 2 horas para García",           ("anadir", "garcia", 7200)),
    ("He estado trabajando hora y media con Acme", ("anadir", "acme", 5400)),
    ("El lunes estuve tres horas con Acme",        ("anadir", "acme", 10800)),
    ("Apunta 2 horas a Acme",                      ("anadir", "acme", 7200)),
    ("Súmale media hora a García",                 ("anadir", "garcia", 1800)),
    ("Apúntame 45 minutos para García del lunes",  ("anadir", "garcia", 2700)),
    ("Ayer trabajé 3 horas para Iberdrola",        ("anadir", "iberdrola", 10800)),
    ("Quítale media hora a Acme",                  ("restar", "acme", 1800)),
    ("Réstale 20 minutos a García",                ("restar", "garcia", 1200)),
    ("Borra la última sesión",                     ("borrar_ultima", None, None)),
]:
    r = horas.orden(frase, False, C2)
    obtenido = (r[0], r[1][0], r[1][1]) if r and r[1] else (r[0], None, None) if r else None
    comprueba(f"{frase!r:<46} -> {obtenido}", obtenido == esperado, f"esperaba {esperado}")

print("\n  lo que NO es corregir horas:")
for frase in [
    "Apunta una reunión de 2 horas mañana con Ana",   # es una tarea
    "Apunta 2 horas a Acme para mañana",              # futuro: tarea
    "He estado dos horas en el médico",               # no es trabajo
    "Estuve dos horas con mi madre",                  # ni cliente conocido
    "Recuérdame llamar a Ana en 2 horas",
    "Añade comprar leche a la lista",
    "Quita el dentista del 1 de septiembre",          # borrar tarea, no horas
    "Borra la última tarea",
]:
    r = horas.orden(frase, False, C2)
    comprueba(f"{frase!r:<46} -> nada", r is None, str(r))

with memoria._conectar() as con:
    con.execute("DELETE FROM sesiones")
H = datetime(2026, 9, 16, 17, 0)
horas.empezar("acme", datetime(2026, 9, 16, 9, 0))
horas.parar(datetime(2026, 9, 16, 12, 0))                 # 3 h
f = horas.responder("anadir", ("garcia", 7200, "ayer trabaje 2 horas para garcia"), "", H)
comprueba("apuntar a mano: lo repite con el día", f == "Apuntadas 2 horas para Garcia ayer.", f)
comprueba("y cuenta en el día que se dijo",
          horas.totales(datetime(2026, 9, 15), datetime(2026, 9, 16), H)["garcia"][1] == 7200)
f = horas.anadir("acme", 20 * 3600, H.date(), H)
comprueba("20 horas en un día no se apuntan: se pide repetir",
          "Me parece mucho" in f and len(horas.sesiones(H.replace(hour=0), H, H)) == 1, f)

f = horas.responder("restar", ("acme", 1800), "", H)
comprueba("restar recorta la última sesión de ese cliente",
          f == "Quitadas 30 minutos a Acme. Su última sesión queda en 2 horas y media.", f)
f = horas.restar("acme", 5 * 3600, H)
comprueba("restar más de lo que dura no toca nada",
          "solo tiene 2 horas y media" in f, f)
comprueba("restar a quien no existe", horas.restar("zzz", 60, H) == "No tengo horas con Zzz.")

horas.empezar("acme", datetime(2026, 9, 16, 15, 0))
horas.restar("acme", 1800, H)
comprueba("restar a una abierta retrasa su inicio (2 h -> 1 h y media)",
          horas.abierta()["inicio"] == datetime(2026, 9, 16, 15, 30))

u = horas.ultima()
comprueba("la última se describe para reconocerla",
          horas.describir(u, H) == "Acme, en marcha desde a las 3 y media de la tarde",
          horas.describir(u, H))
comprueba("y se borra", horas.borrar_sesion(u["id"]) and horas.abierta() is None)
comprueba("borrar dos veces no rompe", not horas.borrar_sesion(u["id"]))
u = horas.ultima()
comprueba("una manual se describe como manual",
          horas.describir(u, H) == "Garcia, 2 horas apuntadas a mano ayer", horas.describir(u, H))

with tempfile.TemporaryDirectory() as tmp:
    ruta = Path(tmp) / "h.csv"
    horas.exportar_csv(datetime(2026, 9, 15), datetime(2026, 9, 16), ruta, H)
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        fila = list(csv.reader(fh, delimiter=";"))[1]
    comprueba("en el CSV, la manual sin horas inventadas",
              fila == ["Garcia", "15/09/2026", "", "(apuntada a mano)", "2,00"], fila)

# Una base de datos de antes de esta versión, sin la columna "manual"
with memoria._conectar() as con:
    con.execute("DROP TABLE sesiones")
    con.execute("CREATE TABLE sesiones (id INTEGER PRIMARY KEY, cliente TEXT NOT NULL, "
                "clave TEXT NOT NULL, inicio TEXT NOT NULL, fin TEXT)")
    con.execute("INSERT INTO sesiones (cliente, clave, inicio, fin) VALUES "
                "('Acme', 'acme', '2026-09-16T09:00:00', '2026-09-16T10:00:00')")
horas.preparar()
comprueba("una base de datos vieja se migra sin perder sesiones",
          horas.totales(datetime(2026, 9, 16), datetime(2026, 9, 17), H)["acme"][1] == 3600)

os.remove(memoria.BASE)

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
