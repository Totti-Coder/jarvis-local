"""Lo que Jarvis NO entendió, apuntado para poder arreglarlo.

POR QUÉ

Hasta ahora, cada vez que Jarvis fallaba había que estar delante, verlo y
contarlo para convertirlo en un caso de prueba. Así se arreglaron "en 100
el cronómetro" (Whisper por "enciende"), "quita el modo trabajo" y
"avísame a los 25 minutos". Eso no escala: depende de que alguien mire.

Esto lo apunta solo. Cuando Jarvis contesta "no encuentro eso", "no te he
entendido" o "no conozco ese atajo", se guarda LA FRASE que se dijo, para
poder revisarla luego y convertirla en un caso de los evals.

DÓNDE SE QUEDA

En tu base de datos y en ningún sitio más. No se envía, no se comparte y
no sale del equipo: son cosas que dices en voz alta en tu casa. Se guardan
90 días y se pueden borrar cuando quieras.

Stdlib pura: corre en el CI.

Uso por consola:
    python diario.py              lo de la última semana
    python diario.py --todo       todo lo guardado
    python diario.py --eval       en formato de caso para pegar en un eval
    python diario.py --borrar     vacía el diario
"""

import re
import sys
import unicodedata
from datetime import datetime, timedelta

import memoria

DIAS = 90

# Las respuestas con las que Jarvis reconoce que no ha podido. Si cambia
# una, se cambia aquí: es la lista de "lo que cuenta como fallo".
_FALLOS = (
    "no te he entendido", "no he entendido", "no entiendo",
    "no encuentro", "no conozco", "no veo", "no tengo ning",
    "no he podido", "no se puede", "no puedo",
    "no estabas", "no habia", "no hay ning",
    "no me cuadra", "no sabria",
)

# Respuestas que SUENAN a fallo pero son la respuesta correcta: contestan
# bien a una pregunta cuya respuesta es "nada". Apuntarlas seria ruido.
_NO_SON_FALLO = (
    "no tienes nada", "no hay nada apuntado", "no has apuntado horas",
    "no tengo nada reciente que deshacer", "no tienes horas",
    "no hay ninguna sesion", "no tienes ninguna tarea",
    "no hay correos", "no tienes correos",
)


# "¿Qué no has entendido?": para poder revisarlo sin abrir la consola
_PREGUNTA = re.compile(
    r"\b(?:que (?:frases? )?no (?:has |habias )?(?:entendido|entendiste|sabido|supiste|pillado)|"
    r"que se te (?:ha |habia )?quedado a medias|"
    r"que no (?:has sabido|supiste) hacer|"
    r"(?:tu )?diario de fallos|que fallos has tenido)\b")


def preguntan_por_el_diario(frase):
    return bool(_PREGUNTA.search(_normal(frase)))


def preparar():
    with memoria._conectar() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS no_entendido (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            cuando    TEXT NOT NULL,
            dijiste   TEXT NOT NULL,
            contesto  TEXT NOT NULL,
            veces     INTEGER DEFAULT 1)""")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_no_entendido_frase "
                    "ON no_entendido (dijiste)")


def _normal(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def parece_fallo(respuesta):
    """¿Esta respuesta es Jarvis reconociendo que no ha podido?"""
    t = _normal(respuesta)
    if not t:
        return False
    if any(b in t for b in (_normal(x) for x in _NO_SON_FALLO)):
        return False
    return any(f in t for f in (_normal(x) for x in _FALLOS))


def apuntar(dijiste, contesto, ahora=None):
    """Guarda una frase que no se entendió. Si ya estaba, suma una vez.

    Lo que importa no es cuántas frases distintas fallan, sino cuáles
    fallan MÁS: esas son las que merece la pena arreglar primero.
    """
    ahora = ahora or datetime.now()
    dijiste = (dijiste or "").strip()
    if not dijiste:
        return False
    with memoria._conectar() as con:
        con.execute(
            "INSERT INTO no_entendido (cuando, dijiste, contesto) VALUES (?, ?, ?) "
            "ON CONFLICT(dijiste) DO UPDATE SET veces = veces + 1, "
            "cuando = excluded.cuando, contesto = excluded.contesto",
            (ahora.isoformat(), dijiste, (contesto or "").strip()))
    return True


def ultimos(dias=7, limite=20, ahora=None):
    """Lo que no se entendió, de lo que más se repite a lo que menos."""
    ahora = ahora or datetime.now()
    desde = (ahora - timedelta(days=dias)).isoformat()
    with memoria._conectar() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM no_entendido WHERE cuando >= ? "
            "ORDER BY veces DESC, id DESC LIMIT ?", (desde, limite))]


def frase_resumen(dias=7, ahora=None):
    """Lo que dirá Jarvis si le preguntas qué no ha entendido."""
    filas = ultimos(dias, 5, ahora)
    if not filas:
        return "No me he quedado con ninguna frase a medias últimamente."
    cuales = "; ".join(
        f"{f['dijiste']}" + (f", {f['veces']} veces" if f["veces"] > 1 else "")
        for f in filas)
    if len(filas) == 1:
        return f"Hubo una que no supe: {cuales}."
    return f"Estas no las supe: {cuales}."


def limpiar(dias=DIAS, ahora=None):
    ahora = ahora or datetime.now()
    limite = (ahora - timedelta(days=dias)).isoformat()
    with memoria._conectar() as con:
        return con.execute("DELETE FROM no_entendido WHERE cuando < ?",
                           (limite,)).rowcount


def vaciar():
    with memoria._conectar() as con:
        return con.execute("DELETE FROM no_entendido").rowcount


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    memoria.preparar()
    preparar()

    if "--borrar" in sys.argv:
        print(f"Borradas {vaciar()} frases.")
        sys.exit(0)

    filas = ultimos(dias=DIAS if "--todo" in sys.argv else 7, limite=100)
    if not filas:
        print("Nada apuntado: no se le ha quedado ninguna frase a medias.")
        sys.exit(0)

    if "--eval" in sys.argv:
        # Listas para pegar en eval_ordenes.py o eval_router.py y decidir
        # a mano qué debería hacer cada una
        print("# Pega estas en el eval que toque y pon qué debería salir:")
        for f in filas:
            print(f'    ({f["dijiste"]!r}, "???", ""),'
                  f'   # x{f["veces"]}, contestó: {f["contesto"][:40]!r}')
        sys.exit(0)

    print(f"{'veces':>5}  {'cuándo':<16}  {'dijiste':<44} contestó")
    print("-" * 110)
    for f in filas:
        cuando = datetime.fromisoformat(f["cuando"]).strftime("%d/%m %H:%M")
        print(f"{f['veces']:>5}  {cuando:<16}  {f['dijiste'][:44]:<44} {f['contesto'][:40]}")
    print(f"\n{len(filas)} frases. Con --eval salen listas para pegarlas en un eval.")
