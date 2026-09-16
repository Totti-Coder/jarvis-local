"""Hablar: sintesis con Piper y reproduccion interrumpible.

La voz vive en su propio hilo porque SAPI de Windows lo exige, y porque
sintetizar bloquea: haciendolo en el bucle de asyncio se congelaria la
interfaz mientras habla.

El audio puede salir por los altavoces del PC o por el navegador (cuando
le hablas desde el movil). Lo decide salida_voz_a().
"""

import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd

from ajustes import AQUI_VOCES, VELOCIDAD_VOZ, VOZ_PIPER


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
    """Alternativa para cuando no hay SAPI (Linux, macOS).

    El import va dentro a proposito: es el ultimo recurso de los tres, y
    en Windows no se llega nunca aqui. Arriba obligaria a instalarlo para
    no usarlo.
    """
    import pyttsx3

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
