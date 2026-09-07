"""Prueba la cadena completa sin micro: audio -> Whisper -> Ollama -> voz.

Comprueba de una vez las cuatro etapas, sin necesidad de micrófono.
La última habla, pero por defecto sin sonido: lo que se mide es que la
cadena llega hasta el final, no la tarjeta de sonido, y nadie tiene por
qué oír a Jarvis mientras corren los tests.

Uso:
    python test_cadena.py              en silencio
    python test_cadena.py --sonido     por los altavoces
"""

import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import servidor

CON_SONIDO = "--sonido" in sys.argv


class Altavoz:
    """Altavoz mudo: consume el audio en tiempo real pero no lo emite."""

    def __init__(self):
        self.fin = 0.0

    @property
    def active(self):
        return time.monotonic() < self.fin

    def play(self, audio, sr):
        self.fin = time.monotonic() + len(audio) / float(sr)

    def get_stream(self):
        return self if self.active else None

    def stop(self):
        self.fin = 0.0


if not CON_SONIDO:
    _mudo = Altavoz()
    servidor.sd.play = _mudo.play
    servidor.sd.get_stream = _mudo.get_stream
    servidor.sd.stop = _mudo.stop


def paso(titulo):
    print(f"\n--- {titulo} ---")


paso("1. Generar audio de prueba con la voz del sistema")
# Se usa el propio TTS para fabricar un audio con voz real, y así comprobar
# que Whisper entiende algo que de verdad suena a alguien hablando.
import pyttsx3
import wave

FRASE = "Hola, dime cuanto es dos mas dos."
motor = pyttsx3.init()
for v in motor.getProperty("voices"):
    if "spanish" in v.name.lower() or "helena" in v.name.lower():
        motor.setProperty("voice", v.id)
        break
motor.save_to_file(FRASE, "_prueba.wav")
motor.runAndWait()
motor.stop()

with wave.open("_prueba.wav", "rb") as w:
    fs = w.getframerate()
    datos = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
audio = datos.astype(np.float32) / 32768.0
# Whisper espera 16 kHz: remuestreo simple por interpolación
if fs != 16000:
    n = int(len(audio) * 16000 / fs)
    audio = np.interp(np.linspace(0, len(audio) - 1, n),
                      np.arange(len(audio)), audio).astype(np.float32)
print(f"audio generado: {len(audio)/16000:.1f}s   frase: {FRASE!r}")

paso("2. Transcribir con el modelo bueno")
t0 = time.time()
texto = servidor.transcribir(audio, servidor.stt_bueno, "final")
print(f"transcrito en {time.time()-t0:.1f}s -> {texto!r}")
if not texto:
    print("FALLO: Whisper no devolvió nada")
    sys.exit(1)

paso("3. Preguntar al LLM")
import ollama

historial = [{"role": "system", "content": servidor.PROMPT_SISTEMA},
             {"role": "user", "content": texto}]
t0 = time.time()
primer = None
respuesta = ""
flujo = ollama.chat(model=servidor.MODELO_LLM, messages=historial,
                    stream=True, options={"num_ctx": 2048, "temperature": 0.8})
for parte in flujo:
    c = parte["message"]["content"]
    if c and primer is None:
        primer = time.time() - t0
    respuesta += c
print(f"primer token: {primer:.1f}s | total: {time.time()-t0:.1f}s")
print(f"respuesta: {respuesta!r}")

paso("4. Comprobaciones sobre la respuesta")
problemas = []
if "<think>" in respuesta or respuesta.lstrip().lower().startswith("okay"):
    problemas.append("parece razonamiento en voz alta")
if any(s in respuesta for s in ("*", "#", "- ")):
    problemas.append("lleva markdown")
if len([f for f in respuesta.split(".") if f.strip()]) > 3:
    problemas.append("más de dos frases")
if not respuesta.strip():
    problemas.append("respuesta vacía")
print("PROBLEMAS:", problemas if problemas else "ninguno")

paso("5. Leerla en voz alta" + ("" if CON_SONIDO else "  (en silencio)"))
t0 = time.time()
servidor.hablar(respuesta)
print(f"hablado en {time.time()-t0:.1f}s")

print("\n=== CADENA COMPLETA OK ===")
