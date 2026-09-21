"""Temporizador: "pon un pomodoro", "avísame en 10 minutos", "¿cuánto queda?".

El cronómetro cuenta hacia arriba; esto hacia abajo, y AVISA al acabar. Es
lo que pide el "modo trabajo": veinticinco minutos de foco y un aviso.

Como el cronómetro, las órdenes no pasan por el modelo (frases fijas) y
hay uno por pestaña. La cuenta atrás la lleva el servidor, que es quien
habla al terminar; la página solo la pinta.

Una frase con contenido ("recuérdame sacar la pizza en 10 minutos") NO es
un temporizador: es una tarea con hora, y va a la agenda como siempre.
Aquí solo entra "avísame en 10 minutos" a secas, o lo que diga
"temporizador" o "pomodoro".

Stdlib pura: corre en el CI.
"""

import re

from cronometro import tiempo_hablado
from horas import _normal, duracion_en

POMODORO_MIN = 25

_NOMBRE = r"(?:temporizador|pomodoro|cuenta atras)"
_CANCELAR = re.compile(r"\b(?:cancela|cancelalo|quita|quitalo|para|paralo|deten|"
                       r"detenlo|borra|apaga|anula)\b(?: (?:el|la|mi))? " + _NOMBRE)
_SOLO_AVISAME = re.compile(r"^(?:jarvis |vale |oye )*(?:avisame|dime algo|llamame) "
                           r"(?:en|dentro de) (?P<dur>.+?)(?: por favor)?$")
_QUEDA = re.compile(r"\b(?:cuanto (?:le )?(?:queda|falta)|cuanto tiempo (?:queda|falta)|"
                    r"como va el " + _NOMBRE + r")\b")


def orden(texto, activo=False):
    """(acción, segundos) o None. Acciones: poner, sin_duracion, consultar,
    cancelar."""
    t = _normal(texto)
    if not t:
        return None

    if _CANCELAR.search(t):
        return ("cancelar", None)
    if activo and t in ("cancelalo", "quitalo", "cancela", "anulalo"):
        return ("cancelar", None)

    if re.search(r"\b" + _NOMBRE + r"\b", t):
        if _QUEDA.search(t):
            return ("consultar", None)
        segundos = duracion_en(t)
        if segundos:
            return ("poner", segundos)
        # Sin duración hace falta pedirlo de verdad: "¿qué es un pomodoro?"
        # habla DEL pomodoro, no pide uno
        pide = re.search(r"\b(?:pon|ponme|pone|poner|inicia|empieza|arranca|activa|"
                         r"quiero|hazme|otro|vamos con|empezamos)\b", t)
        suelto = re.fullmatch(r"(?:jarvis |vale |venga )*(?:un |el )?" + _NOMBRE, t)
        if not (pide or suelto):
            return None
        if "pomodoro" in t:
            return ("poner", POMODORO_MIN * 60)
        return ("sin_duracion", None)

    m = _SOLO_AVISAME.match(t)
    if m:
        segundos = duracion_en(m.group("dur"))
        # "en 10 minutos DE sacar la pizza" lleva contenido: es una tarea.
        # Pero el "de" de "un cuarto de hora" es parte del tiempo
        contenido = re.search(r"\b(?:minutos?|horas?)\s+(?:de|que|para)\b", m.group("dur"))
        if segundos and not contenido:
            return ("poner", segundos)

    if activo and _QUEDA.search(t):
        return ("consultar", None)
    return None


def frase_puesto(segundos):
    if segundos == POMODORO_MIN * 60:
        return "Pomodoro en marcha: veinticinco minutos. Te aviso al acabar."
    return f"Temporizador de {tiempo_hablado(segundos)}. Te aviso al acabar."


def frase_acabado(segundos):
    if segundos == POMODORO_MIN * 60:
        return "Pomodoro terminado. Toca descansar cinco minutos."
    return f"Se acabó el tiempo: {tiempo_hablado(segundos)}."


def frase_queda(restante):
    if restante <= 0:
        return "Está a punto de sonar."
    return f"Quedan {tiempo_hablado(restante)}."
