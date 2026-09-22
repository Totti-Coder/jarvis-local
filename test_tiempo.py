"""El tiempo (ver tiempo.py): qué se pide, qué se dice y qué NO se consulta.

Sin red: `tiempo.traer` se sustituye por respuestas de mentira con la forma
exacta que devuelve Open-Meteo. Así el test vale igual llueva o truene, y
corre en el CI.

Uso:  python test_tiempo.py
"""

import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

import tiempo

fallos = []
llamadas = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


# La forma real de las dos respuestas de Open-Meteo, recortadas
GEO = {"results": [{"name": "Bilbao", "latitude": 43.26, "longitude": -2.93,
                    "country": "España"}]}
PARTE = {
    "current": {"temperature_2m": 21.4, "apparent_temperature": 22.0,
                "weather_code": 0, "wind_speed_10m": 6.2},
    "daily": {
        "weather_code": [0, 3, 61, 95, 2, 2, 71],
        "temperature_2m_max": [27.1, 26.4, 19.8, 21.0, 24.0, 25.0, 3.4],
        "temperature_2m_min": [17.2, 16.1, 14.0, 15.5, 16.0, 17.0, -1.2],
        "precipitation_probability_max": [0, 10, 80, 95, 5, 5, 60],
    },
}


def red_falsa(url):
    llamadas.append(url)
    return GEO if "geocoding" in url else PARTE


tiempo.traer = red_falsa
AHORA = datetime(2026, 9, 22, 13, 0)      # martes

print("=" * 72)
print("CÓMO SE DICE EL TIEMPO")
print("=" * 72)
for cuando, esperado in [
    ("", "En Bilbao hay 21 grados, cielo despejado. Máxima de 27 y mínima de 17."),
    ("hoy", "En Bilbao hay 21 grados, cielo despejado. Máxima de 27 y mínima de 17."),
    ("mañana", "Mañana en Bilbao: nublado, entre 16 y 26 grados."),
    # con probabilidad alta, se dice; por debajo del 20 %, no
    ("pasado mañana", "El jueves en Bilbao: con lluvia floja, entre 14 y 20 grados, "
                      "con un 80 por ciento de probabilidad de lluvia."),
    ("el viernes", "El viernes en Bilbao: con tormenta, entre 16 y 21 grados, "
                   "con un 95 por ciento de probabilidad de lluvia."),
    ("el lunes", "El lunes en Bilbao: con nieve floja, entre -1 y 3 grados, "
                 "con un 60 por ciento de probabilidad de lluvia."),
]:
    r = tiempo.el_tiempo("Bilbao", cuando, AHORA)
    comprueba(f"{cuando or '(ahora)':<14} -> {r}", r == esperado, f"esperaba {esperado}")

# "El fin de semana" no es un día suelto: lo resuelve el intérprete de ventanas
r = tiempo.el_tiempo("Bilbao", "el fin de semana", AHORA)
comprueba(f"el fin de semana -> {r}", r.startswith("El viernes en Bilbao"), r)

print("\n  lo que no se puede contestar:")
r = tiempo.el_tiempo("Bilbao", "el mes que viene", AHORA)
comprueba(f"más allá de la previsión -> {r}", "próximos 7 días" in r, r)
tiempo._cache.clear()
tiempo.traer = lambda url: {"results": []}
r = tiempo.el_tiempo("Chiquitistán", "", AHORA)
comprueba(f"un sitio que no existe -> {r}", r == "No encuentro un sitio que se llame Chiquitistán.", r)


def revienta(url):
    raise OSError("sin conexión")


tiempo._cache.clear()
tiempo.traer = revienta
r = tiempo.el_tiempo("Bilbao", "", AHORA)
comprueba(f"sin internet, lo dice y no revienta -> {r}", "No encuentro" in r or "No he podido" in r, r)

print("\n" + "=" * 72)
print("NO SE PREGUNTA DOS VECES LO MISMO (CACHÉ)")
print("=" * 72)
tiempo._cache.clear()
tiempo.traer = red_falsa
llamadas.clear()
for _ in range(4):
    tiempo.el_tiempo("Bilbao", "", AHORA)
comprueba("cuatro preguntas seguidas, dos llamadas (sitio y parte)",
          len(llamadas) == 2, llamadas)
comprueba("y la temperatura sale en grados enteros, sin decimales",
          "21,4" not in tiempo.el_tiempo("Bilbao", "", AHORA))

print("\n" + "=" * 72)
print("¿ES UNA PREGUNTA DEL TIEMPO?")
print("=" * 72)
for frase, esperado in [
    ("¿Qué tiempo hace?", True),
    ("¿Qué tiempo hace en Madrid?", True),
    ("¿Va a llover mañana?", True),
    ("¿Hace frío en Bilbao?", True),
    ("¿Qué temperatura hay?", True),
    ("¿Cuántos grados hace?", True),
    ("¿Nevará el fin de semana?", True),
    ("Dame la previsión de Vigo", True),
    # "tiempo" también es lo que mide el cronómetro: eso NO es meteorología
    ("¿Cuánto tiempo llevo?", False),
    ("¿Cuánto tiempo lleva el cronómetro?", False),
    ("¿Cuánto le queda al temporizador?", False),
    ("¿Qué tal el día?", False),
    ("Cuéntame un chiste", False),
]:
    r = tiempo.es_del_tiempo(frase)
    comprueba(f"{frase!r:<42} -> {r}", r == esperado, f"esperaba {esperado}")

print("\n  el sitio que nombra la frase:")
for frase, esperado in [
    ("¿Qué tiempo hace en Madrid?", "Madrid"),
    ("¿Va a llover en Cabo de Gata?", "Cabo de Gata"),
    ("¿Hace frío en San Sebastián?", "San Sebastián"),
    ("¿Qué tiempo hace?", ""),
    # en minúscula no es un sitio: es una palabra cualquiera
    ("¿Hace frío en casa?", ""),
    ("¿Llueve en la calle?", ""),
]:
    r = tiempo.lugar_en(frase)
    comprueba(f"{frase!r:<38} -> {r!r}", r == esperado, f"esperaba {esperado!r}")

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
