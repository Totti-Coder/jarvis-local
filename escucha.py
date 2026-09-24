"""Oir: cargar Whisper y transcribir.

Dos modelos a la vez: `tiny` para el texto en vivo mientras hablas y
`small` para la pasada buena al soltar. Comparten un candado porque
comparten GPU.
"""

import sys
import threading
import time
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions, get_speech_timestamps

import filtro_voz
from ajustes import FRECUENCIA, MODELO_BUENO, MODELO_RAPIDO

AQUI = Path(__file__).parent


def cargar_whisper(nombre):
    """Carga el modelo en GPU si de verdad funciona, y si no, en CPU.

    Construir el modelo en CUDA puede salir bien y aun así fallar luego al
    transcribir (los DLL de cuBLAS/cuDNN se cargan en ese momento, no antes).
    Por eso aquí se hace una transcripción de prueba con medio segundo de
    silencio: si el fallo existe, aparece ahora y no en mitad de una frase.
    """
    try:
        modelo = WhisperModel(nombre, device="cuda", compute_type="float16")
        list(modelo.transcribe(np.zeros(8000, dtype=np.float32),
                               language="es", beam_size=1)[0])
        print(f"  {nombre}: GPU (cuda)")
        return modelo
    except Exception as e:
        print(f"  {nombre}: CPU, no se pudo usar GPU -> {type(e).__name__}: {e}")
        return WhisperModel(nombre, device="cpu", compute_type="int8")


# Se crean una vez: el detector se consulta cuatro veces por segundo.
OPCIONES_VAD = VadOptions(threshold=0.5, min_silence_duration_ms=300,
                          speech_pad_ms=100)

print("Cargando modelos de voz...")
stt_rapido = cargar_whisper(MODELO_RAPIDO)
stt_bueno = cargar_whisper(MODELO_BUENO)


# Candado único para Whisper. Los dos modelos comparten la misma GPU, y
# cancelar la tarea de parciales NO detiene el hilo que ya está transcribiendo.
# Sin este candado, el modelo rápido y el bueno pueden acabar solapados en
# CUDA, que es donde se quedaba colgado.
_candado_whisper = threading.Lock()


# Silencio que se pega delante y detrás de lo que se manda a Whisper.
#
# Whisper entiende peor la primera palabra cuando el audio empieza de
# golpe, sin nada de sala delante. Se nota sobre todo desde el navegador:
# manda muestras en el mismo instante en que abre el micro, así que la
# primera sílaba cae en la muestra cero. Con el micro del PC hay algo de
# margen porque el stream ya estaba abierto.
#
# Cuidado con lo que promete esto: MEJORA, no arregla. Medido sobre voz
# sintética, que es el caso peor, acierta la primera palabra en 1 de 4
# frases sin colchón y en 3 de 4 con 0,3 s. Pero 0,5 s vuelve a bajar a
# 2 de 4, así que no hay un óptimo real: es ayudar al modelo, no
# corregirlo. Se deja porque el coste es cero y nunca empeora.
COLCHON_S = 0.3


# Con --audio-debug, cada turno se guarda en _audio/ tal y como llegó.
# Es la única forma de dejar de suponer por qué se entiende mal desde el
# móvil: se escucha y se mira el nivel, en vez de adivinar.
GUARDAR_AUDIO = "--audio-debug" in sys.argv
_DIR_AUDIO = Path(__file__).parent / "_audio"


def guardar_audio(audio, etiqueta="turno"):
    """Escribe un WAV de 16 kHz con lo que se va a transcribir."""
    if not GUARDAR_AUDIO or audio is None or not len(audio):
        return
    import wave
    try:
        _DIR_AUDIO.mkdir(exist_ok=True)
        nombre = _DIR_AUDIO / f"{time.strftime('%H%M%S')}_{etiqueta}.wav"
        with wave.open(str(nombre), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(FRECUENCIA)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        pico = float(np.abs(audio).max())
        rms = float(np.sqrt(np.mean(audio ** 2)))
        print(f"[audio] {nombre.name}  {len(audio)/FRECUENCIA:.1f}s  "
              f"pico {pico:.2f}  rms {rms:.3f}")
    except Exception as e:
        print(f"[audio] no se pudo guardar: {e}")


def transcribir(audio, modelo, etiqueta=""):
    if etiqueta == "final":
        guardar_audio(audio)
    if audio is not None and len(audio):
        silencio = np.zeros(int(COLCHON_S * FRECUENCIA), dtype=np.float32)
        audio = np.concatenate([silencio, np.asarray(audio, dtype=np.float32),
                                silencio])
    t0 = time.monotonic()
    with _candado_whisper:
        espera = time.monotonic() - t0
        # beam_size 5 en la pasada buena: cuesta 0,04 s más y aguanta mejor
        # el audio con ruido. Las parciales van con 1, que es de usar y tirar.
        haz = 1 if modelo is stt_rapido else 5
        segmentos, _ = modelo.transcribe(audio, language="es", beam_size=haz)
        trozos = list(segmentos)
        texto = " ".join(s.text for s in trozos).strip()
        # La MENOR de todas: basta con que un trozo tenga voz de verdad
        sin_habla = min((s.no_speech_prob for s in trozos), default=None)
    total = time.monotonic() - t0

    # Whisper no calla ante el silencio: se inventa una frase de subtítulos.
    # Si cae en una palabra que es orden ("para", "sigue"), Jarvis actuaría
    # sin que nadie haya hablado. Ver filtro_voz.py
    tirar, motivo = filtro_voz.parece_alucinacion(texto, sin_habla)
    if tirar and texto:
        print(f"[{etiqueta or 'stt'}] descartado, {motivo}: {texto[:50]!r}")
    if tirar:
        return ""

    if etiqueta:
        print(f"[{etiqueta}] {total:.1f}s (espera {espera:.1f}s) "
              f"{len(audio)/FRECUENCIA:.1f}s de audio -> {texto[:60]!r}"
              + (f" (sin voz {sin_habla:.2f})" if sin_habla is not None else ""))
    return texto
