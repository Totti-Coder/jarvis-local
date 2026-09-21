"""Rutinas: una frase, varios pasos. "Modo trabajo" abre tus programas,
cierra las distracciones, pone el cronómetro y empieza a contar horas.

Se definen en atajos.json, junto a los atajos, bajo "rutinas":

    {"nombre": "modo trabajo", "alias": ["empezamos"],
     "pasos": [{"abrir": "vs code"}, {"cerrar": "discord"},
               {"cronometro": "empezar"}, {"horas": "Acme"}],
     "dice": "A por ello."}

SEGURIDAD: SOLO PASOS DE UNA LISTA CERRADA

Un paso no es un comando: es uno de estos tipos, y cada uno reutiliza la
pieza que ya existe con su propia protección:

    abrir       programa de la lista blanca o del menú Inicio
    cerrar      programa abierto (nunca procesos del sistema)
    atajo       un atajo tuyo de atajos.json, por su nombre
    cronometro  empezar, pausar, reiniciar...
    horas       un cliente, o "parar"
    temporizador  minutos, en cifra: "25" es un pomodoro

Para ejecutar un comando arbitrario, se crea un ATAJO (donde el comando se
escribe entero y revisado) y la rutina lo llama por su nombre. Así hay un
único sitio del que salen comandos. Los atajos que piden confirmación no
se pueden meter en una rutina: saltarse esa pregunta es justo lo que la
confirmación impide.

EL NOMBRE TIENE QUE COINCIDIR ENTERO

"Modo trabajo", "pon el modo trabajo" o "activa modo trabajo" lanzan la
rutina; "¿qué es el modo trabajo?" no. Una rutina hace varias cosas de
golpe, así que se prefiere no lanzarla a lanzarla sin querer.

Stdlib pura: corre en el CI.
"""

import json
import re
import unicodedata

import sistema

FICHERO = sistema.ATAJOS

TIPOS = ("abrir", "cerrar", "atajo", "cronometro", "horas", "temporizador")

# Lo que se dice delante del nombre sin cambiar lo que se pide
_DELANTE = {"jarvis", "vale", "venga", "oye", "pon", "ponme", "activa",
            "activame", "arranca", "inicia", "lanza", "entra", "en", "el",
            "la", "mi", "rutina", "de", "por", "favor"}


def _normal(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def cargar(fichero=None):
    """Las rutinas válidas. Una mal definida se salta entera, con aviso:
    ejecutar la mitad de una rutina es peor que no ejecutarla."""
    fichero = fichero or FICHERO
    try:
        datos = json.loads(fichero.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[rutinas] {fichero.name} no se puede leer: {e}")
        return []

    peligrosos = {_normal(a.get("nombre", "")) for a in datos.get("atajos", [])
                  if a.get("confirma")}
    salida = []
    for r in datos.get("rutinas", []):
        nombre = (r.get("nombre") or "").strip()
        pasos, error = [], None
        for p in r.get("pasos") or []:
            if not isinstance(p, dict) or len(p) != 1:
                error = f"paso mal escrito: {p}"
                break
            tipo, valor = next(iter(p.items()))
            if tipo not in TIPOS or not isinstance(valor, str) or not valor.strip():
                error = f"paso no permitido: {p}"
                break
            if tipo == "temporizador" and not (valor.strip().isdigit()
                                                and 0 < int(valor) <= 24 * 60):
                error = f"el temporizador va en minutos, de 1 a 1440: {p}"
                break
            if tipo == "atajo" and _normal(valor) in peligrosos:
                error = f"el atajo {valor!r} pide confirmación y no puede ir en una rutina"
                break
            pasos.append((tipo, valor.strip()))
        if not nombre or not pasos or error:
            print(f"[rutinas] {nombre or r}: {error or 'sin nombre o sin pasos'}, la salto")
            continue
        salida.append({"nombre": nombre,
                       "claves": [_normal(nombre)] + [_normal(a) for a in r.get("alias", [])],
                       "pasos": pasos,
                       "dice": r.get("dice") or f"{nombre.capitalize()}, listo."})
    return salida


def buscar(texto, rutinas=None):
    """La rutina que se pide, o None. El nombre tiene que ser la frase
    entera, quitando solo "pon", "activa", "el"... de delante."""
    rutinas = cargar() if rutinas is None else rutinas
    if not rutinas:
        return None
    palabras = _normal(texto).split()
    while palabras and palabras[-1] in ("por", "favor", "jarvis"):
        palabras.pop()
    # Se prueba la frase entera ANTES de quitar cada palabra: una rutina
    # que se llame "la hora de comer" no puede perder su "la"
    while palabras:
        frase = " ".join(palabras)
        for r in rutinas:
            if frase in r["claves"]:
                return r
        if palabras[0] not in _DELANTE:
            return None
        palabras.pop(0)
    return None
