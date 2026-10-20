"""Servidor de demostración para grabar el GIF del README.

Es el servidor DE VERDAD: el mismo router, las mismas herramientas, la
misma interfaz. Lo único que cambia:

  - base de datos aparte, con tareas de ejemplo (nada personal sale ahí)
  - sin voz por los altavoces, que en un GIF no se oye y molesta
  - una rutina de demostración que no toca programas de verdad
  - en cuanto se abre la página, va diciendo las frases solo

Las frases se inyectan como si se hubieran dicho por el micrófono: eso es
lo único simulado, porque aquí no hay micrófono.
"""
import asyncio
import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

# El proyecto esta un nivel por encima de esta carpeta
CARPETA = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, CARPETA)
os.chdir(CARPETA)
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import memoria

TMP = Path(sys.argv[1]).parent
memoria.BASE = Path(sys.argv[1])
if memoria.BASE.exists():
    memoria.BASE.unlink()

import ajustes  # noqa: E402
import servidor  # noqa: E402
import uvicorn  # noqa: E402

# Sin voz: un GIF no suena, y el servidor iría más lento esperando a Piper
servidor.hablar = lambda t: None


class MicroMudo:
    """Sin micrófono: lo que se grabe aquí acabaría en un GIF público, y
    la primera prueba transcribió lo que se hablaba en la habitación."""

    def empezar(self): pass
    def parar(self): return None
    def leer(self): return None
    def nivel(self): return 0.0
    def alimentar(self, datos): pass


servidor.Microfono = MicroMudo
# Y el del navegador también: en la primera prueba, el micro del navegador
# transcribió lo que se hablaba en la habitación
servidor.MicrofonoRemoto = MicroMudo
servidor.aviso_escritorio.mostrar = lambda t: True

# Sin Google: el permiso caduca a los 7 dias y, caducado, cada tarea se
# queda esperando a una peticion que va a fallar. La demo va con su base
# de datos y nada mas
memoria.fuente_externa = None
memoria.crear_externo = None
memoria.borrar_externo = None

# Agenda de ejemplo
ahora = datetime.now()
manana = (ahora + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
with memoria._conectar() as con:
    for texto, cuando in [("reunión con Ana", manana),
                          ("dentista", manana.replace(hour=17))]:
        con.execute("INSERT INTO tareas (texto, cuando_texto, cuando_iso, "
                    "tiene_hora, creada) VALUES (?, '', ?, 1, ?)",
                    (texto, cuando.isoformat(), ahora.isoformat()))

# Perfil, para que el tiempo no pregunte la ciudad
servidor.perfil.guardar("ciudad", "Ponferrada")
servidor.perfil.guardar("nombre", "Pablo")

# Una rutina de demostración: nada de cerrar programas de verdad
rutinas_demo = TMP / "atajos_demo.json"
rutinas_demo.write_text(json.dumps({"rutinas": [{
    "nombre": "modo trabajo",
    "pasos": [{"cronometro": "empezar"}, {"horas": "Acme"}, {"temporizador": "25"}],
    "dice": "Modo trabajo. Veinticinco minutos de foco."}]}), encoding="utf-8")
servidor.rutinas.FICHERO = rutinas_demo
servidor.PAUSA_PASO_S = 0.5          # que se vea paso a paso

GUION = [
    (2.0, "¿Qué tengo mañana?"),
    (3.0, "Apunta llamar al banco el jueves a las nueve"),
    (3.0, "Cámbialo a las diez"),
    (3.0, "Pon el modo trabajo"),
    (4.5, "¿Qué tiempo hace?"),
    (4.0, "¿Cuánto llevo con Acme?"),
]


async def despertar_modelo():
    """Una pregunta de mentira para que el modelo ya este cargado.

    La primera llamada tarda 8 segundos porque Ollama carga los pesos. En
    un GIF eso son 8 segundos mirando a PENSANDO.
    """
    try:
        await asyncio.to_thread(
            servidor.llm.chat, model=ajustes.MODELO_LLM,
            messages=[{"role": "user", "content": "di hola"}],
            options={"num_predict": 1})
        print("[demo] modelo caliente", flush=True)
    except Exception as e:
        print(f"[demo] no se pudo precalentar: {e}", flush=True)


async def actuar():
    """Espera a que se abra la página y va diciendo las frases."""
    await despertar_modelo()
    for _ in range(120):
        if servidor.CONEXIONES:
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(2.5)
    conv = next(iter(servidor.CONEXIONES))
    for espera, frase in GUION:
        await conv.enviar(tipo="usuario", texto=frase)
        print(f"[demo] {frase}", flush=True)
        await conv.responder(frase)
        await asyncio.sleep(espera)
    print("[demo] fin", flush=True)


@servidor.app.on_event("startup")
async def arrancar_demo():
    asyncio.create_task(actuar())


uvicorn.run(servidor.app, host="127.0.0.1", port=8016, log_level="warning")
