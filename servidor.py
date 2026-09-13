"""
Servidor del asistente de voz.

Python captura el micro, transcribe, piensa y habla.
El navegador es solo la pantalla: se comunican por WebSocket.

Arrancar:  python servidor.py
Abrir:     http://localhost:8000
"""

import asyncio
import glob
import hashlib
import json
import os
import queue
import re
import site
import struct
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


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

import numpy as np
import sounddevice as sd
import ollama
import pyttsx3
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions, get_speech_timestamps

import buscar
import calendario
import correo
import memoria
import sistema

# ---------------------------------------------------------------
# CONFIGURACIÓN
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
AQUI_VOCES = Path(__file__).parent / "voces"
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


# ---------------------------------------------------------------
# MODELOS
# ---------------------------------------------------------------

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

memoria.preparar()

print("Cargando modelos de voz...")
stt_rapido = cargar_whisper(MODELO_RAPIDO)
stt_bueno = cargar_whisper(MODELO_BUENO)
print("Listo.")
print(f"  {calendario.estado()}")
print(f"  {correo.estado()}")


# ---------------------------------------------------------------
# MICRÓFONO
# ---------------------------------------------------------------

class Microfono:
    """Captura audio en segundo plano y mide el nivel de entrada."""

    def __init__(self):
        self.trozos = []
        self.nivel = 0.0
        self._stream = None
        self._lock = threading.Lock()
        self._inicio = 0.0

    def _callback(self, indata, frames, tiempo, status):
        with self._lock:
            self.trozos.append(indata.copy())
        # RMS escalado a un rango cómodo para la interfaz
        self.nivel = min(1.0, float(np.sqrt(np.mean(indata ** 2))) * 12)

    def empezar(self):
        with self._lock:
            self.trozos = []
        self.nivel = 0.0
        self._inicio = time.monotonic()
        self._stream = sd.InputStream(
            samplerate=FRECUENCIA, channels=1,
            dtype="float32", callback=self._callback,
        )
        self._stream.start()

    def segundos_grabados(self):
        return time.monotonic() - self._inicio if self._stream else 0.0

    def audio(self):
        with self._lock:
            if not self.trozos:
                return None
            return np.concatenate(self.trozos, axis=0).flatten()

    def espectro(self, bandas=32):
        """Reparte la energía del audio reciente en bandas de frecuencia.

        Antes solo se mandaba el volumen (un número), así que las marcas del
        anillo subían y bajaban todas a la vez. Con el espectro cada marca
        responde a una zona del sonido y se ve de verdad la forma de la voz.
        Las bandas van en escala logarítmica porque así oye el oído: hay
        tanta diferencia entre 100 y 200 Hz como entre 1000 y 2000.
        """
        with self._lock:
            if not self.trozos:
                return [0.0] * bandas
            reciente = self.trozos[-8:]
        muestras = np.concatenate(reciente, axis=0).flatten()
        if len(muestras) < 256:
            return [0.0] * bandas

        ventana = muestras[-1024:] * np.hanning(min(1024, len(muestras)))
        magnitud = np.abs(np.fft.rfft(ventana))

        # La voz vive entre 80 Hz y 4 kHz; por encima solo hay siseo
        hz = np.fft.rfftfreq(len(ventana), 1 / FRECUENCIA)
        util = (hz >= 80) & (hz <= 4000)
        magnitud, hz = magnitud[util], hz[util]
        if not len(magnitud):
            return [0.0] * bandas

        # En decibelios, como cualquier medidor de audio. En escala lineal el
        # ruido de fondo y la voz salían casi iguales (0,62 contra 0,65) y la
        # animación no distinguía si estabas hablando o callado.
        magnitud = magnitud / len(ventana)
        cortes = np.logspace(np.log10(80), np.log10(4000), bandas + 1)
        salida = []
        for i in range(bandas):
            trozo = magnitud[(hz >= cortes[i]) & (hz < cortes[i + 1])]
            energia = float(trozo.mean()) if len(trozo) else 0.0
            db = 20 * np.log10(energia + 1e-10)
            # -75 dB es silencio de sala; -35 dB es hablar cerca del micro
            salida.append(round(float(np.clip((db + 75) / 40, 0, 1)), 3))
        return salida

    def audio_reciente(self, segundos):
        """Solo los últimos N segundos, para que las parciales no crezcan sin fin."""
        audio = self.audio()
        if audio is None:
            return None
        recorte = int(segundos * FRECUENCIA)
        return audio[-recorte:] if len(audio) > recorte else audio

    def parar(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.nivel = 0.0
        return self.audio()


class MicrofonoRemoto(Microfono):
    """El audio llega del navegador, no de la tarjeta de sonido.

    Es lo que permite hablarle desde el móvil: allí el micrófono lo
    captura el navegador con getUserMedia y manda el PCM por el mismo
    WebSocket. Hereda de Microfono a propósito, para que el espectro, el
    recorte por segundos y el volcado a Whisper sean EXACTAMENTE el
    mismo código. Todo lo que consume audio —VAD, parciales, anillo— no
    se entera de cuál de las dos fuentes tiene delante.

    Solo cambia de dónde salen los trozos: aquí los mete alimentar().
    """

    def __init__(self):
        super().__init__()
        self._activo = False

    def empezar(self):
        with self._lock:
            self.trozos = []
        self.nivel = 0.0
        self._inicio = time.monotonic()
        self._activo = True

    def segundos_grabados(self):
        # El de arriba mira self._stream, que aquí no existe nunca
        return time.monotonic() - self._inicio if self._activo else 0.0

    def alimentar(self, crudo):
        """Mete un trozo de PCM recién llegado del navegador.

        Viene en int16: la mitad de bytes que float32, y por wifi eso se
        nota (32 KB/s contra 64). Whisper quiere float32 normalizado, así
        que se convierte aquí.
        """
        if not self._activo:
            return          # llegó tarde, después de soltar el botón
        muestras = np.frombuffer(crudo, dtype=np.int16).astype(np.float32) / 32768.0
        if not len(muestras):
            return
        with self._lock:
            self.trozos.append(muestras.reshape(-1, 1))
        self.nivel = min(1.0, float(np.sqrt(np.mean(muestras ** 2))) * 12)

    def parar(self):
        self._activo = False
        self.nivel = 0.0
        return self.audio()


# ---------------------------------------------------------------
# VOZ (en su propio hilo: SAPI de Windows lo necesita)
# ---------------------------------------------------------------

_cola_voz = queue.Queue()

# Se levanta cuando el usuario quiere cortarle mientras habla. Lo mira tanto
# el hilo de voz (para purgar la frase en curso) como hablar() (para no
# encolar las que aún no han empezado).
_cancelar_voz = threading.Event()

# A dónde sale la voz sintetizada. None = altavoces del PC. Si no, una
# función que recibe (muestras float32, frecuencia) y la manda al
# navegador. Es global, como la cola de voz, porque solo puede haber una
# conversación a la vez: un micrófono, un modelo y una voz.
_salida_voz = None


def salida_voz_a(funcion):
    """Redirige la voz al navegador, o a los altavoces con None."""
    global _salida_voz
    _salida_voz = funcion


def _crear_voz_piper():
    """Voz neuronal local. Es la que hace que no suene a robot.

    SAPI (la voz Helena de Windows) es de hace veinte años y se nota:
    ningún ajuste del texto la arregla, el problema es el sintetizador.
    Piper genera 6 s de audio en 0,33 s, así que va sobrado.

    Se reproduce por trozos con sounddevice en vez de esperar al WAV entero:
    así empieza a sonar antes y, sobre todo, se puede cortar a media frase
    cuando el usuario interrumpe.
    """
    from piper import PiperVoice, SynthesisConfig

    ruta = AQUI_VOCES / f"{VOZ_PIPER}.onnx"
    if not ruta.exists():
        raise FileNotFoundError(
            f"falta {ruta}. Descárgala con:\n"
            f"  python -m piper.download_voices {VOZ_PIPER} --data-dir voces")

    voz = PiperVoice.load(str(ruta))
    ajustes = SynthesisConfig(length_scale=VELOCIDAD_VOZ, volume=1.0)

    def decir(t):
        for trozo in voz.synthesize(t, ajustes):
            if _cancelar_voz.is_set():
                return
            destino = _salida_voz
            if destino is not None:
                # Hablándole desde el móvil, la voz tiene que salir POR EL
                # MÓVIL. Si sonara por los altavoces del PC estarías
                # hablándole a un aparato y escuchándole en otro.
                if not _reproducir_fuera(destino, trozo):
                    return
                continue
            sd.play(trozo.audio_float_array, trozo.sample_rate)
            # Se espera vigilando la cancelación en vez de usar sd.wait(),
            # que bloquearía sin posibilidad de cortar.
            while True:
                flujo = sd.get_stream()
                if flujo is None or not flujo.active:
                    break
                if _cancelar_voz.is_set():
                    sd.stop()
                    return
                time.sleep(0.03)

    return decir


def _reproducir_fuera(destino, trozo):
    """Manda un trozo al navegador y espera lo que dura. False si se corta.

    Se espera a propósito, en vez de soltarlo todo de golpe: si el
    navegador tuviera treinta segundos de audio en el buffer, pulsar para
    interrumpir no callaría nada. Mandándolo al ritmo al que se oye, lo
    que queda por decir todavía no ha salido de aquí.
    """
    muestras = trozo.audio_float_array
    frecuencia = trozo.sample_rate
    try:
        destino(np.asarray(muestras, dtype=np.float32), frecuencia)
    except Exception as e:
        print(f"[voz] no se pudo enviar al navegador: {e}")
        return False

    dura = len(muestras) / float(frecuencia)
    fin = time.monotonic() + dura
    while time.monotonic() < fin:
        if _cancelar_voz.is_set():
            return False
        time.sleep(0.03)
    return True


def _crear_voz_windows():
    """Habla con SAPI directamente, sin pasar por pyttsx3.

    pyttsx3 solo decía la PRIMERA frase: a partir de la segunda, runAndWait()
    volvía en 0,1 s sin emitir sonido. Pasaba tanto creando el motor en cada
    frase como reutilizando uno solo, así que el problema está en cómo maneja
    el bucle de eventos de SAPI, no en cómo se le llama.
    SAPI directo: 4 de 4 frases suenan.
    """
    import win32com.client
    voz = win32com.client.Dispatch("SAPI.SpVoice")
    for v in voz.GetVoices():
        if "spanish" in v.GetDescription().lower() or "helena" in v.GetDescription().lower():
            voz.Voice = v
            break
    voz.Rate = 1                      # SAPI va de -10 a 10; 1 es algo ágil

    def decir(t):
        # Se habla en modo asíncrono (bandera 1) y se vigila la cancelación
        # aquí mismo. Si se usara el modo bloqueante no habría forma de
        # cortarle a media frase, que es justo lo que hace falta para poder
        # interrumpirle. El purgado (bandera 2) se llama desde ESTE hilo,
        # que es el único que puede tocar el objeto COM con seguridad.
        voz.Speak(t, 1)
        while True:
            if voz.Status.RunningState == 1:       # 1 = ha terminado
                return
            if _cancelar_voz.is_set():
                voz.Speak("", 2)                   # 2 = purgar lo pendiente
                return
            time.sleep(0.05)

    return decir


def _crear_voz_pyttsx3():
    """Alternativa para cuando no hay SAPI (Linux, macOS)."""
    motor = pyttsx3.init()
    motor.setProperty("rate", 180)

    def decir(t):
        motor.say(t)
        motor.runAndWait()

    return decir


def _hilo_voz():
    """Único hilo que habla. SAPI necesita que sea siempre el mismo."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    decir = None
    # Por orden de calidad: Piper primero, y SAPI o pyttsx3 solo si falla
    for crear in (_crear_voz_piper, _crear_voz_windows, _crear_voz_pyttsx3):
        try:
            decir = crear()
            print(f"  voz: {crear.__name__.replace('_crear_voz_', '')}")
            break
        except Exception as e:
            print(f"  voz: {crear.__name__} no disponible ({e})")

    while True:
        texto, terminado = _cola_voz.get()
        if texto is None:
            break
        try:
            if decir is None:
                raise RuntimeError("no hay ningún motor de voz disponible")
            decir(texto)
        except Exception as e:
            print(f"Error de voz: {e}")
        finally:
            terminado.set()


threading.Thread(target=_hilo_voz, daemon=True).start()


def limpiar(texto):
    texto = re.sub(r"[*#`_•]", " ", texto)
    return " ".join(texto.split())


# Abreviaturas que acaban en punto sin terminar la frase
ABREVIATURAS = ("sr", "sra", "srta", "dr", "dra", "etc", "ej", "pág", "núm", "av")


def frase_terminada(buf):
    """¿Se puede mandar ya este trozo a la voz?

    El regex de antes era [.!?…]\\s*$ y se disparaba con cualquier punto,
    así que "a las 20.40" se partía en "a las 20." y "40", y la voz decía
    dos frases donde había una hora. Ahora se exige que después del punto
    haya llegado ya un espacio, y se descartan los puntos entre cifras y
    los de abreviatura.
    """
    if not re.search(r"[.!?…]\s+$", buf):
        return False
    limpio = buf.rstrip()
    if re.search(r"\d[.,]$", limpio):          # "20." dentro de "20.40"
        return False
    ultima = re.split(r"[\s(]", limpio[:-1])[-1].lower()
    if ultima in ABREVIATURAS:
        return False
    return len(limpio) >= 12                   # trozos muy cortos no valen la pena


def hablar(texto):
    """Bloquea hasta que termina de hablar. Llamar desde un hilo."""
    texto = limpiar(texto)
    if not texto or _cancelar_voz.is_set():
        return
    terminado = threading.Event()
    _cola_voz.put((texto, terminado))
    terminado.wait()


def cortar_voz():
    """Calla al asistente ya mismo y tira lo que quedaba por decir."""
    _cancelar_voz.set()
    while True:
        try:
            _, terminado = _cola_voz.get_nowait()
            terminado.set()          # quien esperaba esa frase no se queda colgado
        except queue.Empty:
            break


def permitir_voz():
    """Vuelve a dejar hablar. Se llama al empezar cada interacción nueva."""
    _cancelar_voz.clear()


# ---------------------------------------------------------------
# TRANSCRIPCIÓN Y RAZONAMIENTO
# ---------------------------------------------------------------

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
        texto = " ".join(s.text for s in segmentos).strip()
    total = time.monotonic() - t0
    if etiqueta:
        print(f"[{etiqueta}] {total:.1f}s (espera {espera:.1f}s) "
              f"{len(audio)/FRECUENCIA:.1f}s de audio -> {texto[:60]!r}")
    return texto


def recortar_historial(historial):
    """Quita los turnos viejos, dejando siempre el prompt de sistema."""
    if len(historial) > MAX_TURNOS + 1:
        del historial[1:3]


# ---------------------------------------------------------------
# HERRAMIENTAS
#
# Dos llamadas al modelo, no una. Se probó a pasarle las herramientas
# directamente al asistente y se estropea la conversación: empieza a
# contestar "no tengo una función para eso" a preguntas normales, o
# narra en voz alta que no le hace falta ninguna herramienta.
# Separando router y conversador se obtuvo 9/9 de enrutado y 5/5 de
# conversación útil.
#
# IMPORTANTE: las dos llamadas deben usar el MISMO num_ctx. Si cambia,
# Ollama recarga el modelo entero en cada turno: medido, 17,3 s por
# pregunta contra 1,2 s.
# ---------------------------------------------------------------

HERRAMIENTAS = [
    {"type": "function", "function": {
        "name": "que_hora_es",
        "description": "Consulta el reloj del ordenador para saber la hora y la fecha de ahora mismo.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "anadir_tarea",
        "description": "Guarda una tarea o recordatorio nuevo en la lista del usuario.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string", "description": "Qué hay que hacer, sin el momento"},
            "cuando": {"type": "string",
                       "description": "Copia LITERAL de lo que dijo el usuario sobre CUÁNDO, "
                                      "sin traducirlo, resumirlo ni cambiarlo. Si dijo "
                                      "'a las 8.40', pon 'a las 8.40'. Si dijo 'el jueves a "
                                      "las 5', pon 'el jueves a las 5'. NUNCA inventes un "
                                      "momento que el usuario no haya dicho: si no dijo "
                                      "cuándo, déjalo vacío."}},
            "required": ["texto"]}}},
    {"type": "function", "function": {
        "name": "listar_tareas",
        "description": "Consulta las tareas pendientes que el usuario tiene guardadas.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string",
                      "description": "Nombre de UNA tarea concreta, si el usuario "
                                     "pregunta por algo en particular: '¿a qué hora "
                                     "tengo el test?' -> 'test'. Vacío si pregunta "
                                     "en general por lo que tiene pendiente."},
            "cuando": {"type": "string",
                       "description": "Copia LITERAL y COMPLETA de las palabras del usuario "
                                      "sobre el momento, sin traducirlas, resumirlas ni "
                                      "acortarlas. Si dice 'mañana por la tarde', pon "
                                      "'mañana por la tarde' ENTERO, no solo 'mañana': "
                                      "la franja del día importa tanto como el día. "
                                      "Otros ejemplos: 'hoy', 'en 30 minutos', 'esta noche', "
                                      "'el jueves por la mañana'. Si da dos horas, copia el "
                                      "tramo COMPLETO: 'entre las 2 y las 5 de la tarde', "
                                      "'mañana de 9 a 11'. Déjalo vacío SOLO si no "
                                      "mencionó ningún momento ('¿qué tengo pendiente?')."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "buscar_en_web",
        "description": "Busca en internet información ACTUAL que no puedes saber: "
                       "noticias, resultados deportivos, clasificaciones, el tiempo, "
                       "precios, cotizaciones, qué ha pasado hoy, o cualquier cosa "
                       "posterior a tu entrenamiento. NO la uses para cultura general, "
                       "historia, definiciones ni cosas que ya sabes.",
        "parameters": {"type": "object", "properties": {
            "consulta": {"type": "string",
                         "description": "Qué buscar, en pocas palabras y como se "
                                        "escribiría en un buscador: 'clasificación "
                                        "Liga española', 'tiempo Madrid mañana'"}},
            "required": ["consulta"]}}},
    {"type": "function", "function": {
        "name": "abrir_programa",
        "description": "Abre una aplicación del ordenador: el navegador, Spotify, "
                       "la calculadora, el explorador de archivos, VS Code, el "
                       "bloc de notas, el correo, los ajustes o la terminal.",
        "parameters": {"type": "object", "properties": {
            "programa": {"type": "string",
                         "description": "Qué abrir, con las palabras del usuario: "
                                        "'spotify', 'el navegador', 'la calculadora'"}},
            "required": ["programa"]}}},
    {"type": "function", "function": {
        "name": "enviar_correo",
        "description": "Escribe y manda un correo por Gmail a alguien de la agenda "
                       "del usuario. Úsalo cuando pida escribir, mandar o enviar "
                       "un correo, un email o un mensaje a una persona.",
        "parameters": {"type": "object", "properties": {
            "destinatario": {"type": "string",
                             "description": "A quién, con el nombre que usó el "
                                            "usuario: 'Ana', 'mi jefe'. NUNCA una "
                                            "dirección de correo: se saca de su agenda."},
            "asunto": {"type": "string",
                       "description": "Asunto corto, de menos de ocho palabras"},
            "mensaje": {"type": "string",
                        "description": "El correo ya redactado y completo, con "
                                       "saludo y despedida, a partir de lo que "
                                       "pidió el usuario. Escríbelo tú bien, no "
                                       "copies su frase tal cual."}},
            "required": ["destinatario", "mensaje"]}}},
    {"type": "function", "function": {
        "name": "leer_correos",
        "description": "Mira la bandeja de entrada de Gmail del usuario: si "
                       "le ha llegado algo nuevo, quién le ha escrito, de qué "
                       "van los correos o un resumen de lo recibido. Es para "
                       "correos que LE HAN LLEGADO a él. Para escribir y "
                       "mandar uno se usa enviar_correo, no esta.",
        "parameters": {"type": "object", "properties": {
            "solo_nuevos": {"type": "boolean",
                            "description": "true si pregunta por lo nuevo, lo "
                                           "sin leer o lo que le ha llegado; "
                                           "false si pide sus últimos correos "
                                           "en general"},
            "de": {"type": "string",
                   "description": "Nombre de la persona, si pregunta si le ha "
                                  "escrito alguien concreto: 'Ana', 'mi jefe'. "
                                  "Vacío si pregunta en general."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "cerrar_programa",
        "description": "Cierra una aplicación que está abierta ahora mismo: "
                       "'cierra spotify', 'cierra el navegador', 'quita smite'. "
                       "Se cierra con normalidad, así que si hay algo sin guardar "
                       "la aplicación lo preguntará.",
        "parameters": {"type": "object", "properties": {
            "programa": {"type": "string",
                         "description": "Qué cerrar, con las palabras del usuario"}},
            "required": ["programa"]}}},
    {"type": "function", "function": {
        "name": "ejecutar_atajo",
        "description": "Lanza una tarea que el usuario tiene configurada en su "
                       "fichero de atajos: copias de seguridad, scripts, rutinas "
                       "suyas. Usalo cuando pida algo por su nombre y no encaje "
                       "en ninguna otra herramienta.",
        "parameters": {"type": "object", "properties": {
            "nombre": {"type": "string",
                       "description": "Nombre del atajo, con las palabras del usuario"}},
            "required": ["nombre"]}}},
    {"type": "function", "function": {
        "name": "control_sistema",
        "description": "Controla el ordenador: apagarlo, reiniciarlo, bloquear la "
                       "pantalla, cancelar un apagado programado, o subir, bajar "
                       "y silenciar el volumen.",
        "parameters": {"type": "object", "properties": {
            "accion": {"type": "string",
                       "enum": ["apagar", "reiniciar", "suspender", "bloquear",
                                "cancelar", "subir volumen", "bajar volumen",
                                "silenciar", "captura", "minimizar"],
                       "description": "Exactamente una de las opciones de la lista"}},
            "required": ["accion"]}}},
    {"type": "function", "function": {
        "name": "recordar_dato",
        "description": "Guarda un dato que el usuario cuenta SOBRE SÍ MISMO o su "
                       "entorno, y que no es algo que tenga que hacer: cómo se "
                       "llama, dónde vive, a qué se dedica, sus gustos, sus "
                       "alergias, nombres de familiares. No es una tarea: no se "
                       "hace ni se completa, solo se recuerda.",
        "parameters": {"type": "object", "properties": {
            "dato": {"type": "string",
                     "description": "El dato como FRASE COMPLETA en tercera "
                                    "persona, diciendo SIEMPRE de quién se habla. "
                                    "'me llamo Toti' -> 'se llama Toti'. "
                                    "'mi novia se llama Verónica' -> 'su novia se "
                                    "llama Verónica'. 'mi jefe es Luis' -> 'su jefe "
                                    "es Luis'. NUNCA pierdas de quién es el dato: "
                                    "'se llama Verónica' a secas sería un error, "
                                    "porque parece que el usuario se llama así."}},
            "required": ["dato"]}}},
    {"type": "function", "function": {
        "name": "completar_tarea",
        "description": "Quita una tarea de la lista, porque el usuario ya la ha "
                       "hecho o porque pide borrarla, cancelarla o anularla. "
                       "Sirve tanto para 'ya he comprado el pan' como para "
                       "'bórrame el dentista de mañana'.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string",
                      "description": "Qué tarea quitar, con las palabras del "
                                     "usuario pero SIN el verbo de borrar: si "
                                     "dice 'bórrame el test de mañana', pon "
                                     "'el test de mañana'"}},
            "required": ["texto"]}}},
]

PROMPT_ROUTER = """Decide si la frase del usuario necesita una de tus herramientas.

Usa anadir_tarea cuando el usuario cuente algo que tiene que hacer:
"recuérdame X", "apunta X", "tengo que X", "no se me olvide X".

Usa listar_tareas cuando pregunte por lo que tiene pendiente:
"¿qué tengo que hacer?", "¿qué tengo hoy?", "¿qué me queda?",
"¿qué tenía apuntado?", "mis tareas", "mi agenda".

Usa completar_tarea cuando diga que ya ha hecho algo, O cuando pida QUITAR
algo que ya tenía apuntado:
"ya he X", "ya está lo de X", "hecho lo de X", "terminé X",
"quita X", "bórrame X", "borra lo de X", "elimina X", "cancela X",
"anula lo de X", "ya no tengo que X", "olvídate de X".

OJO: "bórrame el test de mañana" NO es una tarea nueva llamada "borrarme el
test". Es quitar de la lista el test que ya estaba apuntado.

Usa listar_tareas TAMBIÉN cuando pregunte por UNA tarea concreta, poniendo
su nombre en "texto" y dejando "cuando" vacío:
"¿a qué hora tengo el test?"     -> texto="test"
"¿cuándo tengo el dentista?"     -> texto="dentista"
"¿a qué hora es la reunión?"     -> texto="reunión"
Eso pregunta por su agenda, NO por el reloj.

Usa que_hora_es SOLO cuando pregunte qué hora es AHORA o en qué fecha
estamos, sin referirse a ninguna tarea suya.

Usa abrir_programa cuando pida abrir una aplicación:
"abre spotify", "pon música", "ábreme el navegador", "abre la calculadora".

Usa enviar_correo cuando pida escribir o mandar un correo a alguien:
"mándale un correo a Ana diciendo que llego tarde", "escríbele a mi jefe".
Tú REDACTAS el mensaje completo, con saludo y despedida, a partir de lo
que te ha dicho. El destinatario va con su NOMBRE, nunca con su dirección.

Usa cerrar_programa cuando pida cerrar una que ya está abierta:
"cierra spotify", "cierra el navegador", "quítame el discord", "sal del juego".

Usa control_sistema para el ordenador en sí:
"apaga el ordenador", "reinicia", "bloquea la pantalla", "cancela el apagado",
"sube el volumen", "baja el volumen", "silencia".

OJO con apagar y reiniciar: solo si lo PIDE. "El ordenador va lento" o
"¿se apaga solo?" NO son órdenes de apagado.

Usa recordar_dato cuando cuente algo sobre sí mismo o sobre su gente, que no
hay que hacer: "me llamo X", "soy X", "vivo en X", "mi novia se llama X",
"mi hermana es X", "soy alérgico a X", "me gusta X", "trabajo en X".
Eso no son tareas: no se hacen ni se completan, solo se recuerdan.

El dato va en tercera persona y SIEMPRE con su sujeto:
"me llamo Toti"                -> "se llama Toti"
"mi novia se llama Verónica"   -> "su novia se llama Verónica"
"mi hermana vive en Vigo"      -> "su hermana vive en Vigo"
Nunca lo recortes a "se llama Verónica": se perdería de quién hablas.

anadir_tarea SOLO si el usuario dice QUÉ hay que hacer. Si la frase no
nombra ninguna acción concreta, no la apuntes. "¿Tengo algo que hacer esta
noche?" es una CONSULTA (listar_tareas), no una tarea llamada "algo que
hacer". Nunca apuntes una tarea cuyo texto sea "algo", "cosas" o "vale".

NO USES NINGUNA HERRAMIENTA en estos casos, que son conversación normal:
"¿qué tal has pasado el día?"   -> te pregunta cómo estás
"¿cómo ha ido la mañana?"       -> te pregunta cómo estás
"¿qué has hecho hoy?"           -> te pregunta por ti, no por su agenda
"¿te acuerdas de lo de ayer?"   -> charla sobre el pasado
"mañana es viernes, ¿no?"       -> comentario, no pregunta la hora
"hoy hace buen día"             -> comentario

Una PREGUNTA sobre el pasado o sobre cómo estás NUNCA es una tarea nueva.
Solo se apunta una tarea si el usuario ENCARGA algo que hay que hacer.
Que una frase contenga "hoy", "mañana" o "el día" no la convierte en tarea.

Para cualquier otra cosa (charla, cultura, chistes, opiniones, saludos,
preguntas de conocimiento) NO uses ninguna herramienta y no respondas nada."""

FUNCIONES = {
    "que_hora_es": lambda **k: memoria.que_hora_es(),
    "anadir_tarea": lambda texto="", cuando="", **k: memoria.anadir_tarea(texto, cuando),
    "listar_tareas": lambda cuando="", texto="", **k: memoria.listar_tareas(cuando, texto),
    "completar_tarea": lambda texto="", **k: memoria.completar_tarea(texto),
    "recordar_dato": lambda dato="", **k: memoria.recordar_dato(dato),
    "abrir_programa": lambda programa="", **k: sistema.abrir_programa(programa),
    "cerrar_programa": lambda programa="", **k: sistema.cerrar_programa(programa),
    "control_sistema": lambda accion="", **k: sistema.ejecutar_accion(accion),
    "ejecutar_atajo": lambda nombre="", **k: sistema.ejecutar_atajo(nombre),
}


def prompt_con_fecha():
    """El prompt de sistema con la fecha de hoy metida dentro.

    El modelo no tiene reloj: sin esto se inventa en qué día vive y calcula
    mal cualquier cosa relativa ("dentro de dos semanas", "el mes que viene").
    Se recalcula en cada turno para que siga valiendo si el asistente lleva
    horas abierto y ha pasado la medianoche.
    """
    a = datetime.now()
    partes = [PROMPT_SISTEMA,
              f"Hoy es {memoria.DIAS_ES[a.weekday()]} "
              f"{a.day} de {memoria.MESES_ES[a.month - 1]} de {a.year}, "
              f"y son las {a.hour}:{a.minute:02d}."]

    # Lo que Jarvis sabe del usuario. Esto es lo que separa un asistente que
    # te conoce de uno que empieza de cero en cada conversación: si le has
    # dicho cómo te llamas, tiene que poder responder cuando se lo preguntes.
    datos = memoria.datos_conocidos()
    if datos:
        partes.append(
            "LO QUE SABES DEL USUARIO\n" +
            "\n".join(f"- {memoria.como_frase(d)}" for d in datos) +
            "\n\nEso es TODO lo que sabes de él, y es fiable: si la respuesta "
            "está ahí, dala directamente sin decir que no tienes información. "
            "Si no está, dilo y ya. No inventes nada sobre su vida ni sobre "
            "las personas que menciona.")
    return "\n\n".join(partes)


# Verbos con los que alguien pide de verdad que le apunten algo. Si no
# aparece ninguno, una frase interrogativa no es un encargo.
VERBOS_TAREA = (
    "apunta", "apunte", "anota", "anote", "añade", "agrega", "mete",
    "recuerdame", "recuérdame", "recuerda", "acuerdate", "acuérdate",
    "tengo que", "he de", "no se me olvide", "no me deje", "avisame", "avísame",
)


def _sin_tildes(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def es_pregunta(t):
    """¿La frase (ya sin tildes y en minúsculas) es una pregunta?"""
    return t.startswith("¿") or t.endswith("?") or bool(
        re.match(r"^(que|como|cuando|cuanto|cual|quien|donde|por que)\b", t))


def parece_encargo(frase):
    """¿Es esto un encargo de apuntar algo, o solo una pregunta de charla?

    Se probó a dejarlo en manos del modelo y acertaba 5 de 10: convertía
    "¿cómo ha ido la mañana?" en una tarea llamada "¿cómo ha ido la mañana?".
    Una pregunta solo es un encargo si además lleva un verbo de encargo
    ("¿me apuntas comprar pan?" sí; "¿qué tal el día?" no).
    """
    t = _sin_tildes(frase).strip()
    if not es_pregunta(t):
        return True
    return any(v in t for v in (_sin_tildes(v) for v in VERBOS_TAREA))


# Frases que el modelo mete como "tarea" cuando en realidad no hay tarea.
# "Vale, perfecto" no es un recado, y "algo que hacer" no dice qué hacer.
TAREAS_VACIAS = {
    "algo", "algo que hacer", "cosas", "cosas que hacer", "nada", "esto",
    "eso", "vale", "perfecto", "gracias", "ok", "si", "no", "bien",
    "una tarea", "tarea", "recordatorio", "hacer algo", "lo de siempre",
}


# Muletillas y acuses de recibo. No son datos sobre nadie: son ruido de
# conversación que el modelo intentaba guardar como si fueran hechos.
RELLENO = {
    "vale", "perfecto", "gracias", "muy amable", "ok", "okay", "de acuerdo",
    "genial", "estupendo", "muy bien", "bien", "claro", "si", "no", "ya",
    "entendido", "correcto", "eso es", "exacto", "nada", "adios", "hasta luego",
}


def dato_vacio(texto):
    """¿Este 'dato' dice algo del usuario, o es solo una muletilla?

    Salió de la evaluación: "Vale, perfecto" se guardaba como un hecho
    sobre el usuario. Se contamina la memoria y encima se la lee luego en
    el prompt de cada turno.
    """
    # La puntuación se quita de CADA palabra, no solo de los extremos:
    # "vale, perfecto" se partía en ["vale,", "perfecto"] y "vale," con la
    # coma pegada no coincidía con nada de la lista.
    palabras = [p.strip(".,;:¿?¡!\"'") for p in _sin_tildes(texto).split()]
    palabras = [p for p in palabras if p]
    if not palabras:
        return "el dato viene vacío"
    if all(p in RELLENO for p in palabras):
        return f"{texto!r} es una muletilla, no un dato"
    if len("".join(palabras)) < 4:
        return f"{texto!r} es demasiado corto"
    return None


def tarea_vacia(texto):
    """¿Esta 'tarea' dice de verdad qué hay que hacer?

    Salió de un caso real: "¿tengo algo que hacer esta noche?" se apuntó
    como una tarea llamada "algo que hacer". Una tarea sin contenido no
    sirve para nada y además ensucia la lista.
    """
    t = _sin_tildes(texto).strip(" .,¿?¡!")
    if not t:
        return "el texto viene vacío"
    if t in TAREAS_VACIAS:
        return f"{texto!r} no dice qué hay que hacer"
    if len(t) < 3:
        return f"{texto!r} es demasiado corto"
    return None


# Verbos con los que se pide información, no se cuenta un dato
VERBOS_PREGUNTA = ("explica", "explicame", "dime", "cuentame", "describe",
                   "define", "sabes", "conoces", "puedes decirme", "que es")


# Marcas de que la frase habla del usuario o de su gente. Sin alguna de
# estas, no es un dato personal por mucho que lo parezca.
MARCAS_PERSONALES = (
    r"\bme\b", r"\bmi\b", r"\bmis\b", r"\bmio\b", r"\bmia\b", r"\bconmigo\b",
    r"\bsoy\b", r"\bestoy\b", r"\btengo\b", r"\bvivo\b", r"\btrabajo\b",
    r"\bllamo\b", r"\bnaci\b", r"\bprefiero\b", r"\bodio\b", r"\bjuego\b",
    r"\bestudio\b", r"\bquiero\b", r"\bsuelo\b",
)


def parece_dato_personal(frase):
    """¿Está el usuario contándome algo suyo, o pidiéndome información?

    Tres cosas distintas que no son un dato personal, y las tres pasaban:
      - "¿Me podrías decir mi nombre?"  pregunta por el dato, no lo aporta
      - "Explícame qué es una API"      pide información
      - "Hoy hace buen día"             habla del tiempo, no de él

    Ese último era el peor: escribía en la memoria una observación sobre
    el clima, y luego se la leía en el prompt de todos los turnos.
    """
    t = _sin_tildes(frase).strip()
    if es_pregunta(t):
        return "recuerda" in t          # "recuerda que soy X" sí vale
    if any(t.startswith(v) for v in VERBOS_PREGUNTA):
        return False
    # Tiene que hablar de él o de los suyos
    return any(re.search(m, t) for m in MARCAS_PERSONALES)


# Señales de que la pregunta va de algo que CAMBIA y no está en el modelo.
# Sin una de estas, buscar en internet es gastar dos segundos y una conexión
# para algo que ya sabe: se midió que buscaba hasta la capital de Francia.
SENALES_ACTUALIDAD = (
    # el momento
    "ahora", "actual", "hoy", "esta semana", "este mes", "ultimo", "ultima",
    "ultimos", "ultimas", "reciente", "novedad", "todavia", "ya ha", "va ganando",
    "que tal va", "como va", "sigue siendo",
    # cosas que cambian solas
    "tiempo hace", "temperatura", "llueve", "lloviendo", "pronostico", "clima",
    "precio", "cuanto cuesta", "cotiza", "bolsa", "euro", "dolar", "bitcoin",
    "noticia", "noticias", "ha pasado", "ha ganado", "gano el", "resultado",
    "resultados", "clasificacion", "partido", "marcador", "estreno", "cartelera",
    "horario", "abierto", "cerrado", "trafico", "vuelo",
)


def merece_busqueda(frase):
    """¿Justifica esta pregunta salir a internet?

    El modelo, si le das una herramienta de búsqueda, la usa para todo:
    medido, buscaba "capital de Francia" y "quién pintó Las Meninas".
    Cada búsqueda son dos segundos y medio y una conexión, así que solo
    se sale fuera cuando la pregunta pide algo que cambia con el tiempo.
    """
    t = _sin_tildes(frase)
    # Si lo pide con todas las letras, se busca y no se discute. Este filtro
    # existe para que el modelo no busque por su cuenta cosas que ya sabe,
    # no para llevarle la contraria al usuario. Pasaba de verdad: a
    # "tienes que buscar en internet cuáles son los mejores trabajos" le
    # contestaba de memoria, y encima empezando por "no tengo acceso a
    # internet" cuando sí lo tiene.
    # El \w{0,4} del final recoge los pronombres pegados: búscaLO,
    # consúltaMELO, míraLA. En español van dentro de la palabra y una
    # lista cerrada de formas se queda corta siempre.
    if re.search(r"\b(busca|buscar|mira|mirar|consulta|consultar|averigua|"
                 r"averiguar|investiga|investigar|informate)\w{0,5}\b"
                 r"[^.]{0,30}"
                 r"\b(internet|la red|la web|google|online|en linea)\b", t):
        return True
    if any(s in t for s in SENALES_ACTUALIDAD):
        return True
    # Un año reciente también cuenta: "quién ganó la liga en 2026"
    return bool(re.search(r"\b20[2-9]\d\b", t))


# Verbos con los que se pide QUITAR algo de la lista, no añadirlo
VERBOS_BORRAR = ("borra", "borrame", "borrarme", "borrar", "borralo", "borrala",
                 "quita", "quitame", "quitar", "quitalo", "quitala",
                 "elimina", "eliminame", "eliminar", "cancela", "cancelame",
                 "cancelar", "anula", "anular", "olvidate de", "ya no tengo que")


def pide_borrar(frase):
    """¿Está pidiendo quitar algo de la lista?

    Salió de un caso real: "vale, puedes borrarme ese test de mañana" se
    apuntó como una tarea NUEVA llamada "borrarme ese test". El modelo solo
    conocía "ya he hecho X" y "quita X" como formas de completar.
    """
    t = _sin_tildes(frase)
    return any(re.search(r"\b" + v.replace(" ", r"\s+"), t) for v in VERBOS_BORRAR)


def sin_verbo_borrar(texto):
    """Quita el verbo de borrar del texto, para poder buscar la tarea.

    "borrarme ese test" no casa con "un test de matemáticas" tan bien como
    "ese test", porque la palabra del verbo mete ruido en la comparación.
    """
    t = texto
    for v in sorted(VERBOS_BORRAR, key=len, reverse=True):
        t = re.sub(r"\b" + v.replace(" ", r"\s+") + r"\b", " ",
                   t, flags=re.IGNORECASE)
    # también sobran las muletillas del principio
    t = re.sub(r"^\s*(vale|oye|jarvis|puedes|por favor|me)\b[\s,]*", " ",
               t.strip(), flags=re.IGNORECASE)
    return " ".join(t.split()) or texto


def cuando_inventado(cuando, frase):
    """¿Se ha sacado el modelo ese "cuándo" de la manga?

    Caso real: a "¿a qué hora tengo el test de matemáticas?" respondió con
    cuando='a las 8 de la mañana'. El usuario no dijo ninguna hora, así que
    el filtro no encontraba nada y contestaba "no tienes nada apuntado para
    a las 8 de la mañana", que suena a alucinación porque lo es.

    La comprobación es simple: alguna palabra con contenido del "cuándo"
    tiene que aparecer también en lo que dijo el usuario.
    """
    if not cuando or not cuando.strip():
        return False
    hueca = {"a", "las", "la", "de", "del", "el", "en", "por", "y", "los"}
    palabras = [p for p in _sin_tildes(cuando).split() if p not in hueca]
    if not palabras:
        return False
    dicho = _sin_tildes(frase)
    return not any(p in dicho for p in palabras)


# Verbos con los que se ORDENA algo al ordenador. Sin uno de estos, la
# frase es un comentario y no debe mover nada.
VERBOS_SISTEMA = (
    r"apag", r"reinici", r"suspend", r"bloque", r"cancel",
    r"\bsube\b", r"\bsubir\b", r"\bsubeme\b",
    r"\bbaja\b", r"\bbajar\b", r"\bbajame\b",
    r"silenci", r"\bmutea", r"minimiz", r"captur",
    r"\bhaz\b", r"\bhazme\b", r"\bpon\b", r"\bponme\b",
    r"\bquita\b", r"\bcierra\b", r"\bmuestra\b", r"\benseñame\b",
    r"\bvolumen\b",
)


AFIRMATIVAS = {"si", "sip", "vale", "confirmo", "confirmado", "adelante",
               "hazlo", "dale", "claro", "afirmativo", "correcto", "eso",
               "ok", "okay", "venga", "exacto", "efectivamente", "porfa"}

NEGATIVAS = {"no", "nop", "nunca", "jamas", "para", "cancela", "cancelalo",
             "cancelar", "anula", "olvidalo", "dejalo", "mejor", "espera"}


def solo_es_respuesta(frase):
    """¿Es la frase solo un "sí" o un "no", sin nada más?

    Sin esto, un "sí" suelto se enrutaba a completar_tarea, y "sí, hazlo"
    llegó a proponer cerrar_programa: un asentimiento perdido podía cerrarte
    una aplicación. Si no hay nada pendiente que confirmar, un monosílabo
    no debe mover nada.
    """
    palabras = re.sub(r"[^\w\s]", " ", _sin_tildes(frase)).split()
    if not palabras or len(palabras) > 3:
        return False
    if palabras[0] not in AFIRMATIVAS and palabras[0] not in NEGATIVAS:
        return False
    # "sí, apaga el ordenador" sí lleva orden dentro: eso no es un monosílabo
    return not any(p in VERBOS_TAREA or re.search(r"^(abre|cierra|apaga|"
                   r"reinicia|sube|baja|pon|quita|busca)", p) for p in palabras[1:])


# Comandos que manda el popup del correo. En una constante porque se
# miran en dos sitios (el bucle normal y el de interrupcion), y tenerlos
# escritos dos veces ya hizo que uno se quedara sin actualizar.
CMDS_BORRADOR = ("borrador_enviar", "borrador_cancelar", "borrador_destinatario")


def pide_dejarlo(frase):
    """¿Quiere abandonar el correo que se está montando a medias?

    Se exige que sea una frase CORTA: "déjalo" abandona, pero "dile que
    lo deje para mañana" es el texto del correo, no una cancelación.
    Contestar un paso con algo largo siempre es contenido.
    """
    b = _sin_tildes(frase or "").strip(" .,!¡?¿")
    if not b or len(b.split()) > 3:
        return False
    return bool(re.search(r"\b(dejalo|dejemoslo|cancela|cancelalo|olvidalo|"
                          r"olvidate|da igual|no importa|nada)\b", b))


def es_afirmacion(frase):
    """¿Ha dicho que sí a lo que se le acaba de preguntar?

    Se mira solo la PRIMERA palabra, que es la que decide: "sí, hazlo",
    "vale, adelante" y "sí por favor" son que sí; "no, déjalo" es que no.
    Ante cualquier otra cosa se entiende que no, porque apagar el
    ordenador equivocándose no tiene arreglo.
    """
    palabras = re.sub(r"[^\w\s]", " ", _sin_tildes(frase)).split()
    if not palabras:
        return False
    if palabras[0] in NEGATIVAS:
        return False
    return palabras[0] in AFIRMATIVAS


def pide_accion_sistema(frase):
    """¿Es una orden para el ordenador, o solo un comentario sobre él?

    Salió de la evaluación: "el PC se calienta mucho" proponía subir el
    volumen, y "va muy lento" reiniciar. Son quejas. Una orden lleva verbo
    de mando, o va envuelta en una petición ("puedes bajar el volumen").
    """
    t = _sin_tildes(frase)
    if es_pregunta(t) and not re.search(r"\b(puedes|podrias|me)\b", t):
        return False
    return any(re.search(v, t) for v in VERBOS_SISTEMA)


def pide_apagar(frase):
    """¿Está pidiendo apagar o reiniciar, o solo lo ha mencionado?

    Apagar es la única acción de la que no se vuelve: si había trabajo sin
    guardar, se pierde. Y Whisper se equivoca —en este proyecto se le ha
    visto oír "qué te harás" donde se dijo "qué tal has"—, así que no basta
    con que el modelo lo proponga: la frase tiene que contener de verdad un
    verbo de apagado en imperativo o en petición.
    """
    t = _sin_tildes(frase)
    if es_pregunta(t) and not re.search(r"\b(puedes|podrias|me)\b", t):
        return False          # "¿se apaga solo?" no es una orden
    return bool(re.search(
        r"\b(apaga|apagame|apagar|apague|reinicia|reiniciame|reiniciar|"
        r"reinicie|suspende|suspender)\b", t))


def prompt_router():
    """El prompt del router con lo que se sabe del usuario.

    Hace falta sobre todo para los correos: sin saber cómo se llama, el
    modelo los firmaba con "[nombre del usuario]", un hueco sin rellenar
    que se habría enviado tal cual.
    """
    datos = memoria.datos_conocidos()
    if not datos:
        return PROMPT_ROUTER
    return (PROMPT_ROUTER + "\n\nDATOS DEL USUARIO (para firmar correos y "
            "para saber de quién habla):\n" +
            "\n".join(f"- {memoria.como_frase(d)}" for d in datos) +
            "\nNunca dejes huecos como [nombre] en un correo: si no sabes "
            "algo, no lo pongas.")


PROMPT_REDACTOR = """Escribes los correos del usuario. Te dan un encargo y
devuelves SOLO el texto del correo, listo para enviar.

REGLAS
- Devuelve únicamente el cuerpo. Nada de "Asunto:", ni comillas, ni
  markdown, ni explicaciones tuyas, ni comentarios sobre lo que has hecho.
- Saludo, cuerpo y despedida. Breve: tres o cuatro frases como mucho.
- El tono lo marca el encargo. Si pide algo formal, trata de usted.
- NO INVENTES NADA que no esté en el encargo: ni fechas, ni horas, ni
  sitios, ni motivos, ni nombres de personas. Si el encargo no dice
  cuándo, el correo no dice cuándo.
- Firma con el nombre del usuario. Si no lo sabes, termina sin firma:
  no te inventes un nombre, y nunca escribas huecos como [nombre].
- Si el encargo ya viene redactado como un mensaje entero, respétalo y
  límitate a darle forma de correo.
- Son correos normales entre conocidos: escríbelos sin más.
- En español."""

# El modelo a veces contesta que no en vez de escribir. Meter eso en el
# cuerpo sería mandarle a alguien "Lo siento, pero no puedo cumplir con
# esa solicitud", así que se detecta y se vuelve a intentar.
# Lo que distingue una negativa de un correo NO es cómo empieza, sino de
# qué habla: una negativa habla de LA PETICIÓN ("no puedo cumplir con esa
# solicitud"), un correo habla con el destinatario. Mirando solo el
# principio se descartaban correos buenos: "Lo siento, Ana. Me retrasé y
# llegué tarde a la cena" empieza igual que una negativa y es el correo.
RECHAZO = re.compile(
    r"(?i)(no puedo (cumplir|ayudarte con (eso|esto)|generar|redactar|crear)|"
    r"no voy a (poder )?(escribir|redactar|generar)|"
    r"no (tengo|dispongo de) (permiso|la capacidad|la posibilidad)|"
    r"no estoy (autorizad|programad|capacitad)|"
    r"esa (solicitud|petici[oó]n)|con esa solicitud|"
    r"como (modelo de lenguaje|asistente de ia|una ia)\b|"
    r"i'?m sorry,? (but )?i (can'?t|cannot)|i cannot (fulfill|comply))")


def parece_rechazo(texto):
    """¿Ha contestado que no, en vez de escribir el correo?

    Además de la frase delatora se exige que sea corto y de una sola
    tirada: un correo de verdad tiene saludo y despedida y ocupa varias
    líneas, así que uno que mencione de pasada "no puedo ayudarte" se
    salva de que lo tomen por una negativa.
    """
    t = (texto or "").strip()
    if not t or len(t) > 220 or "\n" in t:
        return False
    return bool(RECHAZO.search(t))


def nombre_del_usuario():
    """Cómo se llama el usuario, para firmar los correos. "" si no consta.

    Se queda con el PRIMERO que se guardó. Si hay varios es que algo se
    apuntó mal —pasó: contar quién era la novia acabó guardado como el
    nombre del propio usuario— y el más viejo suele ser el bueno.
    """
    for dato in memoria.datos_conocidos():
        m = re.search(r"(?i)(?:me llamo|se llama|soy)\s+"
                      r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,20})", dato)
        if m:
            return m.group(1).capitalize()
    return ""


def saludo_para(destinatario, encargo):
    """El saludo con el que se le arranca la respuesta al modelo.

    Sale del encargo: si pide algo formal, se abre de usted. Los dos son
    válidos sin saber si quien recibe es hombre o mujer, que no consta.
    """
    quien = (destinatario or "").strip()
    if re.search(r"(?i)\bformal|\bseri[oa]|\bde usted|\beducad|\bprofesional",
                 encargo or ""):
        return f"Buenos días{', ' + quien if quien else ''}:\n\n"
    return f"Hola{' ' + quien if quien else ''},\n\n"


def redactar_correo(encargo, destinatario="", asunto=""):
    """Convierte un encargo hablado en el texto de un correo.

    El usuario dice "mándale un mensaje formal pidiéndole que venga a mi
    casa", no el correo palabra por palabra. Dictar un correo entero es
    incómodo y Whisper se come cosas; describir lo que quieres es lo
    natural. Esto lo escribe.

    Lo que salga va SIEMPRE al popup para revisarlo antes de enviarlo,
    así que un borrador flojo se arregla en dos segundos con el teclado.
    Devuelve el encargo tal cual si el modelo falla: mejor eso que nada.
    """
    # Aquí se le pasa SOLO el nombre, no todo lo que Jarvis sabe del
    # usuario. Para redactar no hace falta nada más que la firma, y
    # meterle la vida entera tenía dos efectos malos: colaba datos
    # personales en correos que no venían a cuento, y con ciertos temas
    # el modelo se cerraba en banda y contestaba "no puedo cumplir con
    # esa solicitud" en vez de escribir. Medido: 1 de 3 encargos salía
    # con todos los datos; 3 de 3 pasándole solo el nombre.
    sistema = PROMPT_REDACTOR
    quien = nombre_del_usuario()
    if quien:
        sistema += f"\n\nEl usuario se llama {quien}: firma con ese nombre."
    else:
        sistema += "\n\nNo sabes cómo se llama el usuario: termina sin firma."

    cabecera = (f"Para: {destinatario or 'un conocido'}\n"
                f"Asunto: {asunto or '(sin asunto)'}\n")

    # Dos intentos. El primero le deja elegir el registro: para un encargo
    # formal escribe "Estimada Ana" por su cuenta, y eso se pierde si se
    # le dicta el saludo.
    #
    # El segundo es la red de seguridad, y funciona porque le EMPIEZA la
    # respuesta: con "Hola Ana," ya puesto en su turno, no puede arrancar
    # con "lo siento, no puedo cumplir con esa solicitud". El modelo se
    # negaba en 1 de cada 4 encargos, al azar y sin que hubiera una frase
    # concreta que lo disparara. Medido con el arranque puesto: 6 de 6.
    saludo = saludo_para(destinatario, encargo)
    intentos = [
        (cabecera + f"Encargo: {encargo}", ""),
        (cabecera + f"Contenido que debe transmitir el correo: {encargo}", saludo),
    ]

    for n, (peticion, arranque) in enumerate(intentos, 1):
        mensajes = [{"role": "system", "content": sistema},
                    {"role": "user", "content": peticion}]
        if arranque:
            mensajes.append({"role": "assistant", "content": arranque})
        try:
            r = ollama.chat(
                model=MODELO_LLM, messages=mensajes,
                # Más bajo que en la conversación: aquí no se busca gracia,
                # sino que diga lo encargado y nada más.
                options={"num_ctx": NUM_CTX, "temperature": 0.2},
            )
            # Con arranque, el modelo devuelve solo la continuación
            texto = arranque + (r["message"]["content"] or "").strip()
        except Exception as e:
            print(f"[correo] no se pudo redactar: {e}")
            return encargo

        if parece_rechazo(texto):
            print(f"[correo] intento {n}: se negó ({texto[:50]!r})")
            continue
        limpio = limpiar_correo(texto)
        if limpio:
            return limpio

    # Los dos fallaron: se deja lo dicho tal cual. Queda soso, pero el
    # popup está delante y se arregla escribiendo.
    print("[correo] no hubo manera: se deja el encargo tal cual")
    return encargo


def limpiar_correo(texto):
    """Quita lo que el modelo añade de su cosecha y no es el correo."""
    # A veces contesta con el correo entre comillas, o precedido de
    # "Aquí tienes el correo:". Nada de eso se envía.
    texto = re.sub(r"(?im)^\s*(aqu[íi] tienes|te he escrito|este es el correo)"
                   r"[^\n:]*:\s*", "", texto).strip()
    texto = re.sub(r"(?im)^\s*(asunto|subject)\s*:.*$", "", texto).strip()
    texto = re.sub(r"^[\"“”'`]+|[\"“”'`]+$", "", texto).strip()
    # Markdown: se leería en voz alta y quedaría fatal por escrito
    texto = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", texto)
    # Huecos sin rellenar: "[nombre del usuario]" enviado tal cual es peor
    # que no firmar. Se quitan aunque el prompt diga que no los ponga.
    texto = re.sub(r"\[[^\]]{2,40}\]", "", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip(" ,\n")


def enrutar(pregunta):
    """¿Hace falta una herramienta? Devuelve (nombre, argumentos) o (None, None)."""
    r = ollama.chat(
        model=MODELO_LLM,
        messages=[{"role": "system", "content": prompt_router()},
                  {"role": "user", "content": pregunta}],
        tools=HERRAMIENTAS,
        options={"num_ctx": NUM_CTX, "temperature": 0.0},
    )
    llamadas = r.message.tool_calls or []
    if not llamadas:
        return None, None

    nombre = llamadas[0].function.name
    args = dict(llamadas[0].function.arguments)

    # Un "sí" o un "no" suelto no es una orden. Solo significa algo cuando
    # responde a una pregunta, y de eso se encarga el turno anterior.
    if solo_es_respuesta(pregunta):
        print(f"[router] descarto {nombre}: {pregunta!r} es solo un sí o un no")
        return None, None

    # "Abre el administrador de TAREAS" no es apuntar una tarea. La palabra
    # coincide y el router picaba: se apuntó "abrir el administrador de
    # tareas" en la agenda en vez de abrirlo. Si la frase empieza pidiendo
    # abrir algo, es abrir algo.
    if nombre in ("anadir_tarea", "listar_tareas") and re.match(
            r"^\s*(abre|abreme|abrir|abra|lanza|lanzame|ejecuta|arranca|pon)\b",
            _sin_tildes(pregunta)):
        objeto = re.sub(r"^\s*\w+\s*", "", pregunta.strip(), count=1)
        print(f"[router] {nombre} -> abrir_programa: {pregunta!r} pide abrir algo")
        return "abrir_programa", {"programa": objeto or pregunta}

    # Pedir que se borre algo NUNCA puede acabar creando una tarea nueva.
    # Es el fallo más desconcertante de todos: pides quitar el dentista y
    # te quedas con dos apuntes en vez de ninguno.
    if nombre == "anadir_tarea" and pide_borrar(pregunta):
        limpio = sin_verbo_borrar(args.get("texto") or pregunta)
        print(f"[router] anadir_tarea -> completar_tarea: {pregunta!r} pide borrar")
        return "completar_tarea", {"texto": limpio}

    # Red de seguridad: apuntar es lo único que deja rastro permanente en la
    # base de datos, así que es donde más molesta equivocarse.
    if nombre == "anadir_tarea":
        if not parece_encargo(pregunta):
            print(f"[router] descarto anadir_tarea: {pregunta!r} es una pregunta")
            return None, None
        motivo = tarea_vacia(args.get("texto", ""))
        if motivo:
            print(f"[router] descarto anadir_tarea: {motivo}")
            return None, None

    if nombre == "recordar_dato":
        if not parece_dato_personal(pregunta):
            print(f"[router] descarto recordar_dato: {pregunta!r} pide, no cuenta")
            return None, None
        # se mira lo que dijo el usuario Y lo que el modelo quiere guardar
        motivo = dato_vacio(args.get("dato", "")) or dato_vacio(pregunta)
        if motivo:
            print(f"[router] descarto recordar_dato: {motivo}")
            return None, None

    if nombre == "listar_tareas" and cuando_inventado(args.get("cuando", ""), pregunta):
        print(f"[router] quito cuando={args.get('cuando')!r}: no lo dijo el usuario")
        args["cuando"] = ""

    # Lo contrario del guard de arriba: el modelo a veces se DEJA el momento
    # cuando la pregunta lleva rodeo. "¿Qué planes tengo PARA el fin de
    # semana?" salía sin filtro, y sin filtro se contesta con toda la lista
    # pendiente: el examen del martes incluido. Se recupera de la frase
    # original, que es la única fuente que no depende de que el modelo copie.
    if nombre == "listar_tareas" and not (args.get("cuando") or "").strip():
        rescatado = momento_en(pregunta)
        if rescatado:
            print(f"[router] recupero cuando={rescatado!r}: estaba en la frase")
            args["cuando"] = rescatado

    # Ninguna acción del sistema se dispara con un comentario. "El PC se
    # calienta mucho" llegó a proponer subir el volumen, y "va lento" a
    # reiniciar: son quejas, no órdenes. Tiene que haber verbo de mando.
    if nombre == "control_sistema":
        accion = _sin_tildes(args.get("accion", ""))
        if not pide_accion_sistema(pregunta):
            print(f"[router] descarto {accion!r}: {pregunta!r} es un comentario")
            return None, None
        # Y apagar o reiniciar, además, exigen su verbo concreto
        if ("apagar" in accion or "reiniciar" in accion) and not pide_apagar(pregunta):
            print(f"[router] descarto {accion!r}: {pregunta!r} no lo pide claramente")
            return None, None

    if nombre == "buscar_en_web" and not merece_busqueda(pregunta):
        print(f"[router] descarto buscar_en_web: {pregunta!r} no pide nada actual")
        return None, None

    return nombre, args


# Momentos que se buscan en la frase cuando el router no puso ninguno.
# Cada par es (lo que se busca sin tildes, cómo se dice bien): la
# respuesta empieza con esto en voz alta, y "Fin de semana tienes..."
# sonaba a telegrama. De más largo a más corto, porque "próximo fin de
# semana" tiene que ganarle a "fin de semana" o filtraría por la
# semana equivocada.
FRASES_MOMENTO = [
    ("proximo fin de semana",  "el próximo fin de semana"),
    ("fin de semana que viene", "el fin de semana que viene"),
    ("fin de semana siguiente", "el próximo fin de semana"),
    ("proximo finde",          "el próximo fin de semana"),
    ("finde que viene",        "el fin de semana que viene"),
    ("fin de semana",          "el fin de semana"),
    ("finde",                  "el fin de semana"),
    ("pasado manana",          "pasado mañana"),
    ("manana por la manana",   "mañana por la mañana"),
    ("manana por la tarde",    "mañana por la tarde"),
    ("manana por la noche",    "mañana por la noche"),
    ("esta noche",             "esta noche"),
    ("esta tarde",             "esta tarde"),
    ("esta manana",            "esta mañana"),
    ("manana",                 "mañana"),
    ("hoy",                    "hoy"),
]


def destinatario_inventado(destinatario, pregunta):
    """¿El router se ha sacado el destinatario de la manga?

    El prompt del router lleva lo que Jarvis sabe del usuario, para que
    los correos los firme con su nombre de verdad. Efecto secundario:
    a "quiero mandar un correo electrónico", sin mencionar a nadie, le
    puso destinatario "su novia" — sacado de un dato guardado.

    La comprobación es tonta a propósito: si ninguna palabra del
    destinatario aparece en lo que se dijo, no lo dijo.
    """
    propuesto = set(re.findall(r"[a-z0-9]{3,}", _sin_tildes(destinatario or "")))
    if not propuesto:
        return False
    dicho = set(re.findall(r"[a-z0-9]{3,}", _sin_tildes(pregunta or "")))
    return not (propuesto & dicho)


def momento_en(pregunta):
    """Devuelve el momento que aparezca en la frase, o cadena vacía.

    Solo se usa cuando el router ya decidió que se consulta la agenda y
    encima dejó el filtro vacío: no decide nada por su cuenta, solo
    rellena un hueco que el modelo se dejó.
    """
    t = _sin_tildes(pregunta or "")
    for buscar, decir in FRASES_MOMENTO:
        if re.search(r"\b" + re.escape(buscar) + r"\b", t):
            return decir
    return ""


def ejecutar(nombre, args):
    """Ejecuta la herramienta. El resultado ya viene redactado para decirlo."""
    funcion = FUNCIONES.get(nombre)
    if not funcion:
        return None
    try:
        # Los argumentos vienen del modelo: pueden faltar o venir anidados.
        limpios = {k: (v if isinstance(v, str) else str(v))
                   for k, v in (args or {}).items() if v is not None}
        return funcion(**limpios)
    except Exception as e:
        print(f"Error en la herramienta {nombre}: {e}")
        return None


# ---------------------------------------------------------------
# SERVIDOR
# ---------------------------------------------------------------

app = FastAPI()
AQUI = Path(__file__).parent


# Three.js se sirve desde aquí, no desde un CDN: el asistente tiene que
# funcionar sin conexión, y una etiqueta <script src="https://..."> lo
# convertiría en mentira.
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")


def version_interfaz():
    """Huella de los ficheros que se envían al navegador.

    Se calcula en cada conexión, no al arrancar: así vale también si se
    toca el HTML con el servidor ya en marcha.
    """
    huella = hashlib.sha1()
    for f in (AQUI / "index.html", AQUI / "static" / "nucleo.js"):
        try:
            huella.update(f.read_bytes())
        except OSError:
            pass
    return huella.hexdigest()[:12]


@app.get("/")
async def raiz():
    # Sin no-cache, tras editar el HTML el navegador seguía sirviendo el
    # de su caché aunque se recargara, y parecía que el cambio no existía.
    return FileResponse(AQUI / "index.html",
                        headers={"Cache-Control": "no-store"})


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    micro = Microfono()
    # Una memoria por conexión. Antes era global, así que dos pestañas
    # abiertas compartían conversación y se pisaban los turnos.
    historial = [{"role": "system", "content": PROMPT_SISTEMA}]
    # Lo irreversible no se hace a la primera: se pregunta y se espera un
    # "sí". Aquí se recuerda qué quedó pendiente. Va por conexión, como el
    # historial: cada pestaña lleva su propia pregunta en el aire.
    #   tipo "sistema" -> apagar o reiniciar
    #   tipo "correo"  -> un email escrito y sin enviar
    pendiente = {"tipo": None, "datos": None}
    comandos = asyncio.Queue()
    tarea_receptor = None
    grabando = False
    tarea_medidor = None
    tarea_parciales = None
    tarea_limite = None
    tarea_silencio = None

    bucle_principal = asyncio.get_running_loop()

    async def enviar(**datos):
        try:
            await sock.send_text(json.dumps(datos))
        except Exception:
            pass

    def poner_voz_en_cola(muestras, frecuencia):
        """Manda un trozo de voz al navegador. Lo llama el hilo de voz.

        El hilo de voz no es asíncrono y no puede tocar el socket, así que
        el envío se programa en el bucle principal. Va en int16 con una
        cabecera de 4 bytes con la frecuencia: Piper sintetiza a 22050 y
        el navegador tiene que saberlo para no reproducirlo agudo.
        """
        pcm = (np.clip(muestras, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        paquete = struct.pack("<I", int(frecuencia)) + pcm
        try:
            asyncio.run_coroutine_threadsafe(sock.send_bytes(paquete),
                                             bucle_principal)
        except RuntimeError:
            pass          # el bucle ya no existe: la conexión se cerró

    async def decir_suelto(frase, etiqueta=None):
        """Dice algo que no contesta a nada hablado: los botones del panel."""
        await enviar(tipo="dicho", texto=frase, etiqueta=etiqueta)
        await enviar(tipo="estado", valor="hablando")
        await asyncio.to_thread(hablar, frase)
        await enviar(tipo="estado", valor="inactivo")

    async def decir_turno(frase, etiqueta=None, pregunta=None):
        """Dice una frase corta y cierra el turno, sin pasar por el modelo.

        Con `pregunta` cierra un turno de voz (y lo apunta en el historial).
        Sin ella la frase va suelta: es lo que hace falta cuando quien ha
        hablado es un botón del popup y no el micrófono.
        """
        if pregunta is None:
            await decir_suelto(frase, etiqueta)
            return
        if etiqueta:
            await enviar(tipo="herramienta", nombre=etiqueta)
        await enviar(tipo="token", texto=frase)
        await enviar(tipo="estado", valor="hablando")
        await asyncio.to_thread(hablar, frase)
        historial.append({"role": "user", "content": pregunta})
        historial.append({"role": "assistant", "content": frase})
        recortar_historial(historial)
        await enviar(tipo="fin_respuesta")
        await enviar(tipo="estado", valor="inactivo")

    async def avanzar_correo(borrador, pregunta=None):
        """Pide lo que le falte al correo, y abre el popup cuando esté.

        El orden no es capricho. Primero A QUIÉN, y eso se teclea en el
        popup: una dirección dictada que Whisper oye mal no se puede
        arreglar repitiéndola, porque la vuelve a oír mal. El asunto y el
        texto van por voz, que para eso sí acierta.

        Lo que ya venga dicho no se vuelve a preguntar: "mándale un correo
        a Ana diciéndole que llego tarde" no pasa por ningún paso.
        """
        agenda = [{"nombre": c["nombre"], "email": c["email"]}
                  for c in correo.cargar_contactos()]

        if not borrador.get("email"):
            pendiente["tipo"] = "correo_paso"
            pendiente["datos"] = dict(borrador, paso="destinatario")
            await enviar(tipo="borrador", modo="destinatario", contactos=agenda,
                         destinatario="", asunto=borrador.get("asunto", ""),
                         mensaje=borrador.get("mensaje", ""))
            await decir_turno("¿A quién se lo mando?", "correo", pregunta)
            return

        if not borrador.get("asunto"):
            pendiente["tipo"] = "correo_paso"
            pendiente["datos"] = dict(borrador, paso="asunto")
            await decir_turno(f"Vale, para {borrador['nombre']}. "
                              "¿De qué se trata?", "correo", pregunta)
            return

        if not borrador.get("mensaje"):
            pendiente["tipo"] = "correo_paso"
            pendiente["datos"] = dict(borrador, paso="mensaje")
            await decir_turno("¿Y qué le digo?", "correo", pregunta)
            return

        # Ya está entero: se abre para revisarlo con el teclado antes de
        # mandarlo. Un correo enviado a quien no era no se recupera.
        pendiente["tipo"] = "correo"
        pendiente["datos"] = dict(borrador)
        await enviar(tipo="borrador", modo="completo", contactos=agenda,
                     destinatario=borrador["nombre"], email=borrador["email"],
                     asunto=borrador["asunto"], mensaje=borrador["mensaje"])
        await decir_turno("Te lo he preparado. Míralo y dime si lo mando.",
                          "borrador", pregunta)

    def resolver_destino(escrito):
        """Convierte lo tecleado en (dirección, nombre para decir).

        El destinatario NO se coge tal cual de lo que manda el navegador:
        o es alguien de la agenda, o es algo con forma de dirección.
        Devuelve (None, None) si no es ni una cosa ni la otra.
        """
        escrito = (escrito or "").strip()
        contacto = correo.buscar_contacto(escrito)
        if contacto:
            return contacto["email"], contacto["nombre"]
        if correo.es_direccion(escrito):
            return escrito, escrito
        return None, None

    async def resolver_borrador(mensaje):
        """Botones del popup: elegir destinatario, enviar, o descartar.

        Solo hace algo si ESTA conexión tiene un correo a medias, y eso
        es lo que impide que alguien de la red se monte uno desde cero:
        el servidor escucha en 0.0.0.0, pero `pendiente` va por conexión,
        así que otra pestaña o otro equipo tiene el suyo vacío y estos
        comandos le caen en saco roto.
        """
        if pendiente["tipo"] not in ("correo", "correo_paso"):
            return
        tipo, datos = pendiente["tipo"], pendiente["datos"]
        pendiente["tipo"], pendiente["datos"] = None, None
        cmd = mensaje.get("cmd")

        def dejar_abierto():
            """Devuelve el borrador a su sitio: lo escrito no se pierde."""
            pendiente["tipo"], pendiente["datos"] = tipo, datos

        if cmd == "borrador_cancelar":
            print("[correo] descartado desde el popup")
            await enviar(tipo="borrador_cerrar")
            await decir_suelto("Vale, lo dejo.", "correo")
            return

        # Primer paso: solo el destinatario. Se cierra el popup y se sigue
        # preguntando por voz, que es lo cómodo para el asunto y el texto.
        if cmd == "borrador_destinatario":
            destino, visible = resolver_destino(mensaje.get("destinatario"))
            if not destino:
                dejar_abierto()
                await enviar(tipo="borrador_error",
                             texto="Elige a alguien de la agenda, o escribe "
                                   "una dirección entera.")
                return
            datos.pop("paso", None)
            datos["email"], datos["nombre"] = destino, visible
            await enviar(tipo="borrador_cerrar")
            await avanzar_correo(datos)
            return

        # Último paso: mandar lo que hay escrito en el popup
        asunto = (mensaje.get("asunto") or "").strip() or datos.get("asunto") or "Sin asunto"
        cuerpo = (mensaje.get("mensaje") or "").strip()
        if not cuerpo:
            dejar_abierto()
            await enviar(tipo="borrador_error", texto="El correo está vacío.")
            return

        destino, visible = resolver_destino(mensaje.get("destinatario"))
        if not destino:
            dejar_abierto()
            await enviar(tipo="borrador_error",
                         texto="Elige a quién va, o escribe una dirección entera.")
            return

        print(f"[correo] enviado desde el popup -> {destino}")
        ok, frase = await asyncio.to_thread(correo.enviar, destino, asunto, cuerpo)
        if not ok:
            dejar_abierto()
            await enviar(tipo="borrador_error", texto=frase)
            return
        await enviar(tipo="borrador_cerrar")
        await decir_suelto(frase, f"correo a {visible}")

    async def medidor():
        """Manda nivel y espectro para que el anillo reaccione al micro."""
        while True:
            await enviar(tipo="nivel",
                         valor=round(micro.nivel, 3),
                         bandas=micro.espectro())
            await asyncio.sleep(0.05)

    async def parciales():
        """Re-transcribe solo los últimos VENTANA_PARCIAL_S segundos.

        Si se re-transcribiera el audio entero cada vez, cada vuelta costaría
        más que la anterior (crece con la duración de la grabación al cuadrado
        contando el total de trabajo). Con la ventana fija el coste por vuelta
        es constante.
        """
        while True:
            await asyncio.sleep(MS_PARCIAL / 1000)
            audio = micro.audio_reciente(VENTANA_PARCIAL_S)
            if audio is None or len(audio) < FRECUENCIA * 0.4:
                continue
            try:
                texto = await asyncio.to_thread(transcribir, audio, stt_rapido, "parcial")
            except Exception as e:
                # Sin esto, una excepción aquí mataba la tarea en silencio
                # (asyncio se traga el error de una tarea que nadie espera)
                # y el texto en vivo dejaba de aparecer sin explicación.
                print(f"Error en transcripción parcial: {e}")
                return
            if texto:
                await enviar(tipo="parcial", texto=texto)

    async def responder(pregunta):
        """Llama al LLM en streaming y va hablando frase a frase.

        ollama.chat(stream=True) devuelve un generador SÍNCRONO y bloqueante:
        cada `next()` espera a la red. Antes se hacía list(...) sobre él dentro
        de un hilo, lo que consumía la respuesta ENTERA antes de devolver nada
        al bucle de eventos — de ahí que no hubiera streaming real.
        Aquí el generador se consume en un hilo aparte, y cada trozo se mete
        en una asyncio.Queue mediante call_soon_threadsafe (la única forma
        correcta de tocar una asyncio.Queue desde fuera del hilo del bucle de
        eventos). El bucle de eventos va sacando trozos de la cola según
        llegan, así que puede hablar la primera frase sin esperar al resto.
        """
        await enviar(tipo="estado", valor="pensando")

        async def decir_y_cerrar(frase, etiqueta=None):
            """Dice una frase corta y cierra el turno. Sin pasar por el modelo."""
            await decir_turno(frase, etiqueta, pregunta)

        # ¿Se le acababa de preguntar si apagar o reiniciar? Entonces este
        # turno es la respuesta, y se resuelve aquí SIN pasar por el router:
        # el modelo enrutaría un "sí" suelto como charla y se perdería.
        if pendiente["tipo"]:
            tipo, datos = pendiente["tipo"], pendiente["datos"]
            pendiente["tipo"], pendiente["datos"] = None, None
            dijo_si = es_afirmacion(pregunta)

            if tipo == "sistema":
                if dijo_si:
                    print(f"[sistema] confirmado: {datos}")
                    resultado = await asyncio.to_thread(
                        sistema.ejecutar_accion, datos)
                    await decir_y_cerrar(resultado, "control sistema")
                else:
                    print(f"[sistema] NO confirmado: {datos} descartado")
                    verbo = "reinicio" if datos == "reiniciar" else "apago"
                    await decir_y_cerrar(f"Vale, no {verbo} nada.")
                return

            # Se le acaba de preguntar el asunto, el texto o a quién: lo
            # que ha dicho ES la respuesta, no una orden nueva. No pasa
            # por el router, que enrutaría "que llego tarde a la cena"
            # como una tarea que apuntar.
            if tipo == "correo_paso":
                if pide_dejarlo(pregunta):
                    print("[correo] abandonado a medias")
                    await enviar(tipo="borrador_cerrar")
                    await decir_y_cerrar("Vale, lo dejo.", "correo")
                    return
                paso = datos.pop("paso", "")
                dicho = pregunta.strip()
                if paso == "asunto":
                    # El asunto es una línea: sin punto final y sin más
                    datos["asunto"] = dicho.rstrip(" .").strip()
                elif paso == "mensaje":
                    # Lo dicho es un ENCARGO, no el texto: "mándale algo
                    # formal pidiéndole que venga a casa". Lo redacta el
                    # modelo, y luego se revisa en el popup.
                    await enviar(tipo="estado", valor="pensando")
                    t0 = time.monotonic()
                    datos["mensaje"] = await asyncio.to_thread(
                        redactar_correo, dicho, datos.get("nombre", ""),
                        datos.get("asunto", ""))
                    print(f"[correo] redactado en {time.monotonic()-t0:.1f}s")
                elif paso == "destinatario":
                    # Contestó hablando en vez de por el popup
                    destino, visible = resolver_destino(dicho)
                    if not destino:
                        pendiente["tipo"] = "correo_paso"
                        pendiente["datos"] = dict(datos, paso="destinatario")
                        await decir_y_cerrar(
                            "No tengo a esa persona en la agenda. "
                            "Escríbelo en el recuadro.", "correo")
                        return
                    datos["email"], datos["nombre"] = destino, visible
                await avanzar_correo(datos, pregunta)
                return

            if tipo == "correo":
                if not dijo_si:
                    print("[correo] NO confirmado: no se envia")
                    await enviar(tipo="borrador_cerrar")
                    await decir_y_cerrar("Vale, no lo mando.")
                    return
                # Decir "sí" manda lo que hay escrito en el borrador. Si le
                # falta el destinatario no se puede: hay que elegirlo, y eso
                # se hace en el panel, no hablando.
                if not datos.get("email"):
                    pendiente["tipo"], pendiente["datos"] = "correo", datos
                    await decir_y_cerrar(
                        "Antes tienes que elegir a quién se lo mando.", "borrador")
                    return
                print(f"[correo] confirmado por voz -> {datos['email']}")
                ok, frase = await asyncio.to_thread(
                    correo.enviar, datos["email"], datos["asunto"],
                    datos["mensaje"])
                await enviar(tipo="borrador_cerrar")
                await decir_y_cerrar(frase, "correo")
                return

        # Primero el router: ¿esto va de tareas o del reloj?
        try:
            t_ruta = time.monotonic()
            nombre, args = await asyncio.to_thread(enrutar, pregunta)
            print(f"[router] {time.monotonic()-t_ruta:.1f}s -> {nombre or 'conversación'}")
        except Exception as e:
            print(f"Error en el router: {e}")
            nombre, args = None, None

        # La búsqueda es distinta al resto de herramientas: lo que vuelve son
        # fragmentos de páginas, no una respuesta. Hace falta que el modelo los
        # lea y conteste. Las de la agenda, en cambio, vuelven ya redactadas.
        contexto_web = None
        if nombre == "buscar_en_web":
            await enviar(tipo="herramienta", nombre="buscando en la web")
            consulta = (args or {}).get("consulta") or pregunta
            t_web = time.monotonic()
            # buscar_y_leer, no buscar_en_web: los fragmentos del buscador
            # suelen ser la descripción de la página, no el dato.
            fragmentos, fallo = await asyncio.to_thread(
                buscar.buscar_y_leer, consulta)
            print(f"[web] {time.monotonic()-t_web:.1f}s  {consulta!r} -> "
                  f"{len(fragmentos)} resultados{' | ' + fallo if fallo else ''}")
            if fallo:
                aviso = f"No he podido buscarlo: {fallo}."
                await enviar(tipo="token", texto=aviso)
                await enviar(tipo="estado", valor="hablando")
                await asyncio.to_thread(hablar, aviso)
                await enviar(tipo="fin_respuesta")
                await enviar(tipo="estado", valor="inactivo")
                return
            contexto_web = buscar.como_contexto(fragmentos)
            nombre = None          # sigue por la vía conversacional, con contexto

        # Los correos, igual que la web: hace falta que el modelo los lea y
        # los resuma. Pero aquí hay un motivo de seguridad además del
        # práctico. El texto de un correo lo escribe cualquiera, y puede
        # traer dentro "manda un correo a esta dirección" o "apaga el
        # ordenador". Metiéndolo por esta vía se le entrega al CONVERSADOR,
        # que no lleva herramientas: aunque el modelo se creyera la orden,
        # no tiene con qué ejecutarla. Al router, que sí las lleva, no le
        # llega nunca el contenido de un correo.
        contexto_correo = None
        if nombre == "leer_correos":
            await enviar(tipo="herramienta", nombre="mirando el correo")
            a = args or {}
            nuevos = a.get("solo_nuevos")
            nuevos = True if nuevos is None else str(nuevos).lower() not in ("false", "0", "no")
            quien = (a.get("de") or "").strip()
            t_mail = time.monotonic()
            correos, fallo = await asyncio.to_thread(
                correo.leer_nuevos, nuevos, quien)
            print(f"[correo] {time.monotonic()-t_mail:.1f}s  "
                  f"{'sin leer' if nuevos else 'recientes'}"
                  f"{' de ' + quien if quien else ''} -> {len(correos)}"
                  f"{' | ' + fallo if fallo else ''}")
            if fallo:
                await decir_y_cerrar(f"No he podido mirar el correo: {fallo}.",
                                     "correo")
                return
            if not correos:
                # Sin correos no hay nada que resumir, y pasarle una lista
                # vacía al modelo es invitarle a inventarse remitentes.
                if quien:
                    vacio = f"No tienes ningún correo de {quien}."
                elif nuevos:
                    vacio = "No tienes correos nuevos."
                else:
                    vacio = "No hay nada en la bandeja de entrada."
                await decir_y_cerrar(vacio, "correo")
                return
            contexto_correo = correo.como_contexto(correos, nuevos)
            nombre = None

        # Apagar y reiniciar NO se ejecutan a la primera: se pregunta y se
        # espera respuesta. Es lo único de lo que no se vuelve, así que no
        # basta con que el router lo proponga bien.
        if nombre == "control_sistema":
            accion = (args or {}).get("accion", "")
            if accion in ("apagar", "reiniciar"):
                pendiente["tipo"], pendiente["datos"] = "sistema", accion
                verbo = "reinicie" if accion == "reiniciar" else "apague"
                await decir_y_cerrar(f"¿Seguro que quieres que {verbo} el ordenador?",
                                     "confirmar")
                return

        # Un correo tampoco se manda a la primera: se lee entero en voz alta
        # y se espera un "sí". Mandarlo a quien no era no tiene arreglo.
        if nombre == "enviar_correo":
            a = args or {}
            if not correo.cargar_contactos():
                await decir_y_cerrar(
                    "No tengo la agenda de contactos preparada todavía.",
                    "correo")
                return

            pedido = (a.get("destinatario") or "").strip()
            # Un destinatario que no salió de la boca del usuario no vale:
            # a "quiero mandar un correo" el router propuso "su novia",
            # sacado de los datos guardados. Se ignora y se pregunta.
            if pedido and destinatario_inventado(pedido, pregunta):
                print(f"[correo] descarto destinatario {pedido!r}: no lo dijo")
                pedido = ""
            contacto = correo.buscar_contacto(pedido) if pedido else None

            mensaje = (a.get("mensaje") or "").strip()
            # Aunque se le diga que no, a veces deja "[nombre del usuario]".
            # Enviarlo con el hueco puesto quedaría fatal, así que se quita.
            mensaje = re.sub(r"\[[^\]]{2,40}\]", "", mensaje)
            mensaje = re.sub(r"\s+([,.])", r"\1", mensaje).strip(" ,\n")
            # El modelo a veces describe el encargo en vez de redactarlo:
            # "El usuario pide que mandes un correo electrónico". Eso no es
            # un correo, así que se tira y se pregunta qué decir.
            if re.match(r"(?i)\s*(el|la)\s+usuari[oa]\b", mensaje):
                print(f"[correo] descarto mensaje {mensaje[:40]!r}: es una descripción")
                mensaje = ""

            await avanzar_correo({
                "email": contacto["email"] if contacto else "",
                "nombre": contacto["nombre"] if contacto else "",
                "asunto": (a.get("asunto") or "").strip(),
                "mensaje": mensaje,
            }, pregunta)
            return

        if nombre:
            resultado = await asyncio.to_thread(ejecutar, nombre, args)
            if resultado:
                # La respuesta de una herramienta ya viene redactada y es
                # exacta. No se la pasa al modelo para que la reformule:
                # se diría igual de bien y podría cambiar una hora o una fecha.
                await enviar(tipo="herramienta", nombre=nombre)
                await enviar(tipo="token", texto=resultado)
                await enviar(tipo="estado", valor="hablando")
                await asyncio.to_thread(hablar, resultado)
                historial.append({"role": "user", "content": pregunta})
                historial.append({"role": "assistant", "content": resultado})
                recortar_historial(historial)
                await enviar(tipo="fin_respuesta")
                await enviar(tipo="estado", valor="inactivo")
                return
            # Si la herramienta falla, se sigue como conversación normal

        # La fecha se refresca cada turno: el asistente puede llevar horas
        # abierto y haber cruzado la medianoche.
        historial[0] = {"role": "system", "content": prompt_con_fecha()}

        material = contexto_web or contexto_correo
        if material:
            # El material va en el turno del usuario, no en el prompt de
            # sistema: así se va solo cuando el historial se recorta y no
            # contamina las preguntas siguientes. Que un correo leído hace
            # diez turnos siga influyendo sería un problema, no una ventaja.
            historial.append({"role": "user",
                              "content": f"{material}\n\nPREGUNTA: {pregunta}"})
        else:
            historial.append({"role": "user", "content": pregunta})
        completa = ""
        buffer_frase = ""
        primera = True

        bucle = asyncio.get_running_loop()
        cola = asyncio.Queue()
        FIN = object()

        t0 = time.monotonic()
        primer_token = [None]

        def producir():
            try:
                flujo = ollama.chat(
                    model=MODELO_LLM,
                    messages=historial,
                    stream=True,
                    options={"num_ctx": NUM_CTX, "temperature": TEMPERATURA},
                )
                for parte in flujo:
                    trozo = parte["message"]["content"]
                    if trozo:
                        bucle.call_soon_threadsafe(cola.put_nowait, trozo)
            except Exception as e:
                bucle.call_soon_threadsafe(cola.put_nowait, ("__error__", str(e)))
            finally:
                bucle.call_soon_threadsafe(cola.put_nowait, FIN)

        threading.Thread(target=producir, daemon=True).start()

        error = None
        while True:
            trozo = await cola.get()
            if trozo is FIN:
                break
            if isinstance(trozo, tuple) and trozo[0] == "__error__":
                error = trozo[1]
                break

            if primer_token[0] is None:
                primer_token[0] = time.monotonic() - t0
                print(f"[llm] primer token en {primer_token[0]:.1f}s")

            completa += trozo
            buffer_frase += trozo
            await enviar(tipo="token", texto=trozo)

            # Al cerrar una frase, la manda a la voz sin esperar al resto
            if frase_terminada(buffer_frase):
                if primera:
                    await enviar(tipo="estado", valor="hablando")
                    primera = False
                await asyncio.to_thread(hablar, buffer_frase)
                buffer_frase = ""

        if error:
            # Un fallo de Ollama (modelo no descargado, servicio caído, etc.)
            # ya no debe tumbar la conexión entera: se avisa a la interfaz,
            # se quita del historial la pregunta que quedó sin responder,
            # y se vuelve a inactivo para poder seguir usando el asistente.
            print(f"Error al hablar con Ollama: {error}")
            historial.pop()
            await enviar(tipo="error", texto="No he podido pensar la respuesta.")
            await enviar(tipo="estado", valor="inactivo")
            return

        if buffer_frase.strip():
            if primera:
                await enviar(tipo="estado", valor="hablando")
            await asyncio.to_thread(hablar, buffer_frase)

        historial.append({"role": "assistant", "content": completa})
        recortar_historial(historial)
        await enviar(tipo="fin_respuesta")
        await enviar(tipo="estado", valor="inactivo")

    async def parar_grabacion():
        nonlocal grabando
        grabando = False
        for t in (tarea_medidor, tarea_parciales, tarea_limite, tarea_silencio):
            if t:
                t.cancel()
        await enviar(tipo="nivel", valor=0)

        audio = micro.parar()
        if audio is None or len(audio) < FRECUENCIA * 0.5:
            await enviar(tipo="descartar")
            await enviar(tipo="estado", valor="inactivo")
            return

        await enviar(tipo="estado", valor="transcribiendo")
        try:
            texto = await asyncio.to_thread(transcribir, audio, stt_bueno, "final")
        except Exception as e:
            # Antes, un fallo aquí reventaba el handler entero del WebSocket
            # y la interfaz solo mostraba "sin servidor", sin decir por qué.
            print(f"Error al transcribir: {e}")
            await enviar(tipo="error", texto="No he podido entender el audio.")
            await enviar(tipo="estado", valor="inactivo")
            return

        if not texto:
            await enviar(tipo="descartar")
            await enviar(tipo="estado", valor="inactivo")
            return

        await enviar(tipo="usuario", texto=texto)
        await responder(texto)

    async def vigilar_limite():
        """Corta sola la grabación a los MAX_GRABACION_S segundos."""
        while True:
            await asyncio.sleep(0.5)
            if micro.segundos_grabados() >= MAX_GRABACION_S:
                await parar_grabacion()
                return

    async def vigilar_silencio():
        """Envía solo cuando dejas de hablar, sin pulsar espacio otra vez.

        Mira los últimos segundos con Silero (el mismo VAD que trae dentro
        faster-whisper, así que no hace falta nada nuevo) y mide cuánto
        silencio hay DESPUÉS de lo último que dijiste. Cuesta entre 4 y 30
        milisegundos, así que se puede consultar cuatro veces por segundo.

        No corta hasta haberte oído: si pulsas y te quedas pensando, espera.
        """
        if not CORTE_POR_SILENCIO:
            return
        while True:
            await asyncio.sleep(0.25)
            audio = micro.audio_reciente(VAD_VENTANA_S)
            if audio is None or len(audio) < FRECUENCIA * 0.8:
                continue
            try:
                tramos = await asyncio.to_thread(
                    get_speech_timestamps, audio, OPCIONES_VAD)
            except Exception as e:
                print(f"Error en el detector de voz: {e}")
                return
            if not tramos:
                continue                      # todavía no ha hablado nadie

            habla = sum(t["end"] - t["start"] for t in tramos) / FRECUENCIA
            cola = (len(audio) - tramos[-1]["end"]) / FRECUENCIA
            if habla >= MIN_HABLA_S and cola >= SILENCIO_CORTE_S:
                print(f"[vad] corte por silencio: {habla:.1f}s de voz, "
                      f"{cola:.1f}s de silencio")
                await parar_grabacion()
                return

    try:
        # La huella de la interfaz va lo primero. Al reiniciar el servidor,
        # la página NO se recarga: el WebSocket se cae, reconecta solo, y la
        # pestaña sigue con el JavaScript de antes. Todo parece funcionar
        # —el servidor manda bien sus mensajes— pero los nuevos los ignora
        # sin dar ningún error. Pasó con el popup del correo: el servidor
        # decía "¿a quién se lo mando?" y el popup no salía por ningún lado.
        await enviar(tipo="version", valor=version_interfaz())

        await enviar(tipo="estado", valor="inactivo")

        # Nada más entrar, cuenta lo que hay para hoy. Es la diferencia entre
        # una libreta (hay que ir a mirarla) y algo que de verdad te recuerda
        # las cosas. Si no hay nada apuntado, no dice nada.
        resumen = await asyncio.to_thread(memoria.resumen_del_dia)
        if resumen:
            await enviar(tipo="saludo", texto=resumen)
            await enviar(tipo="estado", valor="hablando")
            await asyncio.to_thread(hablar, resumen)
            await enviar(tipo="fin_respuesta")
            await enviar(tipo="estado", valor="inactivo")

        # Una tarea aparte no deja NUNCA de leer el socket. Antes el bucle
        # principal hacía el receive él mismo, así que mientras el asistente
        # pensaba o hablaba nadie leía: las pulsaciones se acumulaban y se
        # ejecutaban al terminar, provocando grabaciones fantasma.
        FIN = object()

        async def receptor():
            try:
                while True:
                    mensaje = await sock.receive()
                    if mensaje.get("type") == "websocket.disconnect":
                        break
                    crudo = mensaje.get("bytes")
                    if crudo is not None:
                        # El audio NO pasa por la cola de comandos: llegan
                        # decenas de trozos por segundo y taparían las
                        # pulsaciones, que es justo lo que hay que atender
                        # rápido para poder interrumpirle. Va directo.
                        if isinstance(micro, MicrofonoRemoto):
                            micro.alimentar(crudo)
                        continue
                    texto = mensaje.get("text")
                    if texto is not None:
                        await comandos.put(json.loads(texto))
            except Exception:
                pass
            await comandos.put(FIN)

        tarea_receptor = asyncio.create_task(receptor())

        while True:
            mensaje = await comandos.get()
            if mensaje is FIN:
                break
            # El navegador avisa de que tiene micrófono propio (móvil, o
            # cualquier pestaña servida por https). A partir de aquí el
            # audio entra y sale por él, no por la tarjeta de sonido.
            if mensaje.get("cmd") == "micro_navegador":
                if not isinstance(micro, MicrofonoRemoto):
                    micro.parar()
                    micro = MicrofonoRemoto()
                salida_voz_a(poner_voz_en_cola)
                # Se imprime lo que el navegador ha negociado de verdad. Sin
                # esto, "desde el móvil se entiende mal" solo se puede
                # investigar adivinando.
                print(f"[audio] micrófono y voz por el navegador "
                      f"| contexto {mensaje.get('contexto_hz')} Hz "
                      f"| pista {mensaje.get('ajustes')}")
                print(f"[audio] {mensaje.get('agente', '')}")
                await enviar(tipo="audio_navegador", valor=True)
                continue

            if mensaje.get("cmd") in CMDS_BORRADOR:
                await resolver_borrador(mensaje)
                continue
            if mensaje.get("cmd") != "alternar":
                continue

            if not grabando:
                grabando = True
                permitir_voz()
                micro.empezar()
                await enviar(tipo="estado", valor="escuchando")
                tarea_medidor = asyncio.create_task(medidor())
                tarea_parciales = asyncio.create_task(parciales())
                tarea_limite = asyncio.create_task(vigilar_limite())
                tarea_silencio = asyncio.create_task(vigilar_silencio())
                continue

            # Se procesa el turno como tarea, vigilando a la vez si llega
            # otra pulsación: eso es lo que permite interrumpirle.
            grabando = False
            tarea_turno = asyncio.create_task(parar_grabacion())
            tarea_cmd = asyncio.create_task(comandos.get())

            hechas, _ = await asyncio.wait(
                {tarea_turno, tarea_cmd},
                return_when=asyncio.FIRST_COMPLETED)

            if tarea_cmd in hechas:
                llegado = tarea_cmd.result()
                # Los botones del borrador no son una interrupción: si se
                # pulsan mientras aún está diciendo "te lo he preparado",
                # se le deja acabar y se atienden después. Tratarlos como
                # una pulsación de micro habría descartado el correo.
                if (llegado is not FIN and isinstance(llegado, dict)
                        and llegado.get("cmd") in CMDS_BORRADOR):
                    await tarea_turno
                    await resolver_borrador(llegado)
                    continue
                # Pulsó mientras pensaba o hablaba: se le calla en el acto
                cortar_voz()
                # Y el navegador tira lo que le quede en el buffer: si no,
                # seguiria oyendose la frase por el movil despues de callarle
                await enviar(tipo="voz_corta")
                tarea_turno.cancel()
                try:
                    await tarea_turno
                except asyncio.CancelledError:
                    pass
                await enviar(tipo="fin_respuesta")
                await enviar(tipo="estado", valor="inactivo")
                print("[voz] interrumpido por el usuario")
                # Callarle ya es la acción: esa pulsación no graba nada más.
                if tarea_cmd.result() is FIN:
                    break
            else:
                tarea_cmd.cancel()
                try:
                    await tarea_cmd
                except asyncio.CancelledError:
                    pass

    except WebSocketDisconnect:
        pass
    finally:
        for t in (tarea_medidor, tarea_parciales, tarea_limite,
                  tarea_silencio, tarea_receptor):
            if t:
                t.cancel()
        cortar_voz()          # si se va con el asistente hablando, que calle
        permitir_voz()        # y que la próxima conexión pueda hablar
        # La salida de voz es global: si esta conexion la habia desviado al
        # navegador, hay que devolverla o la siguiente se quedaria muda.
        salida_voz_a(None)
        micro.parar()


def ip_en_la_red():
    """La IP de este equipo en la red local, para poder decirla."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No se conecta a nada: solo se pregunta al sistema por qué
        # interfaz saldría el tráfico, y de ahí sale la IP buena.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "tu-ip-local"
    finally:
        s.close()


if __name__ == "__main__":
    # Por defecto solo escucha en este ordenador.
    #
    # Antes escuchaba en 0.0.0.0, o sea en TODAS las interfaces: cualquiera
    # en la misma wifi podía abrir la interfaz y usar Jarvis entero, sin
    # contraseña. Pulsar el micro, apagar el ordenador, mandar correos
    # desde la cuenta de Gmail del usuario. En casa da igual; en la
    # universidad o en una cafetería, no.
    #
    # Con --red vuelve a abrirse, que es lo que hace falta para verlo en
    # el móvil o enseñárselo a alguien. Se avisa por pantalla de lo que
    # implica, para que sea una decisión y no un descuido.
    abierto = "--red" in sys.argv
    host = "0.0.0.0" if abierto else "127.0.0.1"
    ssl = {}

    if abierto:
        # Por la red hace falta HTTPS, y no por gusto: el micrófono del
        # navegador (getUserMedia) solo existe en "contexto seguro". Por
        # http:// desde el móvil la API ni siquiera aparece, así que no
        # habría forma de hablarle. localhost sí cuenta como seguro, por
        # eso en local se sigue usando http y nadie ve ningún aviso.
        import certificado
        if not certificado.existe():
            print()
            print("  Falta el certificado HTTPS, y sin él el móvil no puede")
            print("  usar el micrófono. Se crea una vez con:")
            print()
            print("      python certificado.py")
            print()
            sys.exit(1)

        ssl = {"ssl_keyfile": str(certificado.CLAVE),
               "ssl_certfile": str(certificado.CERT)}
        dias = certificado.caduca_en()
        if dias is not None and dias < 15:
            print(f"\n  Aviso: el certificado caduca en {dias} días."
                  "  Renuévalo con: python certificado.py")

        print()
        print("  " + "!" * 62)
        print("  ABIERTO A LA RED LOCAL")
        print(f"  Desde el móvil:  https://{ip_en_la_red()}:8000")
        print()
        print("  La primera vez el móvil avisará de que la conexión no es")
        print("  privada: el certificado lo firma tu PC y no hay autoridad")
        print("  que pueda certificar una IP privada. Continúa y acepta.")
        print()
        print("  Cualquiera en esta wifi puede usar Jarvis: no hay")
        print("  contraseña. Podría apagarte el ordenador o mandar")
        print("  correos con tu cuenta. Úsalo solo en una red de fiar.")
        print("  " + "!" * 62)
    else:
        print("\n  Abre http://localhost:8000")
        print("  (solo desde este ordenador. Para el móvil: python servidor.py --red)")
    print()

    uvicorn.run(app, host=host, port=8000, log_level="warning", **ssl)
