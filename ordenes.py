"""Qué orden fija es una frase, ANTES de preguntar al modelo.

Cronómetro, temporizador, horas y rutinas no pasan por el LLM: son frases
fijas que una lista de patrones resuelve al instante y sin fallar. Aquí
está la decisión entera, en el orden en que se prueba, como una función
pura: recibe la frase y el estado, y devuelve qué hacer.

Así la usan igual el servidor (que después ejecuta) y eval_ordenes.py (que
mide cuántas frases reales se entienden). Si estuviera repartida dentro del
servidor, la evaluación mediría una copia, y las copias se separan.

Stdlib pura: corre en el CI.
"""

from dataclasses import dataclass, field

import cronometro
import horas
import rutinas
import temporizador


@dataclass
class Estado:
    """Lo que cambia el significado de una frase. "Para" solo es una orden
    con el cronómetro a la vista; "he terminado", con una sesión abierta."""
    crono_visible: bool = False
    temporizador_activo: bool = False
    sesion_abierta: bool = False
    clientes: dict = field(default_factory=dict)
    rutinas: list = None               # None: se leen de atajos.json


def detectar(frase, estado=None):
    """(tipo, dato) o None si la frase es para el modelo.

    Tipos:
      encadenadas   [("temporizador", (acción, segundos)) | ("cronometro", acción), ...]
      temporizador  (acción, segundos)
      cronometro    acción
      horas         (acción, dato)
      quitar_rutina rutina
      rutina        rutina
    """
    estado = estado or Estado()

    # Varias órdenes en una frase: "empieza el cronómetro y avísame a los 25
    # minutos". Solo si TODAS se entienden: si una no, la frase sigue entera
    # su camino, que es mejor que hacer la mitad
    partes = cronometro.trozos(frase)
    if len(partes) > 1:
        plan, resto, visible = [], [], estado.crono_visible
        for parte in partes:
            t = temporizador.orden(parte, estado.temporizador_activo)
            c = None if t else cronometro.orden(parte, visible)
            if not (t or c):
                resto.append(parte)
                continue
            plan.append(("temporizador", t) if t else ("cronometro", c))
            visible = visible or c in ("abrir", "empezar", "reanudar", "reiniciar")
        if plan and not resto:
            return ("encadenadas", plan)
        if plan and resto:
            # Media frase es una orden fija y la otra media no ("borra las
            # tres tareas y abre el cronómetro"). Antes ganaba la orden fija
            # sobre la frase ENTERA y la otra mitad se perdía sin avisar.
            # Ahora se hace la que se entiende y el resto sigue su camino.
            return ("parciales", (plan, " y ".join(resto)))

    t = temporizador.orden(frase, estado.temporizador_activo)
    if t:
        return ("temporizador", t)
    c = cronometro.orden(frase, estado.crono_visible)
    if c:
        return ("cronometro", c)
    h = horas.orden(frase, estado.sesion_abierta, estado.clientes)
    if h:
        return ("horas", h)
    r = rutinas.buscar_quitar(frase, estado.rutinas)
    if r:
        return ("quitar_rutina", r)
    r = rutinas.buscar(frase, estado.rutinas)
    if r:
        return ("rutina", r)
    return None
