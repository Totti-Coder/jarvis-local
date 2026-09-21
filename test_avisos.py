"""Avisos a la hora: Jarvis te recuerda las cosas sin que preguntes.

Dos partes:

  - la agenda (memoria.py): que toca avisar, cuando, y que no se repita.
    Base de datos temporal y reloj fijo: da lo mismo hoy que en un mes.

  - el vigilante (servidor.py): que solo hable cuando esta libre, que no
    pise una pregunta en el aire, y que con dos pestanas abiertas lo diga
    una sola vez. Sin audio ni red: la voz y el socket son de mentira.

Uso:  python test_avisos.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_avisos.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

AHORA = datetime(2026, 9, 8, 16, 52)      # martes, 16:52
fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


def tarea(texto, cuando, tiene_hora=1, hecha=0):
    with memoria._conectar() as con:
        cur = con.execute(
            "INSERT INTO tareas (texto, cuando_texto, cuando_iso, tiene_hora, "
            "hecha, creada) VALUES (?, '', ?, ?, ?, ?)",
            (texto, cuando.isoformat() if cuando else None, tiene_hora, hecha,
             AHORA.isoformat()))
        return cur.lastrowid


def reiniciar():
    with memoria._conectar() as con:
        con.execute("DELETE FROM tareas")
        con.execute("DELETE FROM avisos")
    # Las tres, no solo la lectura: importar el servidor engancha tu Google
    # de verdad, y un test no puede escribir en tu calendario
    memoria.fuente_externa = memoria.crear_externo = memoria.borrar_externo = None
    memoria._cache_fuera.update(cuando=None, hasta=None, eventos=[])


print("=" * 70)
print("QUE TOCA AVISAR")
print("=" * 70)
reiniciar()
tarea("dentista", AHORA + timedelta(minutes=8))           # 17:00: dentro
tarea("reunion", AHORA + timedelta(minutes=40))           # 17:32: aun no
tarea("llamar a Ana", AHORA - timedelta(minutes=1))       # 16:51: acaba de pasar
tarea("gimnasio", AHORA - timedelta(hours=3))             # 13:52: muy atras
tarea("comprar pan", AHORA, tiene_hora=0)                 # sin hora
tarea("ya hecho", AHORA + timedelta(minutes=5), hecha=1)  # completada

p = memoria.pendientes_de_aviso(AHORA)
textos = [t for _, t, _ in p]
comprueba("avisa de lo que empieza en los proximos 10 min", "dentista" in textos, textos)
comprueba("NO de lo que queda lejos", "reunion" not in textos, textos)
comprueba("SI de lo que acaba de empezar", "llamar a Ana" in textos, textos)
comprueba("NO de lo que paso hace horas (eso es del saludo)", "gimnasio" not in textos, textos)
comprueba("NO de lo que no tiene hora", "comprar pan" not in textos, textos)
comprueba("NO de lo que ya esta hecho", "ya hecho" not in textos, textos)
comprueba("del mas proximo al mas lejano", textos == ["llamar a Ana", "dentista"], textos)

print("\n" + "=" * 70)
print("QUE NO SE REPITA")
print("=" * 70)
clave = p[1][0]
comprueba("el primero en marcarlo se lo queda", memoria.marcar_avisado(clave, AHORA))
comprueba("el segundo no (dos pestanas: solo una lo dice)",
          not memoria.marcar_avisado(clave, AHORA))
textos = [t for _, t, _ in memoria.pendientes_de_aviso(AHORA + timedelta(seconds=20))]
comprueba("una vez dado, ya no sale", "dentista" not in textos, textos)

# Cambiar la tarea de hora es otro aviso distinto
with memoria._conectar() as con:
    nuevo = (AHORA + timedelta(minutes=9)).isoformat()
    con.execute("UPDATE tareas SET cuando_iso = ? WHERE texto = 'dentista'", (nuevo,))
textos = [t for _, t, _ in memoria.pendientes_de_aviso(AHORA)]
comprueba("si se cambia de hora, se vuelve a avisar", "dentista" in textos, textos)

print("\n" + "=" * 70)
print("TAMBIEN LO QUE SOLO ESTA EN GOOGLE")
print("=" * 70)
reiniciar()
llamadas = []


def calendario_falso(desde, hasta):
    llamadas.append(desde)
    return [
        {"texto": "cita medica", "cuando_iso": (AHORA + timedelta(minutes=5)).isoformat(),
         "tiene_hora": True, "id": "g1", "marcado": False},
        {"texto": "festivo", "cuando_iso": AHORA.replace(hour=0, minute=0).isoformat(),
         "tiene_hora": False, "id": "g2", "marcado": False},
    ]


memoria.fuente_externa = calendario_falso
textos = [t for _, t, _ in memoria.pendientes_de_aviso(AHORA)]
comprueba("avisa de un evento que solo esta en Google", "cita medica" in textos, textos)
comprueba("pero no de uno de dia completo", "festivo" not in textos, textos)

for s in range(0, 200, 20):                  # diez revisiones seguidas
    memoria.pendientes_de_aviso(AHORA + timedelta(seconds=s))
comprueba("Google no se consulta en cada revision (cache)",
          len(llamadas) == 1, f"{len(llamadas)} llamadas")
memoria.pendientes_de_aviso(AHORA + timedelta(minutes=6))
comprueba("pero si al pasar el rato de refresco", len(llamadas) == 2,
          f"{len(llamadas)} llamadas")

print("\n" + "=" * 70)
print("COMO SE DICE")
print("=" * 70)
m = datetime(2026, 9, 8, 17, 0)
for minutos, esperado in [
    (10, "Te recuerdo: dentista a las 5 de la tarde, dentro de 10 minutos."),
    (1, "Te recuerdo: dentista a las 5 de la tarde, dentro de un minuto."),
    (0, "Es la hora: dentista."),
    (-1, "Es la hora: dentista."),
]:
    r = memoria.frase_de_aviso("dentista", m, m - timedelta(minutes=minutos))
    comprueba(f"a {minutos:>2} minutos", r == esperado, r)


# ---------------------------------------------------------------
# EL VIGILANTE, DENTRO DEL SERVIDOR
# ---------------------------------------------------------------
print("\n" + "=" * 70)
print("EL VIGILANTE")
print("=" * 70)

import servidor  # noqa: E402  (carga Whisper: va despues de lo rapido)

memoria.fuente_externa = memoria.crear_externo = memoria.borrar_externo = None

servidor.AVISO_CADA_S = 0.01
servidor.hablar = lambda t: None


class SocketFalso:
    def __init__(self):
        self.mensajes = []

    async def send_text(self, texto):
        self.mensajes.append(json.loads(texto))

    async def send_bytes(self, datos):
        pass


def dichos(sock):
    return [m["texto"] for m in sock.mensajes if m.get("tipo") == "dicho"]


async def vigilar_un_rato(*convs, segundos=0.15):
    tareas = [asyncio.create_task(c.vigilar_avisos()) for c in convs]
    await asyncio.sleep(segundos)
    for t in tareas:
        t.cancel()
    await asyncio.gather(*tareas, return_exceptions=True)


def con_un_aviso():
    reiniciar()
    tarea("dentista", datetime.now() + timedelta(minutes=5))


async def escenarios():
    con_un_aviso()
    s = SocketFalso()
    c = servidor.Conversacion(s)
    await vigilar_un_rato(c)
    comprueba("libre: avisa", len(dichos(s)) == 1 and "dentista" in dichos(s)[0], dichos(s))
    comprueba("y vuelve a reposo al acabar", c.estado == "inactivo", c.estado)

    con_un_aviso()
    s = SocketFalso()
    c = servidor.Conversacion(s)
    c.grabando = True
    await vigilar_un_rato(c)
    comprueba("grabando: se calla", dichos(s) == [], dichos(s))

    c.grabando = False
    c.estado = "pensando"
    await vigilar_un_rato(c)
    comprueba("pensando: se calla", dichos(s) == [], dichos(s))

    c.estado = "inactivo"
    c.pendiente = {"tipo": "sistema", "datos": "apagar"}
    await vigilar_un_rato(c)
    comprueba("con una pregunta en el aire: se calla", dichos(s) == [], dichos(s))

    c.pendiente = {"tipo": None, "datos": None}
    await vigilar_un_rato(c)
    comprueba("el aviso esperaba: sale al quedar libre", len(dichos(s)) == 1, dichos(s))

    con_un_aviso()
    s1, s2 = SocketFalso(), SocketFalso()
    await vigilar_un_rato(servidor.Conversacion(s1), servidor.Conversacion(s2))
    total = len(dichos(s1)) + len(dichos(s2))
    comprueba("dos pestanas abiertas: lo dice una sola", total == 1,
              f"{dichos(s1)} / {dichos(s2)}")

    # La carrera: se pulsa el micro mientras dice el aviso
    con_un_aviso()
    s = SocketFalso()
    c = servidor.Conversacion(s)

    def hablar_y_que_pulsen(texto):
        c.grabando = True          # el bucle de comandos empezo a grabar
        c.estado = "escuchando"

    servidor.hablar = hablar_y_que_pulsen
    await vigilar_un_rato(c)
    servidor.hablar = lambda t: None
    estados = [m["valor"] for m in s.mensajes if m.get("tipo") == "estado"]
    comprueba("si pulsas mientras avisa, NO vuelve a reposo",
              c.estado == "escuchando" and estados[-1:] == ["hablando"], estados)

    # ---- EL TEMPORIZADOR: que suene de verdad, y cuándo no ----
    def acabados(sock):
        return [d for d in dichos(sock) if "Se acabó" in d]

    def barra(sock):
        return [m for m in sock.mensajes if m.get("tipo") == "temporizador"]

    s = SocketFalso()
    c = servidor.Conversacion(s)
    await c.poner_temporizador(0.05)
    comprueba("temporizador: la barra lo enseña al ponerlo",
              barra(s)[-1]["queda"] is not None, barra(s))
    await asyncio.sleep(0.25)
    comprueba("temporizador: suena al acabar", len(acabados(s)) == 1, dichos(s))
    comprueba("temporizador: y la barra se apaga", barra(s)[-1]["queda"] is None, barra(s)[-1])

    s = SocketFalso()
    c = servidor.Conversacion(s)
    c.grabando = True
    await c.poner_temporizador(0.05)
    await asyncio.sleep(0.25)
    comprueba("temporizador: mientras hablas, espera", acabados(s) == [], dichos(s))
    c.grabando = False
    await asyncio.sleep(0.5)
    comprueba("temporizador: y suena al quedar libre", len(acabados(s)) == 1, dichos(s))

    s = SocketFalso()
    c = servidor.Conversacion(s)
    await c.poner_temporizador(0.1)
    await c._usar_temporizador("cancelar", None, "cancela el temporizador")
    await asyncio.sleep(0.3)
    comprueba("temporizador: cancelado, no suena", acabados(s) == [], dichos(s))

    s = SocketFalso()
    c = servidor.Conversacion(s)
    await c.poner_temporizador(0.1)
    await c.poner_temporizador(0.4)
    await asyncio.sleep(0.25)
    comprueba("temporizador: poner otro sustituye al primero (que no suena)",
              acabados(s) == [], dichos(s))
    await asyncio.sleep(0.4)
    comprueba("temporizador: suena solo el segundo", len(acabados(s)) == 1, dichos(s))


asyncio.run(escenarios())

reiniciar()
os.remove(memoria.BASE)

print("\n" + "=" * 70)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 70)
sys.exit(1 if fallos else 0)
