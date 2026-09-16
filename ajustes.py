"""Todo lo que se toca para cambiar el comportamiento, en un sitio.

Estaba repartido por la cabecera de servidor.py, que habia crecido hasta
las 2.500 lineas. Aqui se ve de un vistazo que modelo, que voz y con
cuanto silencio corta, sin bucear.

Tambien vive aqui el registro de los DLL de CUDA, porque tiene que pasar
ANTES de que nadie importe faster_whisper y este modulo lo importan todos.
"""

import glob
import os
import site
from pathlib import Path

AQUI = Path(__file__).parent


def _registrar_cuda():
    """Pone los DLL de CUDA al alcance de CTranslate2 (el motor de Whisper).

    pip instala cuBLAS y cuDNN dentro de site-packages/nvidia/*/bin, pero
    CTranslate2 los carga con LoadLibrary, que solo mira el PATH del proceso.
    Sin esto, el modelo se CONSTRUYE bien en CUDA y luego revienta al
    transcribir con "Library cublas64_12.dll is not found or cannot be loaded".
    Tiene que ejecutarse ANTES de importar faster_whisper.
    """
    dirs = []
    for base in site.getsitepackages():
        dirs += glob.glob(os.path.join(base, "nvidia", "*", "bin"))
    if not dirs:
        return
    os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ["PATH"]
    for d in dirs:
        os.add_dll_directory(d)


_registrar_cuda()


# ---------------------------------------------------------------
# MODELOS Y VOZ
# ---------------------------------------------------------------
# Modelo SIN razonamiento, a propósito. Qwen3 se probó y no sirve para voz:
# con think=False vuelca su razonamiento dentro del texto normal (se leería
# en voz alta), y con think=True lo separa bien pero tarda 32 s en decir la
# primera palabra. llama3.1:8b responde en 0,7 s y cabe de sobra en 8 GB.
MODELO_LLM = "llama3.1:8b"
MODELO_RAPIDO = "tiny"       # para la transcripción en vivo
MODELO_BUENO = "small"       # para la versión final
FRECUENCIA = 16000
MAX_TURNOS = 8

# Temperatura baja a propósito: a 0.8 el modelo se inventaba siglas y datos
# (se probó con "DAW" y dio cuatro respuestas distintas, todas falsas).
TEMPERATURA = 0.35
# 2048 se quedaba corto y la conversación se olvidaba enseguida. No subo más
# porque la VRAM va justa: Whisper y el modelo comparten los 8 GB.
NUM_CTX = 4096
MS_PARCIAL = 900             # cada cuánto refresca el texto en vivo

# Corte automático al dejar de hablar, para no pulsar espacio dos veces.
# Ponlo a False si prefieres controlarlo tú.
CORTE_POR_SILENCIO = True
SILENCIO_CORTE_S = 1.8       # cuánto silencio hace falta para dar por terminado
MIN_HABLA_S = 0.5            # menos que esto no cuenta como haber hablado
VAD_VENTANA_S = 6            # cuánto audio reciente se analiza cada vez

MAX_GRABACION_S = 30         # corte automático: nadie graba un audio eterno
VENTANA_PARCIAL_S = 15       # las parciales solo re-transcriben los últimos N s,
                              # si no, cada vuelta transcribe más audio que la anterior

NOMBRE = "Jarvis"

# Voz de Piper. Se probaron las dos "medium" de España sintetizando la misma
# frase: davefx tardó 1,98 s y sharvard 0,33 s. Seis veces más rápida, y con
# davefx habría dos segundos de silencio antes de empezar a hablar.
# Para cambiarla:
#   python -m piper.download_voices es_ES-davefx-medium --data-dir voces
VOZ_PIPER = "es_ES-sharvard-medium"
AQUI_VOCES = AQUI / "voces"
VELOCIDAD_VOZ = 1.0          # más de 1 va más lento, menos de 1 más rápido

PROMPT_SISTEMA = f"""Te llamas {NOMBRE}. Eres un asistente de voz en español,
hablando con alguien de España.

CÓMO HABLAS
Responde SIEMPRE en dos frases cortas como máximo.
Natural y coloquial, como quien contesta de viva voz, no como quien escribe.
NUNCA uses listas, markdown, asteriscos, emojis ni símbolos:
tu respuesta se va a leer en voz alta.
No digas "claro" ni "por supuesto", y no ofrezcas más ayuda al final.
No repitas la pregunta antes de contestarla: ve directo.
No te presentes ni digas tu nombre salvo que te lo pregunten.

QUÉ NO HACER
Es MEJOR decir "no lo sé" que inventarse una respuesta.
Nunca te inventes datos, siglas, fechas ni nombres: si no estás seguro, dilo.
Si unas siglas o una palabra pueden significar varias cosas, pregunta a cuál
se refiere en vez de elegir una al azar.
Nunca digas que no puedes ayudar por no tener una función o herramienta:
eres un asistente con conocimientos generales y puedes charlar de lo que sea.
Tampoco digas "no puedo ayudarte con eso" ni "no tengo acceso a internet":
SÍ tienes, y si hacía falta buscar ya se ha buscado antes de llegar aquí.
Si te piden una opinión o un consejo, dalo. Si te preguntan algo que no
sabes del todo, cuenta lo que sí sepas. Siempre contestas a algo.

CIFRAS Y HORAS
Los números se leen bien tal cual, así que puedes escribir "las 8 y media".
Lo que NO debes usar son barras ni abreviaturas: escribe "el 3 de septiembre",
no "3/9", y "a las 17 horas", no "17h". Esos se leerían letra por letra."""
