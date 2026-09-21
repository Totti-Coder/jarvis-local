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

# Base de datos propia, ANTES de importar el servidor (que la prepara al
# cargar). Antes estos escenarios leian tu agenda real: si apuntabas un
# "dentista" de verdad, el test cambiaba de resultado sin que nadie hubiera
# tocado el codigo.
import memoria  # noqa: E402

memoria.BASE = Path(__file__).parent / "_test_conversacion.db"
if memoria.BASE.exists():
    memoria.BASE.unlink()

import servidor  # noqa: E402

# Y el servidor, al importarse, engancha tu Google Calendar de verdad. Aqui
# se desengancha: un test no puede escribir en tu calendario. Ya paso, y
# dejo doscientos eventos de prueba en el calendario real.
memoria.fuente_externa = memoria.crear_externo = memoria.borrar_externo = None

# Las rutinas salen de un fichero de prueba, nunca de tu atajos.json: si
# tuvieras una rutina que se llamara como una frase de aqui, el test
# cambiaria de resultado sin que nadie hubiera tocado el codigo.
import tempfile  # noqa: E402

_RUTINAS = Path(tempfile.mkdtemp()) / "atajos.json"
_RUTINAS.write_text(json.dumps({"rutinas": [
    {"nombre": "modo trabajo",
     "pasos": [{"abrir": "vs code"}, {"cronometro": "empezar"}, {"horas": "Acme"}],
     "dice": "A por ello."}]}), encoding="utf-8")
servidor.rutinas.FICHERO = _RUTINAS

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
        elif t == "horas":
            salida.append(("horas", m.get("cliente")))
        elif t == "crono":
            salida.append(("crono", m.get("visible"), m.get("corriendo"),
                           m.get("segundos")))
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
    # No esta en la lista pero SI en Google Calendar: se pregunta antes
    ("calendario_pregunta", "Quita el dentista",            ("completar_tarea", {"texto": "el dentista"}), None),
    ("calendario_si",       "sí",                           (None, None), ("borrado_calendario", ["e1", "e2"])),
    ("calendario_no",       "no",                           (None, None), ("borrado_calendario", ["e1", "e2"])),
    # Ni en la lista ni en el calendario: contesta la herramienta de siempre
    ("no_esta_en_ningun_sitio", "Quita lo del gimnasio",    ("completar_tarea", {"texto": "gimnasio"}), None),
    # Borrar por FECHA. El reloj esta fijo en el 16 de septiembre de 2026
    ("fecha_pregunta",      "Quita lo del 1 de septiembre", ("completar_tarea", {"texto": "lo del 1 de septiembre"}), None),
    # "todo" no puede ganarle a la fecha: seria vaciar la agenda entera
    ("fecha_con_todo",      "Borra todo lo que tenía el 1 de septiembre", ("completar_tarea", {"texto": "todo lo que tenia"}), None),
    ("fecha_con_nombre",    "Quita el dentista del 1 de septiembre", ("completar_tarea", {"texto": "el dentista"}), None),
    ("fecha_vacia",         "Quita lo del 25 de septiembre", ("completar_tarea", {"texto": "lo del 25"}), None),
    ("fecha_si",            "sí",                           (None, None), ("borrado_dia", None)),
    ("fecha_no",            "no",                           (None, None), ("borrado_dia", None)),
    # El cronometro no pasa por el router: aunque este diga "abre un
    # programa", manda la orden fija. Y "para" suelto solo vale con el
    # panel abierto; sin el, sigue el camino de siempre.
    ("crono_abre",          "Abre el cronómetro",           ("abrir_programa", {"nombre": "cronometro"}), None),
    ("crono_panel_para",    "Para",                         (None, None), None),
    ("crono_sin_panel_para", "Para",                        (None, None), None),
    # Horas y rutinas: tampoco pasan por el router. Van en este orden y al
    # final, porque comparten la base de datos: la sesion que abre uno la
    # ve el siguiente.
    ("horas_sin_sesion_terminado", "He terminado",          (None, None), None),
    # Un cliente que no existe se confirma antes de crearlo
    ("horas_empieza",       "Empiezo con Acme",             ("abrir_programa", {"nombre": "acme"}), None),
    ("horas_cliente_no",    "no",                           (None, None), ("cliente_nuevo", "Informe")),
    ("horas_cliente_si",    "sí",                           (None, None), ("cliente_nuevo", "Acme")),
    # Y uno que suena igual que uno conocido es ese: Whisper oyo "Akme"
    ("horas_parecido",      "Empiezo con Akme",             (None, None), None),
    ("horas_consulta",      "¿Cuántas horas llevo este mes?", (None, None), None),
    ("rutina",              "Pon el modo trabajo",          (None, None), None),
    ("horas_termina",       "He terminado",                 ("completar_tarea", {"texto": "lo"}), None),
]

# El reloj de los escenarios. Sin fijarlo, "el 1 de septiembre" seria de
# 2026 o de 2027 segun el dia en que se ejecutara el test.
from datetime import datetime  # noqa: E402

AHORA_FIJO = datetime(2026, 9, 16, 17, 0)

# Calendario de mentira, con los duplicados que dejaron las pruebas
EVENTOS_FALSOS = [
    {"id": "e1", "summary": "dentista",
     "start": {"dateTime": "2026-09-01T09:00:00+02:00"}},
    {"id": "e2", "summary": "dentista",
     "start": {"dateTime": "2026-09-01T17:00:00+02:00"}},
    {"id": "e3", "summary": "comprar pan", "start": {"date": "2026-09-01"}},
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
    servidor.sistema.abrir_programa = lambda n: f"Abriendo {n}."
    servidor.sistema.cerrar_programa = lambda n: f"Cerrando {n}."

    # El calendario tampoco se toca de verdad
    servidor.calendario.conectar = lambda interactivo=False: True
    servidor.calendario.borrar_evento = lambda i: True
    servidor.calendario.listar_eventos = lambda d, h, maximo=250: (
        [] if nombre == "no_esta_en_ningun_sitio" else EVENTOS_FALSOS)
    conv.ahora = lambda: AHORA_FIJO
    if nombre == "crono_panel_para":
        # En marcha desde hace 75 segundos, con un reloj que no se mueve
        conv.crono = servidor.cronometro.Cronometro(reloj=lambda: 100.0)
        conv.crono.visible, conv.crono.desde = True, 25.0

    def agenda_falsa(desde, hasta):
        salida = []
        for ev in EVENTOS_FALSOS:
            t = servidor.calendario.como_tarea(ev)
            if desde <= datetime.fromisoformat(t["cuando_iso"]) <= hasta:
                salida.append(t)
        return salida

    servidor.calendario.eventos_para_agenda = agenda_falsa
    servidor.calendario.borrar_varios = lambda ids: len(ids)

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
        elif tipo == "borrado_calendario":
            conv.pendiente = {"tipo": "borrado_calendario", "datos": datos}
        elif tipo == "borrado_dia":
            conv.pendiente = {"tipo": "borrado_dia",
                              "datos": {"propias": [], "fuera": ["e1", "e2"]}}
        elif tipo == "cliente_nuevo":
            conv.pendiente = {"tipo": "cliente_nuevo", "datos": datos}
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
