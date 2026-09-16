"""La agenda junta lo propio y lo que hay en Google Calendar, sin repetir.

Antes Jarvis solo miraba su SQLite: lo que creabas a mano en Calendar no
existia para el. Y ademas podia BORRAR un evento del calendario que no era
capaz de LISTARTE, que no tiene sentido.

Aqui no se toca Google: el calendario es una funcion falsa que devuelve
siempre lo mismo, y la base de datos es un fichero temporal. Asi el test da
lo mismo hoy que dentro de un mes, y puede correr en el CI.

Uso:  python test_agenda_externa.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import calendario
import memoria

memoria.BASE = Path(__file__).parent / "_test_agenda_externa.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

# Lunes 7 de septiembre de 2026, 12:00
AHORA = datetime(2026, 9, 7, 12, 0)
fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


def propia(texto, cuando_iso, tiene_hora=1, evento_id=None):
    """Mete una tarea a mano, sin pasar por Google.

    Con memoria._conectar() y no con sqlite3.connect() a pelo: el "with" de
    sqlite3 confirma pero NO cierra, y en Windows el fichero se queda
    bloqueado y no se puede borrar al terminar.
    """
    with memoria._conectar() as con:
        con.execute("INSERT INTO tareas (texto, cuando_texto, cuando_iso, "
                    "tiene_hora, hecha, creada, evento_id) "
                    "VALUES (?, '', ?, ?, 0, ?, ?)",
                    (texto, cuando_iso, tiene_hora, AHORA.isoformat(), evento_id))


def evento(texto, cuando_iso, tiene_hora=True, id_=None, marcado=False):
    return {"texto": texto, "cuando_iso": cuando_iso, "tiene_hora": tiene_hora,
            "id": id_ or f"g-{texto}-{cuando_iso}", "marcado": marcado}


# Mañana: una propia a las 5, y en el calendario...
propia("examen de economia", "2026-09-08T17:00:00", evento_id="ya-mio")
CALENDARIO = [
    evento("dentista", "2026-09-08T09:00:00"),                   # solo en Google
    evento("examen de economia", "2026-09-08T17:00:00",
           id_="ya-mio"),                                        # copia de la propia
    evento("reunion", "2026-09-08T11:00:00", marcado=True),      # creada por Jarvis
    evento("gimnasio", "2026-09-10T19:00:00"),                   # otro dia
] + [evento("comprar pan", "2026-09-08T00:00:00", tiene_hora=False,
            id_=f"pan-{i}") for i in range(20)]                   # 20 repetidos

pedidos = []


def calendario_falso(desde, hasta):
    pedidos.append((desde, hasta))
    return [e for e in CALENDARIO
            if desde <= datetime.fromisoformat(e["cuando_iso"]) <= hasta]


print("=" * 70)
print("SIN CALENDARIO: igual que siempre")
print("=" * 70)
memoria.fuente_externa = None
r = memoria.listar_tareas("mañana", ahora=AHORA)
comprueba("solo sale lo propio", r == "Mañana tienes: examen de economia a las 5 de la tarde.", r)

print("\n" + "=" * 70)
print("CON CALENDARIO")
print("=" * 70)
memoria.fuente_externa = calendario_falso
r = memoria.listar_tareas("mañana", ahora=AHORA)
print(f"  -> {r}")
comprueba("sale el dentista, que solo esta en Google", "dentista" in r)
comprueba("el examen sale UNA vez, no dos",
          r.count("examen de economia") == 1, r)
comprueba("la reunion creada por Jarvis no se cuela", "reunion" not in r, r)
comprueba("el pan repetido 20 veces se dice una", r.count("comprar pan") == 1, r)
comprueba("el gimnasio del jueves no sale en mañana", "gimnasio" not in r, r)
comprueba("va en orden: primero las 9, luego las 5",
          r.find("dentista") < r.find("examen"), r)

r = memoria.listar_tareas("", texto="dentista", ahora=AHORA)
comprueba("preguntar por nombre encuentra lo de Google",
          "dentista" in r and "9 de la mañana" in r, r)

pedidos.clear()
memoria.listar_tareas("", ahora=AHORA)
desde, hasta = pedidos[-1]
comprueba("sin ventana, solo mira hacia delante",
          desde == AHORA and (hasta - desde).days == memoria.DIAS_EXTERNOS,
          f"pidio de {desde} a {hasta}")


print("\n" + "=" * 70)
print("EL SALUDO DE LA MAÑANA")
print("=" * 70)
MARTES = datetime(2026, 9, 8, 8, 0)
CALENDARIO.append(evento("dentista", "2026-08-20T09:00:00"))   # huerfano viejo
r = memoria.resumen_del_dia(ahora=MARTES)
print(f"  -> {r}")
comprueba("incluye lo de hoy que solo esta en Google", "dentista" in r, r)
comprueba("sin repetir lo propio", r.count("examen de economia") == 1, r)
comprueba("lo atrasado de fuera NO se cuenta", "atrasad" not in r, r)


def que_revienta(desde, hasta):
    raise ConnectionError("sin red")


memoria.fuente_externa = que_revienta
r = memoria.listar_tareas("mañana", ahora=AHORA)
comprueba("si Google falla, sale lo propio sin error",
          r == "Mañana tienes: examen de economia a las 5 de la tarde.", r)

print("\n" + "=" * 70)
print("DE EVENTO DE GOOGLE A TAREA")
print("=" * 70)
# Misma hora dicha de dos formas: con zona de Madrid y en UTC. Tienen que
# dar lo mismo en hora local, sea cual sea la zona de quien lo ejecute.
a = calendario.como_tarea({"id": "a", "summary": "x",
                           "start": {"dateTime": "2026-09-01T09:00:00+02:00"}})
b = calendario.como_tarea({"id": "b", "summary": "x",
                           "start": {"dateTime": "2026-09-01T07:00:00Z"}})
comprueba("la zona horaria no cambia el momento", a["cuando_iso"] == b["cuando_iso"],
          f"{a['cuando_iso']} vs {b['cuando_iso']}")
comprueba("sale sin zona, comparable con SQLite",
          datetime.fromisoformat(a["cuando_iso"]).tzinfo is None)
d = calendario.como_tarea({"id": "d", "summary": " comprar pan ",
                           "start": {"date": "2026-09-01"}})
comprueba("dia completo: sin hora", d["tiene_hora"] is False
          and d["cuando_iso"] == "2026-09-01T00:00:00", str(d))
comprueba("el titulo se limpia", d["texto"] == "comprar pan")
comprueba("un evento sin inicio se ignora",
          calendario.como_tarea({"id": "z", "summary": "raro"}) is None)
m = calendario.como_tarea({"id": "m", "summary": "x",
                           "start": {"date": "2026-09-01"},
                           "extendedProperties": {"private": {"origen": "jarvis"}}})
comprueba("se sabe si lo creo Jarvis", m["marcado"] is True)

print("\n" + "=" * 70)
print("LOS TESTS NO ESCRIBEN EN TU CALENDARIO")
print("=" * 70)
# Paso de verdad: test_resumen y test_ventanas usaban una base temporal,
# pero anadir_tarea() llamaba a Google igualmente. Cada vez que corria la
# bateria con el permiso vigente, siete eventos nuevos en el calendario
# real: mas de doscientos en unas semanas. Esto vigila que no vuelva.
llamadas = []
calendario_crear, calendario_borrar = calendario.crear_evento, calendario.borrar_evento
calendario.crear_evento = lambda *a, **k: llamadas.append(("crear", a)) or "id-falso"
calendario.borrar_evento = lambda *a, **k: llamadas.append(("borrar", a)) or True

memoria.fuente_externa = memoria.crear_externo = memoria.borrar_externo = None
memoria.anadir_tarea("espia", "mañana a las 10")
memoria.completar_tarea("espia")
memoria.anadir_tarea("espia dos", "mañana a las 11")
memoria.completar_varias(memoria.tareas_del_grupo("todas"))
comprueba("sin enganchar, memoria no toca Google", llamadas == [], str(llamadas))

# Y enganchado —como hace el servidor— si lo hace
memoria.crear_externo = calendario.crear_evento
memoria.borrar_externo = calendario.borrar_evento
memoria.anadir_tarea("espia tres", "mañana a las 12")
memoria.completar_tarea("espia tres")
tipos = [t for t, _ in llamadas]
comprueba("enganchado, crea y borra en Google", tipos == ["crear", "borrar"], str(tipos))

calendario.crear_evento, calendario.borrar_evento = calendario_crear, calendario_borrar
memoria.fuente_externa = memoria.crear_externo = memoria.borrar_externo = None
os.remove(memoria.BASE)

print("\n" + "=" * 70)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 70)
sys.exit(1 if fallos else 0)
