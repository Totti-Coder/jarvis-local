"""Seguir el hilo: "bórralo", "cámbialo a las 6", "¿a qué hora era?".

EL PROBLEMA

Cada frase empezaba de cero. Acababas de apuntar el dentista y, para
moverlo, había que repetir el nombre entero: "cambia el dentista a las
seis". Hablando eso cansa, porque nadie habla así: se dice "cámbialo".

CÓMO

Se recuerda LA ÚLTIMA cosa de la que se habló —una tarea, por ahora— y
las frases con pronombre se resuelven contra ella. Si no hay nada
reciente, la frase sigue su camino de siempre: es mejor no entender que
borrar la tarea equivocada.

El hilo es POR PESTAÑA, como la conversación: dos ventanas abiertas son
dos conversaciones distintas, y "bórralo" en una no puede referirse a lo
que se dijo en la otra.

Y dura poco a propósito (5 minutos): "bórralo" media hora después casi
seguro habla de otra cosa.

Stdlib pura: corre en el CI.
"""

import re
import unicodedata
from datetime import datetime, timedelta

MINUTOS = 5          # lo que se recuerda de qué se estaba hablando


def _normal(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9: ]+", " ", s).split())


# "Bórralo", "quítalo", "ya está hecho": quitar lo último de lo que se habló
_QUITAR = re.compile(
    r"^(?:vale |pues |y )?(?:ya )?(?:"
    r"borra(?:lo|la)|quita(?:lo|la)|elimina(?:lo|la)|cancela(?:lo|la)|"
    r"anula(?:lo|la)|(?:ya )?esta hecho|(?:ya )?lo he hecho|(?:ya )?lo hice|"
    r"marca(?:lo|la) como hech[oa]|dalo por hecho|fuera"
    r")$")

# "Cámbialo a las 6", "muévelo al jueves", "ponlo a las nueve y media"
_CAMBIAR = re.compile(
    r"^(?:vale |pues |y )?(?:"
    r"cambia(?:lo|la)|mueve(?:lo|la)|muevelo|pon(?:lo|la)|pasa(?:lo|la)|"
    r"dejalo|dejala|adelanta(?:lo|la)|retrasa(?:lo|la)"
    # El "a" de "a las 6" NO se quita: el intérprete de fechas lo necesita
    # para saber que ahí viene una hora ("las 6" suelto no lo entiende)
    r")\s+(?:mejor\s+)?(?P<cuando>.+)$")

# "¿A qué hora era?", "¿cuándo era?"
_CUANDO = re.compile(
    r"^(?:y )?(?:a que hora|cuando|que dia)\s+(?:era|es|lo tengo|la tengo|"
    r"queda|habia quedado|decia)\s*\??$")


class Hilo:
    """De qué se estaba hablando en esta pestaña."""

    def __init__(self):
        self.tarea = None          # {"id", "texto", "cuando_iso", "tiene_hora"}
        self.cuando = None         # cuándo se habló de ella

    def apuntar_tarea(self, tarea, ahora=None):
        self.tarea = dict(tarea) if tarea else None
        self.cuando = ahora or datetime.now()

    def olvidar(self):
        self.tarea = self.cuando = None

    def tarea_reciente(self, ahora=None):
        """La última tarea, si se habló de ella hace poco."""
        if not self.tarea or not self.cuando:
            return None
        ahora = ahora or datetime.now()
        if ahora - self.cuando > timedelta(minutes=MINUTOS):
            return None
        return self.tarea


def referencia(frase):
    """(acción, dato) si la frase habla de "eso" sin nombrarlo.

    Acciones: quitar, cambiar (con el cuándo dicho), cuando.
    """
    t = _normal(frase)
    if not t:
        return None
    if _QUITAR.match(t):
        return ("quitar", None)
    if _CUANDO.match(t):
        return ("cuando", None)
    m = _CAMBIAR.match(t)
    if m:
        cuando = m.group("cuando").strip()
        # "Cámbialo" a secas no dice a qué: eso lo pregunta el servidor
        return ("cambiar", cuando) if cuando else None
    return None
