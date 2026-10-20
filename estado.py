"""El cronómetro y el temporizador, uno solo y para todas las pestañas.

POR QUÉ CAMBIÓ

Antes vivían dentro de la conexión del navegador: uno por pestaña. Con dos
pestañas abiertas había dos cronómetros distintos, y al cerrar la ventana o
reiniciar el servidor se perdían los dos. Un pomodoro que desaparece al
cerrar la pestaña no sirve para concentrarse, que es justo para lo que se
pone.

Ahora son del SERVIDOR, no de la página: una pestaña los enseña y los
maneja, pero quien cuenta es el servidor. Y se guardan en la base de datos,
así que un reinicio no se los lleva.

CÓMO SOBREVIVEN A UN REINICIO

El cronómetro cuenta con un reloj que se pone a cero al reiniciar el
ordenador (monotonic), que es lo correcto para medir ratos. Para guardarlo
hace falta la hora de pared: se apunta cuándo arrancó y, al volver, se suma
lo que pasó mientras el servidor estaba parado.

Stdlib pura: corre en el CI.
"""

import json
from datetime import datetime, timedelta

import cronometro
import memoria

# El temporizador guarda su FIN como hora de pared, por lo mismo
CLAVE_CRONO = "cronometro"
CLAVE_TEMPORIZADOR = "temporizador"


def preparar():
    with memoria._conectar() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS estado (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL)""")


def _guardar(clave, datos):
    with memoria._conectar() as con:
        if datos is None:
            con.execute("DELETE FROM estado WHERE clave = ?", (clave,))
        else:
            con.execute("INSERT INTO estado (clave, valor) VALUES (?, ?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                        (clave, json.dumps(datos)))


def _leer(clave):
    with memoria._conectar() as con:
        f = con.execute("SELECT valor FROM estado WHERE clave = ?", (clave,)).fetchone()
    try:
        return json.loads(f["valor"]) if f else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------
# CRONÓMETRO
# ---------------------------------------------------------------

def guardar_crono(crono, ahora=None):
    ahora = ahora or datetime.now()
    _guardar(CLAVE_CRONO, {
        "visible": crono.visible,
        "acumulado": round(crono.transcurrido(), 2),
        # Si está en marcha se apunta DESDE CUÁNDO, para poder sumar luego
        # lo que corra con el servidor apagado
        "desde": ahora.isoformat() if crono.corriendo else None,
    })


def cargar_crono(ahora=None, reloj=None):
    """El cronómetro tal como quedó, contando lo que pasó mientras tanto."""
    ahora = ahora or datetime.now()
    crono = cronometro.Cronometro(**({"reloj": reloj} if reloj else {}))
    d = _leer(CLAVE_CRONO)
    if not d:
        return crono
    crono.visible = bool(d.get("visible"))
    crono.acumulado = float(d.get("acumulado") or 0)
    if d.get("desde"):
        try:
            parado = (ahora - datetime.fromisoformat(d["desde"])).total_seconds()
        except (ValueError, TypeError):
            parado = 0
        # Lo que corrió con el servidor apagado cuenta igual: el tiempo pasó
        crono.acumulado += max(0.0, parado)
        crono._arrancar()
    return crono


# ---------------------------------------------------------------
# TEMPORIZADOR
# ---------------------------------------------------------------

def guardar_temporizador(fin, segundos):
    """`fin` es hora de pared (datetime), o None para borrarlo."""
    _guardar(CLAVE_TEMPORIZADOR,
             None if fin is None else {"fin": fin.isoformat(), "segundos": segundos})


def cargar_temporizador(ahora=None):
    """(fin, segundos, vencido) o None. `vencido` si sonó con el servidor
    apagado: entonces se dice al volver, en vez de callarlo."""
    ahora = ahora or datetime.now()
    d = _leer(CLAVE_TEMPORIZADOR)
    if not d:
        return None
    try:
        fin = datetime.fromisoformat(d["fin"])
    except (ValueError, TypeError, KeyError):
        return None
    return fin, int(d.get("segundos") or 0), fin <= ahora


def frase_vencido(segundos, fin, ahora=None):
    """Lo que se dice al arrancar si el temporizador venció estando apagado."""
    ahora = ahora or datetime.now()
    hace = (ahora - fin).total_seconds()
    if hace < 60:
        return f"El temporizador de {cronometro.tiempo_hablado(segundos)} acaba de terminar."
    return (f"El temporizador de {cronometro.tiempo_hablado(segundos)} terminó "
            f"hace {cronometro.tiempo_hablado(hace)}.")


def limpiar():
    """Para los tests: deja el estado como recién instalado."""
    _guardar(CLAVE_CRONO, None)
    _guardar(CLAVE_TEMPORIZADOR, None)
