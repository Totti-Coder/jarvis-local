"""La papelera: poder deshacer lo que se borró por error.

POR QUÉ

Jarvis pregunta antes de borrar, y está bien. Pero la confianza no viene de
preguntar mucho: viene de poder equivocarse. Un "sí" dicho de más, o una
frase mal oída que suena a "sí", y hoy no había vuelta atrás.

Y hay una parte que ni siquiera se podía rescatar a mano: las tareas se
marcan como hechas (siguen en SQLite), pero su evento de Google Calendar
se borra de verdad. Por eso aquí se guarda lo necesario para VOLVER A
CREARLO, no solo los identificadores.

QUÉ SE GUARDA

    tareas    las que se marcaron hechas, y los eventos que tenían
    eventos   los de Google que no estaban en la lista
    sesion    una sesión de horas borrada

De cada cosa se guarda lo imprescindible para recrearla, en JSON. Se
conservan 30 días; lo más viejo se va solo.

Stdlib pura: corre en el CI. El calendario entra por un hook, como en
memoria.py, para que los tests no toquen Google.
"""

import json
from datetime import datetime, timedelta

import memoria

DIAS = 30

# Lo pone el servidor si hay Google conectado: crear_externo(texto, iso, tiene_hora)
crear_externo = None


def preparar():
    with memoria._conectar() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS papelera (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            cuando   TEXT NOT NULL,
            tipo     TEXT NOT NULL,
            resumen  TEXT NOT NULL,     -- cómo se cuenta en voz alta
            datos    TEXT NOT NULL,     -- lo necesario para recrearlo
            deshecha INTEGER DEFAULT 0)""")


def apuntar(tipo, resumen, datos, ahora=None):
    """Guarda algo que se acaba de borrar. Devuelve su id."""
    ahora = ahora or datetime.now()
    with memoria._conectar() as con:
        cur = con.execute(
            "INSERT INTO papelera (cuando, tipo, resumen, datos) VALUES (?, ?, ?, ?)",
            (ahora.isoformat(), tipo, resumen, json.dumps(datos, ensure_ascii=False)))
        return cur.lastrowid


def apuntar_tareas(filas, ahora=None):
    """Lo llama memoria al completar tareas (ver memoria.al_borrar)."""
    filas = [dict(f) for f in filas or []]
    if not filas:
        return None
    nombres = "; ".join(f["texto"] for f in filas)
    resumen = (f"{filas[0]['texto']}" if len(filas) == 1
               else f"{len(filas)} cosas: {nombres}")
    return apuntar("tareas", resumen, {"tareas": [
        {"id": f["id"], "texto": f["texto"], "cuando_iso": f.get("cuando_iso"),
         "tiene_hora": bool(f.get("tiene_hora")), "evento_id": f.get("evento_id")}
        for f in filas]}, ahora)


def apuntar_eventos(eventos, ahora=None):
    """Eventos de Google que no estaban en la lista. Cada uno:
    {"texto", "cuando_iso", "tiene_hora"}."""
    eventos = [dict(e) for e in eventos or []]
    if not eventos:
        return None
    resumen = (eventos[0]["texto"] if len(eventos) == 1
               else f"{len(eventos)} del calendario")
    return apuntar("eventos", resumen, {"eventos": eventos}, ahora)


def apuntar_sesion(fila, ahora=None):
    """Una sesión de horas borrada, con todo lo suyo."""
    if not fila:
        return None
    return apuntar("sesion", f"la sesión de {fila['cliente']}",
                   {"sesion": dict(fila)}, ahora)


# ---------------------------------------------------------------
# DESHACER
# ---------------------------------------------------------------

def ultima(ahora=None):
    """Lo último que se puede deshacer, o None si no hay nada."""
    ahora = ahora or datetime.now()
    limite = (ahora - timedelta(days=DIAS)).isoformat()
    with memoria._conectar() as con:
        f = con.execute("SELECT * FROM papelera WHERE deshecha = 0 AND cuando >= ? "
                        "ORDER BY id DESC LIMIT 1", (limite,)).fetchone()
    return dict(f) if f else None


def deshacer(ahora=None):
    """Devuelve lo último a su sitio. Devuelve la frase que dirá Jarvis."""
    ahora = ahora or datetime.now()
    entrada = ultima(ahora)
    if not entrada:
        return "No tengo nada reciente que deshacer."

    try:
        datos = json.loads(entrada["datos"])
    except ValueError:
        return "No he podido leer lo que había guardado."

    if entrada["tipo"] == "tareas":
        frase = _volver_tareas(datos.get("tareas", []))
    elif entrada["tipo"] == "eventos":
        frase = _volver_eventos(datos.get("eventos", []))
    elif entrada["tipo"] == "sesion":
        frase = _volver_sesion(datos.get("sesion"))
    else:
        return "Eso no sé deshacerlo."

    with memoria._conectar() as con:
        con.execute("UPDATE papelera SET deshecha = 1 WHERE id = ?", (entrada["id"],))
    limpiar(ahora)
    return frase


def _volver_tareas(tareas):
    if not tareas:
        return "No había nada que devolver."
    ids = [t["id"] for t in tareas]
    with memoria._conectar() as con:
        con.execute(f"UPDATE tareas SET hecha = 0 "
                    f"WHERE id IN ({','.join('?' * len(ids))})", ids)

    # Lo que estaba en Google se borró de verdad: hay que volver a crearlo,
    # y el evento nuevo tiene otro id, así que se apunta el nuevo
    recreados = 0
    for t in tareas:
        if not (t.get("evento_id") and crear_externo and t.get("cuando_iso")):
            continue
        try:
            nuevo = crear_externo(t["texto"], t["cuando_iso"], t.get("tiene_hora"))
        except Exception as e:
            print(f"[papelera] no se pudo recrear el evento: {e}")
            nuevo = None
        if nuevo:
            recreados += 1
            with memoria._conectar() as con:
                con.execute("UPDATE tareas SET evento_id = ? WHERE id = ?",
                            (nuevo, t["id"]))

    cuantas = len(tareas)
    frase = (f"Recuperado: {tareas[0]['texto']}." if cuantas == 1
             else f"Recuperadas {cuantas} cosas.")
    if recreados:
        frase += f" {'También está' if recreados == 1 else 'También están'} otra vez en el calendario."
    return frase


def _volver_eventos(eventos):
    if not crear_externo:
        return "No puedo recrearlos: el calendario no está conectado."
    hechos = 0
    for e in eventos:
        try:
            if crear_externo(e["texto"], e.get("cuando_iso"), e.get("tiene_hora")):
                hechos += 1
        except Exception as err:
            print(f"[papelera] no se pudo recrear el evento: {err}")
    if not hechos:
        return "No he podido devolverlos al calendario."
    return (f"Devuelto al calendario: {eventos[0]['texto']}." if hechos == 1
            else f"Devueltos {hechos} al calendario.")


def _volver_sesion(sesion):
    if not sesion:
        return "No había nada que devolver."
    with memoria._conectar() as con:
        con.execute("INSERT INTO sesiones (cliente, clave, inicio, fin, manual) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (sesion["cliente"], sesion["clave"], sesion["inicio"],
                     sesion.get("fin"), int(sesion.get("manual") or 0)))
    return f"Recuperada la sesión de {sesion['cliente']}."


def limpiar(ahora=None):
    """Tira lo de más de 30 días. Devuelve cuántas entradas quitó."""
    ahora = ahora or datetime.now()
    limite = (ahora - timedelta(days=DIAS)).isoformat()
    with memoria._conectar() as con:
        return con.execute("DELETE FROM papelera WHERE cuando < ?", (limite,)).rowcount
