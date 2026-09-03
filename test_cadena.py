"""Prueba la cadena completa sin micro: audio -> Whisper -> Ollama -> voz.

Uso:  python test_cadena.py
Comprueba de una vez las cuatro etapas, sin necesidad de micrófono.
"""

import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import servidor  # reutiliza la config y los modelos ya cargados del servidor


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

paso("5. Leerla en voz alta")
t0 = time.time()
servidor.hablar(respuesta)
print(f"hablado en {time.time()-t0:.1f}s")

print("\n=== CADENA COMPLETA OK ===")
