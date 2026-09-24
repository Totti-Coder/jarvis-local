"""La única puerta al modelo de lenguaje, con tiempo límite.

POR QUÉ

`ollama.chat` sin timeout espera para siempre. Si Ollama se atasca —una
carga de modelo que no acaba, la GPU ocupada por un juego, el proceso a
medio morir— el turno se queda colgado: la interfaz en "pensando", el
micrófono sin volver, y la única salida es reiniciar el servidor.

Con un límite, un atasco se convierte en una frase ("se me ha atascado el
modelo, repítemelo") y la conversación sigue.

CUÁNTO

Lo normal son 0,5 s el router y 1,2 s el conversador. El caso lento de
verdad es la primera llamada tras arrancar, cuando Ollama carga el modelo
en la GPU: ahí puede irse a 20-30 s. Por eso el límite es generoso, y no
está pensado para cortar respuestas lentas sino cuelgues.

El tiempo es POR TROZO recibido, no para la respuesta entera: al hablar en
streaming, los trozos van llegando y cada uno reinicia la cuenta, así que
una respuesta larga nunca se corta por llegar entera tarde.
"""

import ollama

from ajustes import TIMEOUT_LLM_S

_cliente = ollama.Client(timeout=TIMEOUT_LLM_S)


class SeAtasco(Exception):
    """El modelo no contestó a tiempo."""


def chat(**kw):
    """Igual que ollama.chat, pero con límite y un error propio."""
    try:
        return _cliente.chat(**kw)
    except Exception as e:
        if _es_timeout(e):
            raise SeAtasco(f"el modelo no contestó en {TIMEOUT_LLM_S} s") from e
        raise


def _es_timeout(e):
    # httpx no es dependencia directa de este proyecto (entra con ollama),
    # así que se mira el nombre en vez de importarlo para un isinstance
    nombres = {type(x).__name__ for x in (e, e.__cause__) if x is not None}
    return any("Timeout" in n or "ConnectTimeout" in n for n in nombres)
