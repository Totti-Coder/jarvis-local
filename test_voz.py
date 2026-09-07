"""Comprueba que la voz de Piper funciona y se puede interrumpir.

No se puede "oír" desde un test, así que se comprueba lo medible:
que suena varias veces seguidas, que dura lo que debe, que se corta
cuando toca, y que los números se pronuncian como palabras.

POR QUE NO SE OYE NADA AL EJECUTARLO

Por defecto los altavoces se sustituyen por uno de mentira que consume
el audio en tiempo real pero sin emitirlo. Lo que se está probando es
la maquinaria de la cola y del corte, no la tarjeta de sonido, así que
las medidas valen igual. Sonando de verdad, cualquiera que estuviera
delante oía a Jarvis soltar "frase número uno, frase número dos" a todo
volumen, y eso no lo tiene que oír nadie.

Uso:
    python test_voz.py              en silencio
    python test_voz.py --sonido     por los altavoces, para comprobarlo a oído
"""

import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
import servidor

CON_SONIDO = "--sonido" in sys.argv


class Altavoz:
    """Altavoz de mentira: se traga el audio en tiempo real, sin ruido.

    Imita lo justo de sounddevice que usa servidor.hablar(): play(),
    get_stream().active y stop(). Así el corte a media frase se sigue
    midiendo de verdad.
    """

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
    _falso = Altavoz()
    servidor.sd.play = _falso.play
    servidor.sd.get_stream = _falso.get_stream
    servidor.sd.stop = _falso.stop

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
print("REPRODUCCIÓN" + ("  (por los altavoces)" if CON_SONIDO
                        else "  (en silencio: --sonido para oírlo)"))
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
