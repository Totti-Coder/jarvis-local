"""El tiempo: "¿qué tiempo hace?", "¿va a llover mañana en Bilbao?".

POR QUÉ OPEN-METEO Y NO OPENWEATHER

OpenWeather obliga a registrarse para tener una clave, y su One Call 3.0
pide además una tarjeta aunque no pases del plan gratuito. Este proyecto
promete funcionar sin registrarte en nada y sin una sola clave de API, y
esa promesa vale más que la diferencia entre dos fuentes de datos.

Open-Meteo no pide clave para uso personal, y sus datos vienen de los
servicios meteorológicos nacionales (AEMET entre ellos).

CÓMO

  - Dos llamadas: una para saber dónde está el sitio (geocodificación) y
    otra para el tiempo. Las dos se guardan en caché: preguntar dos veces
    seguidas por Madrid no vuelve a salir a internet.
  - La respuesta se redacta AQUÍ, con los números tal cual vienen. No pasa
    por el modelo: una temperatura que el modelo reformula es una
    temperatura que puede cambiar por el camino.
  - `traer` se puede sustituir: los tests no tocan la red.

Stdlib pura: corre en el CI.

Uso por consola:
    python tiempo.py             el tiempo donde vives
    python tiempo.py Bilbao      en otro sitio
"""

import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

GEO = "https://geocoding-api.open-meteo.com/v1/search"
API = "https://api.open-meteo.com/v1/forecast"

CACHE_S = 600          # 10 minutos: el tiempo no cambia más rápido que eso
DIAS = 7               # lo que da la previsión gratuita
ESPERA_S = 6

_cache = {}

# Códigos WMO, que es como viene el estado del cielo. Dicho como lo diría
# una persona: "cielo despejado", no "code 0".
CIELO = {
    0: "cielo despejado", 1: "casi despejado", 2: "algunas nubes",
    3: "nublado", 45: "con niebla", 48: "con niebla helada",
    51: "con llovizna", 53: "con llovizna", 55: "con llovizna fuerte",
    56: "con llovizna helada", 57: "con llovizna helada",
    61: "con lluvia floja", 63: "con lluvia", 65: "con lluvia fuerte",
    66: "con lluvia helada", 67: "con lluvia helada fuerte",
    71: "con nieve floja", 73: "con nieve", 75: "con nieve fuerte",
    77: "con aguanieve", 80: "con chubascos", 81: "con chubascos",
    82: "con chubascos fuertes", 85: "con nevadas", 86: "con nevadas fuertes",
    95: "con tormenta", 96: "con tormenta y granizo", 99: "con tormenta y granizo",
}


def traer(url):
    """Descarga y devuelve el JSON. Se sustituye en los tests."""
    with urllib.request.urlopen(url, timeout=ESPERA_S) as r:
        return json.loads(r.read().decode("utf-8"))


def _cacheado(clave, hacer, ahora=None):
    ahora = ahora or time.monotonic()
    guardado = _cache.get(clave)
    if guardado and ahora - guardado[0] < CACHE_S:
        return guardado[1]
    valor = hacer()
    _cache[clave] = (ahora, valor)
    return valor


def _sin_tildes(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


# ---------------------------------------------------------------
# DÓNDE
# ---------------------------------------------------------------

def donde_esta(lugar):
    """(nombre bonito, latitud, longitud) o None si no existe ese sitio."""
    if not (lugar or "").strip():
        return None

    def pedir():
        url = GEO + "?" + urllib.parse.urlencode(
            {"name": lugar.strip(), "count": 1, "language": "es", "format": "json"})
        return traer(url)

    try:
        d = _cacheado(("geo", _sin_tildes(lugar)), pedir)
    except Exception as e:
        print(f"[tiempo] no se pudo buscar {lugar!r}: {e}")
        return None
    sitios = (d or {}).get("results") or []
    if not sitios:
        return None
    s = sitios[0]
    return s.get("name", lugar), s["latitude"], s["longitude"]


# ---------------------------------------------------------------
# QUÉ TIEMPO HACE
# ---------------------------------------------------------------

def _grados(x):
    return f"{round(x)} grados" if abs(round(x)) != 1 else "1 grado"


def _prevision(lat, lon):
    def pedir():
        url = API + "?" + urllib.parse.urlencode({
            "latitude": round(lat, 3), "longitude": round(lon, 3),
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max"),
            "timezone": "auto", "forecast_days": DIAS,
        })
        return traer(url)
    return _cacheado(("tiempo", round(lat, 2), round(lon, 2)), pedir)


def _dia_pedido(texto, ahora):
    """Qué día quiere: (índice dentro de la previsión, cómo se dice)."""
    import memoria
    t = _sin_tildes(texto)
    if not t or "ahora" in t or "hoy" in t:
        return 0, "hoy"
    cuando, _ = memoria.interpretar_cuando(texto, ahora)
    if not cuando:
        # "El fin de semana" no es un día suelto: lo entiende el de ventanas,
        # y se contesta por el primero (el sábado)
        ventana = memoria.interpretar_ventana(texto, ahora)
        cuando = ventana[0] if ventana else None
    if not cuando:
        # Dijo un momento y no se entiende como día: no vale contestar por
        # hoy, que sería contestar a otra cosa ("el mes que viene")
        return None, None
    dias = (cuando.date() - ahora.date()).days
    if dias < 0 or dias >= DIAS:
        return None, None
    if dias == 0:
        return 0, "hoy"
    if dias == 1:
        return 1, "mañana"
    return dias, f"el {memoria.DIAS_ES[cuando.weekday()]}"


def el_tiempo(lugar, cuando="", ahora=None):
    """La frase que dirá Jarvis, ya redactada. None si no se pudo."""
    ahora = ahora or datetime.now()
    sitio = donde_esta(lugar)
    if not sitio:
        return f"No encuentro un sitio que se llame {lugar}."
    nombre, lat, lon = sitio

    indice, dicho = _dia_pedido(cuando, ahora)
    if indice is None:
        return f"Solo tengo la previsión de los próximos {DIAS} días."

    try:
        d = _prevision(lat, lon)
    except Exception as e:
        print(f"[tiempo] sin datos de {nombre}: {e}")
        return f"No he podido consultar el tiempo en {nombre}."

    diario = (d or {}).get("daily") or {}
    try:
        maxima = diario["temperature_2m_max"][indice]
        minima = diario["temperature_2m_min"][indice]
        cielo = CIELO.get(diario["weather_code"][indice], "")
        lluvia = (diario.get("precipitation_probability_max") or [None] * DIAS)[indice]
    except (KeyError, IndexError):
        return f"No he podido consultar el tiempo en {nombre}."

    if indice == 0 and (d.get("current") or {}).get("temperature_2m") is not None:
        # Hoy se dice la temperatura de AHORA, que es lo que se pregunta
        c = d["current"]
        frase = f"En {nombre} hay {_grados(c['temperature_2m'])}"
        sensacion = c.get("apparent_temperature")
        if sensacion is not None and abs(sensacion - c["temperature_2m"]) >= 3:
            frase += f", aunque se notan {_grados(sensacion)}"
        cielo_ahora = CIELO.get(c.get("weather_code"), cielo)
        frase += f", {cielo_ahora}. Máxima de {round(maxima)} y mínima de {round(minima)}"
    else:
        frase = (f"{dicho.capitalize()} en {nombre}: {cielo}, "
                 f"entre {round(minima)} y {round(maxima)} grados")

    if lluvia is not None and lluvia >= 20:
        frase += f", con un {round(lluvia)} por ciento de probabilidad de lluvia"
    return frase + "."


def es_del_tiempo(frase):
    """¿Pregunta por el tiempo? "¿Qué tiempo hace?", "¿va a llover?"."""
    t = _sin_tildes(frase)
    if re.search(r"\bcuanto tiempo\b|\bcronometr|\btemporizador\b", t):
        return False           # "¿cuánto tiempo llevo?" no es meteorología
    # "previsi", "meteo" y "pronostic" van por delante y sin \b al final:
    # "previsión" no acaba ahí ("Dame la previsión de Vigo" no encajaba)
    if re.search(r"\b(?:previsi|meteo|pronostic)", t):
        return True
    return bool(re.search(
        r"\b(que tiempo|el tiempo|tiempo hace|va a llover|llovera|esta lloviendo|"
        r"temperatura|grados|hace frio|hace calor|previsi|meteo|pronostico|"
        r"hara (?:frio|calor|sol)|nevara|va a nevar)\b", t))


def lugar_en(frase):
    """El sitio que nombra la frase: "en Bilbao" -> "Bilbao". Se mira la
    frase ORIGINAL, con sus mayúsculas, que es lo que distingue un nombre
    propio ("en Vigo") de una palabra cualquiera ("en casa")."""
    m = re.search(r"\ben ([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.'-]*(?: de [a-zA-Z]+)?"
                  r"(?: [A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.'-]*)*)", frase)
    return m.group(1).strip(" ?¿!.,") if m else ""


def donde_vive():
    """La ciudad que el usuario haya contado alguna vez ("vivo en Bilbao")."""
    import memoria
    for dato in reversed(memoria.datos_conocidos()):
        m = re.search(r"\bvive en ([A-Za-zÁÉÍÓÚáéíóúÑñ .'-]{2,40})", dato)
        if m:
            return m.group(1).strip(" .")
    return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    lugar = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or donde_vive()
    if not lugar:
        print("Dime un sitio: python tiempo.py Bilbao")
        sys.exit(1)
    print(el_tiempo(lugar))
    print(el_tiempo(lugar, "mañana"))
