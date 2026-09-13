"""El audio que llega del navegador (movil) entra igual que el del PC.

Desde el movil el microfono lo captura el navegador, no Python: manda PCM
en int16 a 16 kHz por el WebSocket. Aqui se comprueba que esa ruta acaba
en el mismo sitio que la del microfono del ordenador.

QUE SE COMPRUEBA Y QUE NO

Se comprueba lo determinista: que MicrofonoRemoto expone los mismos
metodos que el del PC, que el PCM entra bien, y que transcribir() pega el
colchon de silencio.

NO se comprueba que Whisper acierte la transcripcion. Se probo y se
descarto: con voz sintetica de Piper el acierto va de 1 a 3 de cada 4
frases segun el colchon, sin patron claro, asi que un test asi fallaria
un dia si y otro no sin que nadie hubiera roto nada. Las transcripciones
se imprimen para mirarlas, pero no deciden si el test pasa.

Uso:  python test_movil.py
"""

import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
import servidor

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"   {detalle}" if detalle else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 70)
print("MICROFONO DEL NAVEGADOR")
print("=" * 70)

# ---- mismo interfaz que el del PC ------------------------------------
# Si a Microfono le sale un metodo nuevo y aqui no, el movil se rompe en
# silencio: el VAD o el espectro llamarian a algo que no existe.
faltan = (set(dir(servidor.Microfono)) - set(dir(object))
          - (set(dir(servidor.MicrofonoRemoto)) - set(dir(object))))
comprueba("no le falta ningun metodo del microfono del PC", not faltan,
          str(sorted(faltan)) if faltan else "")

# ---- alimentar y recuperar -------------------------------------------
m = servidor.MicrofonoRemoto()
m.empezar()
t = np.linspace(0, 1, 16000, endpoint=False)
tono = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
for i in range(0, len(tono), 1024):
    m.alimentar(tono[i:i + 1024].tobytes())

audio = m.audio()
comprueba("sale 1 segundo de audio", len(audio) == 16000, f"{len(audio)} muestras")
comprueba("en float32 normalizado",
          audio.dtype == np.float32 and abs(audio).max() <= 1.0,
          f"{audio.dtype}, pico {abs(audio).max():.2f}")
comprueba("el nivel sube al hablar", m.nivel > 0.5, f"{m.nivel:.2f}")
comprueba("el espectro tiene 32 bandas", len(m.espectro()) == 32)
comprueba("audio_reciente recorta", len(m.audio_reciente(0.5)) == 8000)

# Lo que llega despues de soltar el boton se tira: si no, el turno
# siguiente empezaria con la cola del anterior.
m.parar()
antes = len(m.audio())
m.alimentar(tono[:1024].tobytes())
comprueba("ignora el audio que llega tras parar", len(m.audio()) == antes)

# ---- la ruta entera: voz -> navegador -> Whisper ---------------------
print("\n" + "=" * 70)
print("DE LA VOZ A WHISPER POR LA RUTA DEL MOVIL")
print("=" * 70)

from piper import PiperVoice

_voz = PiperVoice.load(str(servidor.AQUI_VOCES / f"{servidor.VOZ_PIPER}.onnx"))


def como_del_navegador(frase):
    """Fabrica lo que mandaria el movil: 16 kHz, int16, empezando de golpe.

    El remuestreo es el mismo que hace static/captura.js: promediar cada
    grupo de muestras, que ademas de bajar la frecuencia hace de filtro.
    """
    trozos = list(_voz.synthesize(frase))
    x = np.concatenate([c.audio_float_array for c in trozos]).astype(np.float32)
    factor = trozos[0].sample_rate / 16000
    n = int(len(x) / factor)
    bajado = np.zeros(n, dtype=np.float32)
    for i in range(n):
        desde, hasta = int(i * factor), min(len(x), int((i + 1) * factor))
        bajado[i] = x[desde:hasta].mean() if hasta > desde else 0.0
    pcm = (np.clip(bajado, -1, 1) * 32767).astype(np.int16)

    remoto = servidor.MicrofonoRemoto()
    remoto.empezar()
    for i in range(0, len(pcm), 1024):
        remoto.alimentar(pcm[i:i + 1024].tobytes())
    return remoto.parar()


def sin_adornos(s):
    return s.strip(" ¿?¡!.,").lower()


FRASES = [
    "Hola, ¿qué tengo que hacer hoy?",       # la que se rompia
    "¿Qué tengo que hacer mañana?",
    "Recuérdame comprar pan a las ocho",
    "¿Tengo correos nuevos?",
    "Apaga el ordenador",
]
# El colchon SI se comprueba, porque eso es codigo nuestro: se espia lo
# que transcribir() le pasa de verdad al modelo.
recibido = {}


class ModeloEspia:
    def transcribe(self, audio, **k):
        recibido["muestras"] = len(audio)
        return [], None


largo = 16000
servidor.transcribir(np.zeros(largo, dtype=np.float32), ModeloEspia())
esperado = largo + 2 * int(servidor.COLCHON_S * servidor.FRECUENCIA)
comprueba(f"pega {servidor.COLCHON_S}s de silencio a cada lado",
          recibido.get("muestras") == esperado,
          f"{recibido.get('muestras')} muestras, esperaba {esperado}")

# Y esto es solo para mirarlo: la precision de Whisper no decide nada aqui
print("\n  (informativo: qué oye Whisper por esta ruta)")
for frase in FRASES:
    oido = servidor.transcribir(como_del_navegador(frase), servidor.stt_bueno)
    primera = sin_adornos(frase).split()[0]
    marca = "   " if primera in sin_adornos(oido) else " ~ "
    print(f"  {marca}{frase!r:<38} -> {oido!r}")

print("\n" + "=" * 70)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 70)
sys.exit(1 if fallos else 0)
