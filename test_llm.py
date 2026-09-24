"""El tiempo límite del modelo (ver llm.py).

Lo importante no es que el código compile, sino que un Ollama ATASCADO
—que acepta la conexión y no contesta nunca— acabe en un error nuestro y
no en una espera infinita. Aquí se monta justo eso: un servidor de mentira
que no responde jamás.

Necesita la librería ollama instalada, así que corre en local, no en el CI.

Uso:  python test_llm.py
"""

import socket
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import ollama

import llm

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 72)
print("UN OLLAMA ATASCADO NO CUELGA EL TURNO")
print("=" * 72)

# Un servidor que acepta y se queda mudo: el cuelgue clásico
mudo = socket.socket()
mudo.bind(("127.0.0.1", 0))
mudo.listen(5)
puerto = mudo.getsockname()[1]
vivas = []


def aceptar():
    while True:
        try:
            c, _ = mudo.accept()
            vivas.append(c)          # ni contesta ni cierra: se queda ahí
        except OSError:
            return


threading.Thread(target=aceptar, daemon=True).start()

antes = llm._cliente
llm._cliente = ollama.Client(host=f"http://127.0.0.1:{puerto}", timeout=1.5)
t0 = time.monotonic()
try:
    llm.chat(model="lo-que-sea", messages=[{"role": "user", "content": "hola"}])
    comprueba("se atasca y avisa", False, "no lanzó nada")
except llm.SeAtasco as e:
    tardo = time.monotonic() - t0
    comprueba(f"se atasca y avisa en {tardo:.1f}s, no espera para siempre",
              tardo < 6, f"{tardo:.1f}s")
    comprueba(f"y el error lo dice claro: {e}", "no contestó" in str(e))
except Exception as e:
    comprueba("se atasca y avisa", False, f"lanzó {type(e).__name__}: {e}")
llm._cliente = antes
mudo.close()

print("\n" + "=" * 72)
print("LOS DEMÁS ERRORES SIGUEN SIENDO LO QUE ERAN")
print("=" * 72)


class ClienteFalso:
    def __init__(self, error):
        self.error = error

    def chat(self, **kw):
        raise self.error


class ReadTimeout(Exception):        # como la de httpx, por el nombre
    pass


llm._cliente = ClienteFalso(ReadTimeout("read timeout"))
try:
    llm.chat(model="x", messages=[])
    comprueba("un timeout de httpx se traduce", False, "no lanzó nada")
except llm.SeAtasco:
    comprueba("un timeout de httpx se traduce a SeAtasco", True)
except Exception as e:
    comprueba("un timeout de httpx se traduce", False, type(e).__name__)

llm._cliente = ClienteFalso(ValueError("modelo no encontrado"))
try:
    llm.chat(model="x", messages=[])
    comprueba("otro error NO se disfraza de atasco", False, "no lanzó nada")
except llm.SeAtasco:
    comprueba("otro error NO se disfraza de atasco", False, "lo llamó atasco")
except ValueError:
    comprueba("otro error NO se disfraza de atasco (sube tal cual)", True)

# Un error con la causa dentro, como los envuelve ollama
envuelto = ConnectionError("fallo al conectar")
envuelto.__cause__ = ReadTimeout("read timeout")
llm._cliente = ClienteFalso(envuelto)
try:
    llm.chat(model="x", messages=[])
    comprueba("un timeout envuelto también se reconoce", False, "no lanzó nada")
except llm.SeAtasco:
    comprueba("un timeout envuelto también se reconoce", True)
except Exception as e:
    comprueba("un timeout envuelto también se reconoce", False, type(e).__name__)

llm._cliente = antes
comprueba(f"el límite configurado es de {llm.TIMEOUT_LLM_S} s",
          10 <= llm.TIMEOUT_LLM_S <= 120, llm.TIMEOUT_LLM_S)

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
