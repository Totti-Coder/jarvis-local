"""El turno completo: que mensajes manda Jarvis ante cada tipo de frase.

PARA QUE SIRVE ESTO

responder() decide todo lo que pasa en un turno: si hay algo pendiente de
confirmar, si toca buscar en la web, leer el correo, preparar un borrador,
ejecutar una herramienta o simplemente conversar. Es la pieza con mas
caminos del proyecto.

Este fichero fija el comportamiento: para cada tipo de frase, la secuencia
EXACTA de mensajes que salen hacia el navegador. Si alguien reordena el
codigo y cambia un camino sin querer, aqui se nota.

Se escribio antes de partir responder() en metodos, justo para poder
comparar el antes y el despues y demostrar que no habia cambiado nada.

COMO ESTA MONTADO

Nada de LLM, ni audio, ni red: el router, la voz y las herramientas se
sustituyen por dobles que devuelven siempre lo mismo. Lo que se comprueba
no es lo que dice el modelo —eso ya lo mide eval_router.py— sino por donde
pasa el codigo y en que orden habla.

Uso:
    python test_conversacion.py            compara con lo fijado
    python test_conversacion.py --fijar    guarda el comportamiento actual
"""

import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import servidor

AQUI = Path(__file__).parent
GUARDADO = AQUI / "test_conversacion.json"


# ---------------------------------------------------------------
# DOBLES: nada de LLM, ni audio, ni red
# ---------------------------------------------------------------

class SocketFalso:
    """Apunta lo que se le manda en vez de enviarlo a ningun sitio."""

    def __init__(self):
        self.mensajes = []

    async def send_text(self, texto):
        d = json.loads(texto)
        # El nivel del microfono son decenas de mensajes por segundo con
        # numeros que cambian solos: no dicen nada del camino seguido.
        if d.get("tipo") == "nivel":
            return
        self.mensajes.append(d)

    async def send_bytes(self, datos):
        self.mensajes.append({"tipo": "binario", "bytes": len(datos)})

    async def accept(self):
        pass


def resumir(mensajes):
    """Deja de cada mensaje lo que importa: el tipo y su contenido."""
    salida = []
    for m in mensajes:
        t = m.get("tipo")
        if t == "token":
            salida.append(("token", m.get("texto", "")))
        elif t == "estado":
            salida.append(("estado", m.get("valor")))
        elif t == "herramienta":
            salida.append(("herramienta", m.get("nombre")))
        elif t == "borrador":
            salida.append(("borrador", m.get("modo"), m.get("destinatario"),
                           m.get("asunto"), (m.get("mensaje") or "")[:40]))
        else:
            salida.append((t, str(m.get("texto") or m.get("valor") or "")[:60]))
    return salida


# ---------------------------------------------------------------
# ESCENARIOS
# ---------------------------------------------------------------
# Cada uno: nombre, lo que el usuario dice, que devuelve el router, y
# opcionalmente el estado "pendiente" con el que arranca el turno.

ESCENARIOS = [
    ("charla",              "Cuéntame un chiste",           (None, None), None),
    ("herramienta",         "¿Qué tengo hoy?",              ("listar_tareas", {"cuando": "hoy"}), None),
    ("herramienta_sin_dato", "¿Qué tengo hoy?",             ("listar_tareas", {"cuando": "hoy"}), None),
    ("web_ok",              "¿Qué tiempo hace en Madrid?",  ("buscar_en_web", {"consulta": "tiempo Madrid"}), None),
    ("web_falla",           "¿Qué tiempo hace en Madrid?",  ("buscar_en_web", {"consulta": "tiempo Madrid"}), None),
    ("correos_hay",         "¿Tengo correos nuevos?",       ("leer_correos", {"solo_nuevos": True}), None),
    ("correos_vacio",       "¿Tengo correos nuevos?",       ("leer_correos", {"solo_nuevos": True}), None),
    ("correos_falla",       "¿Tengo correos nuevos?",       ("leer_correos", {"solo_nuevos": True}), None),
    ("apagar_pregunta",     "Apaga el ordenador",           ("control_sistema", {"accion": "apagar"}), None),
    ("apagar_si",           "sí",                           (None, None), ("sistema", "apagar")),
    ("apagar_no",           "no, déjalo",                   (None, None), ("sistema", "apagar")),
    ("correo_entero",       "Dile a Ana que llego tarde",   ("enviar_correo", {"destinatario": "Ana", "asunto": "Tarde", "mensaje": "Llego tarde"}), None),
    ("correo_sin_quien",    "Quiero mandar un correo",      ("enviar_correo", {}), None),
    ("correo_paso_asunto",  "La reunión del jueves",        (None, None), ("correo_paso", "asunto")),
    ("correo_abandona",     "déjalo",                       (None, None), ("correo_paso", "asunto")),
    ("correo_confirma_no",  "no",                           (None, None), ("correo", None)),
]


async def un_turno(escenario):
    nombre, frase, ruta, pendiente = escenario
    sock = SocketFalso()
    conv = servidor.Conversacion(sock)
    conv.bucle_principal = asyncio.get_running_loop()

    # --- dobles, distintos segun el escenario ---
    servidor.enrutar = lambda p: ruta
    servidor.hablar = lambda t: None
    servidor.ejecutar = lambda n, a: (
        None if nombre == "herramienta_sin_dato" else "Hoy tienes: dentista a las 5.")

    if nombre == "web_falla":
        servidor.buscar.buscar_y_leer = lambda c: ([], "sin conexión")
    else:
        servidor.buscar.buscar_y_leer = lambda c: (
            [{"titulo": "El tiempo", "texto": "Sol en Madrid."}], None)

    if nombre == "correos_vacio":
        servidor.correo.leer_nuevos = lambda n, q: ([], None)
    elif nombre == "correos_falla":
        servidor.correo.leer_nuevos = lambda n, q: ([], "sin permiso")
    else:
        servidor.correo.leer_nuevos = lambda n, q: (
            [{"de": "Ana", "asunto": "Cena", "cuando": "hoy", "texto": "Ven"}], None)

    servidor.correo.cargar_contactos = lambda: [
        {"nombre": "Ana", "email": "ana@ejemplo.com", "alias": []}]
    servidor.correo.buscar_contacto = lambda n: (
        {"nombre": "Ana", "email": "ana@ejemplo.com", "alias": []}
        if n and "ana" in n.lower() else None)
    servidor.correo.enviar = lambda d, a, c: (True, "Enviado.")
    servidor.redactar_correo = lambda e, d="", a="": "Hola Ana, llego tarde."
    servidor.sistema.ejecutar_accion = lambda a: "Apagando."

    # El conversador: se devuelve un chorro fijo, troceado como el de verdad
    def chat_falso(**k):
        if k.get("stream"):
            for t in ["Claro. ", "Aquí va. ", "Fin."]:
                yield {"message": {"content": t}}
        else:
            return {"message": {"content": "respuesta"}}
    servidor.ollama.chat = chat_falso

    if pendiente:
        tipo, datos = pendiente
        if tipo == "sistema":
            conv.pendiente = {"tipo": "sistema", "datos": datos}
        elif tipo == "correo_paso":
            conv.pendiente = {"tipo": "correo_paso", "datos": {
                "email": "ana@ejemplo.com", "nombre": "Ana",
                "asunto": "", "mensaje": "", "paso": datos}}
        elif tipo == "correo":
            conv.pendiente = {"tipo": "correo", "datos": {
                "email": "ana@ejemplo.com", "nombre": "Ana",
                "asunto": "Cena", "mensaje": "Llego tarde"}}

    await conv.responder(frase)
    return nombre, resumir(sock.mensajes)


async def todos():
    return [await un_turno(e) for e in ESCENARIOS]


if __name__ == "__main__":
    actual = {n: v for n, v in asyncio.run(todos())}

    if "--fijar" in sys.argv:
        GUARDADO.write_text(json.dumps(actual, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"Comportamiento fijado: {len(actual)} escenarios -> {GUARDADO.name}")
        sys.exit(0)

    if not GUARDADO.exists():
        print(f"No hay {GUARDADO.name}. Créalo con --fijar.")
        sys.exit(1)

    esperado = json.loads(GUARDADO.read_text(encoding="utf-8"))
    fallos = []
    print("=" * 70)
    print("EL TURNO COMPLETO, CAMINO POR CAMINO")
    print("=" * 70)
    for nombre in sorted(set(esperado) | set(actual)):
        a = [tuple(x) for x in actual.get(nombre, [])]
        e = [tuple(x) for x in esperado.get(nombre, [])]
        ok = a == e
        print(f"  {'OK ' if ok else 'MAL'} {nombre}")
        if not ok:
            fallos.append(nombre)
            print(f"        antes:  {e}")
            print(f"        ahora:  {a}")

    print("\n" + "=" * 70)
    print(f"  {'TODO IGUAL QUE ANTES' if not fallos else str(len(fallos)) + ' CAMINOS CAMBIADOS'}")
    print("=" * 70)
    sys.exit(1 if fallos else 0)
