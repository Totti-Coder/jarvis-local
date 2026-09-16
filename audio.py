"""De donde sale el audio que se transcribe.

Dos fuentes con el MISMO interfaz: el microfono del ordenador, que
captura Python con sounddevice, y el del navegador, que llega por
WebSocket desde el movil. Todo lo que consume audio —el VAD, las
transcripciones parciales, el anillo de la interfaz— habla con ese
interfaz y no sabe cual de las dos tiene delante.
"""

import threading
import time

import numpy as np
import sounddevice as sd

from ajustes import FRECUENCIA


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
