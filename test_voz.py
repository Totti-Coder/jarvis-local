"""Comprueba que la voz de Piper funciona y se puede interrumpir.

No se puede "oír" desde un test, así que se comprueba lo medible:
que suena varias veces seguidas, que dura lo que debe, que se corta
cuando toca, y que los números se pronuncian como palabras.

Uso:  python test_voz.py
"""

import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
import servidor

fallos = 0


def comprobar(titulo, condicion, detalle=""):
    global fallos
    fallos += not condicion
    print(f"  {'OK ' if condicion else 'MAL'} {titulo}" + (f"   {detalle}" if detalle else ""))


print("=" * 66)
print("PRONUNCIACIÓN")
print("=" * 66)
# Los números tienen que sonar como palabras, no saltarse
from piper import PiperVoice

voz = PiperVoice.load(str(servidor.AQUI_VOCES / f"{servidor.VOZ_PIPER}.onnx"))
CIFRAS = [
    ("a las 5 de la tarde",   "θˈinko"),        # cinco
    ("Son las 20 y 21",       "βˈeɪnte"),       # veinte
    ("Tienes 3 cosas",        "tɾˈes"),         # tres
    ("el 31 de agosto",       "tɾˌeɪntaiʲˈuno"),  # treintaiuno
]
for texto, esperado in CIFRAS:
    fonemas = "".join("".join(c.phonemes) for c in voz.synthesize(texto))
    comprobar(f"{texto!r}", esperado in fonemas, f"-> {fonemas[:60]}")

print()
print("=" * 66)
print("REPRODUCCIÓN")
print("=" * 66)

# El fallo de pyttsx3 era que solo sonaba la primera frase
suenan = 0
for i in range(4):
    t0 = time.time()
    servidor.hablar(f"Frase número {i + 1}, con unos cuantos segundos de duración.")
    suenan += (time.time() - t0) > 1.5
comprobar("cuatro frases seguidas suenan", suenan == 4, f"{suenan}/4")

# Interrupción a media frase
servidor.permitir_voz()
threading.Thread(target=lambda: (time.sleep(1.0), servidor.cortar_voz()),
                 daemon=True).start()
t0 = time.time()
servidor.hablar("Esta frase es muy larga y tendría que cortarse mucho antes "
                "de llegar al final si la interrupción funciona bien.")
corte = time.time() - t0
comprobar("se corta al interrumpir", corte < 2.0, f"{corte:.1f}s")

# Y sigue funcionando después
servidor.permitir_voz()
t0 = time.time()
servidor.hablar("Ya puedo hablar otra vez sin ningún problema.")
despues = time.time() - t0
comprobar("vuelve a hablar tras el corte", despues > 1.5, f"{despues:.1f}s")

print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
