"""Lo que Jarvis sabe de ti, con forma: ciudad, nombre, horario.

POR QUÉ NO BASTA CON LO QUE YA HABÍA

Lo que contabas se guardaba como frases sueltas ("se llama Pablo", "su
novia se llama Verónica"). Está bien para charlar, pero no sirve para
usarlo: para saber tu ciudad había que buscar "vive en" con una expresión
regular, y si lo habías dicho de otra forma, no aparecía. Por eso "¿qué
tiempo hace?" seguía preguntando de qué ciudad.

Aquí se guardan los pocos datos que el asistente USA de verdad, cada uno
en su sitio:

    nombre     cómo te llamas
    ciudad     para el tiempo, sin tener que repetirla
    horario    de cuándo a cuándo trabajas

Las frases sueltas siguen donde estaban: esto no las sustituye, las
complementa.

CÓMO SE RELLENA

Hablando, y sin modelo: "vivo en Ponferrada", "me llamo Pablo", "trabajo
de 9 a 6". Son frases fijas, así que se reconocen al instante y no
dependen de que el modelo acierte. También se puede escribir a mano en la
base de datos.

Stdlib pura: corre en el CI.

Uso por consola:
    python perfil.py                    lo que sabe
    python perfil.py ciudad Ponferrada  ponerlo a mano
"""

import re
import sys
import unicodedata
from datetime import datetime

import memoria

CAMPOS = ("nombre", "ciudad", "horario_inicio", "horario_fin")


def preparar():
    with memoria._conectar() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS perfil (
            clave  TEXT PRIMARY KEY,
            valor  TEXT NOT NULL,
            cuando TEXT NOT NULL)""")


def guardar(clave, valor, ahora=None):
    ahora = ahora or datetime.now()
    valor = " ".join(str(valor).split()).strip(" .")
    if clave not in CAMPOS or not valor:
        return False
    with memoria._conectar() as con:
        con.execute("INSERT INTO perfil (clave, valor, cuando) VALUES (?, ?, ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, "
                    "cuando = excluded.cuando", (clave, valor, ahora.isoformat()))
    return True


def leer(clave):
    with memoria._conectar() as con:
        f = con.execute("SELECT valor FROM perfil WHERE clave = ?", (clave,)).fetchone()
    return f["valor"] if f else None


def todo():
    with memoria._conectar() as con:
        return {f["clave"]: f["valor"] for f in con.execute("SELECT * FROM perfil")}


def olvidar(clave):
    with memoria._conectar() as con:
        return con.execute("DELETE FROM perfil WHERE clave = ?", (clave,)).rowcount == 1


def ciudad():
    return leer("ciudad")


def nombre():
    return leer("nombre")


def horario():
    """(hora de entrada, hora de salida) o None si no lo ha dicho."""
    ini, fin = leer("horario_inicio"), leer("horario_fin")
    if ini is None or fin is None:
        return None
    try:
        return int(ini), int(fin)
    except ValueError:
        return None


def en_horario(ahora=None):
    """¿Son horas de trabajo? None si no se sabe su horario."""
    h = horario()
    if not h:
        return None
    ahora = ahora or datetime.now()
    ini, fin = h
    if ahora.weekday() >= 5:          # sábado y domingo no son jornada
        return False
    return ini <= ahora.hour < fin


# ---------------------------------------------------------------
# RECONOCER LO QUE SE DICE
# ---------------------------------------------------------------

def _normal(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9: ]+", " ", s).split())


_NUMEROS = {"una": 1, "la una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
            "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
            "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
            "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
            "veinte": 20, "veintiuna": 21, "veintiuno": 21, "veintidos": 22}

_CIUDAD = re.compile(
    r"\b(?:(?:vivo|resido|estoy viviendo)\s+en|soy\s+de|"
    r"me\s+he\s+mudado\s+a|me\s+mudo\s+a)\s+"
    r"(?P<ciudad>[a-z][a-z ]{1,38}?)\s*$")
_NOMBRE = re.compile(r"\b(?:me llamo|mi nombre es|puedes llamarme|llamame)\s+"
                     r"(?P<nombre>[a-z]{2,20})\s*$")
_HORARIO = re.compile(
    r"\b(?:trabajo|curro|mi (?:horario|jornada) es|trabajo normalmente|"
    r"suelo trabajar|entro a trabajar)\b[^0-9a-z]*"
    r"(?:de|desde)?\s*(?:las\s+)?(?P<ini>[a-z]+|\d{1,2})(?::\d{2})?\s*"
    r"(?:a|hasta|y)\s*(?:las\s+)?(?P<fin>[a-z]+|\d{1,2})(?::\d{2})?\b")

# Lo que puede ir detrás de "vivo en" y NO es una ciudad: "vivo en casa de
# mis padres", "vivo en un piso". Basta mirar la primera palabra, porque
# ninguna ciudad empieza por un artículo suelto ni por "casa"
_NO_CIUDAD = {"casa", "piso", "el", "la", "los", "las", "un", "una", "unos",
              "mi", "mis", "tu", "su", "familia", "pareja", "paz", "ello",
              "eso", "esto", "ella", "el", "pueblo", "ciudad", "barrio",
              "alquiler", "pisos"}


def _hora(texto):
    texto = texto.strip()
    if texto.isdigit():
        n = int(texto)
        return n if 0 <= n <= 23 else None
    return _NUMEROS.get(texto)


def detectar(frase):
    """(clave, valor) si la frase dice uno de los datos del perfil.

    "Trabajo de 9 a 6" devuelve el horario entero, con la salida pasada a
    24 horas: nadie trabaja de nueve de la mañana a seis de la mañana.
    """
    t = _normal(frase)
    if not t:
        return None

    m = _HORARIO.search(t)
    if m:
        ini, fin = _hora(m.group("ini")), _hora(m.group("fin"))
        if ini is not None and fin is not None:
            if fin <= ini:                 # "de 9 a 6" es de 9 a 18
                fin = (fin + 12) % 24 or 24
            if 0 <= ini < fin <= 24:
                return ("horario", (ini, fin))

    m = _NOMBRE.search(t)
    if m:
        return ("nombre", m.group("nombre").capitalize())

    m = _CIUDAD.search(t)
    if m:
        ciudad_ = m.group("ciudad").strip()
        if ciudad_.split()[0] not in _NO_CIUDAD and len(ciudad_) > 2:
            return ("ciudad", " ".join(p.capitalize() for p in ciudad_.split()))
    return None


def guardar_detectado(clave, valor, ahora=None):
    """Guarda lo que devolvió detectar y dice cómo ha quedado."""
    if clave == "horario":
        ini, fin = valor
        guardar("horario_inicio", ini, ahora)
        guardar("horario_fin", fin, ahora)
        return f"Apuntado: trabajas de {ini} a {fin}."
    guardar(clave, valor, ahora)
    if clave == "ciudad":
        return f"Apuntado: vives en {valor}. Ya no te preguntaré la ciudad."
    return f"Apuntado: te llamas {valor}."


# ---------------------------------------------------------------
# CONTARLO
# ---------------------------------------------------------------

_PREGUNTA = re.compile(
    r"\b(?:que sabes de mi|que datos (?:tienes|sabes)|que te he contado|"
    r"que recuerdas de mi|mi perfil|que sabes sobre mi)\b")


def preguntan_por_el_perfil(frase):
    return bool(_PREGUNTA.search(_normal(frase)))


def frase_resumen():
    d = todo()
    partes = []
    if d.get("nombre"):
        partes.append(f"te llamas {d['nombre']}")
    if d.get("ciudad"):
        partes.append(f"vives en {d['ciudad']}")
    h = horario()
    if h:
        partes.append(f"trabajas de {h[0]} a {h[1]}")
    sueltos = memoria.datos_conocidos(limite=3)
    if not partes and not sueltos:
        return ("De ti no sé casi nada todavía. Dime dónde vives o cómo te "
                "llamas y me lo apunto.")
    frase = ("Sé que " + ", ".join(partes) + ". ") if partes else ""
    if sueltos:
        frase += "También: " + "; ".join(sueltos) + "."
    return frase.strip()


def migrar_de_datos():
    """Saca ciudad y nombre de las frases sueltas que ya hubiera.

    Lo que se contó antes de que existiera el perfil no se pierde: se lee
    una vez y queda donde se puede usar.
    """
    rescatados = 0
    for dato in memoria.datos_conocidos(limite=50):
        t = _normal(dato)
        m = re.search(r"\bvive en ([a-z][a-z ]{1,38})$", t)
        if m and not ciudad():
            guardar("ciudad", " ".join(p.capitalize() for p in m.group(1).split()))
            rescatados += 1
        m = re.search(r"\bse llama ([a-z]{2,20})$", t)
        if m and not nombre():
            guardar("nombre", m.group(1).capitalize())
            rescatados += 1
    return rescatados


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    memoria.preparar()
    preparar()
    if len(sys.argv) >= 3:
        clave, valor = sys.argv[1], " ".join(sys.argv[2:])
        if clave == "horario":
            ini, fin = valor.replace("-", " ").split()
            guardar("horario_inicio", ini)
            guardar("horario_fin", fin)
        elif not guardar(clave, valor):
            print(f"Campos válidos: {', '.join(CAMPOS)}, horario")
            sys.exit(1)
    migrar_de_datos()
    d = todo()
    if not d:
        print("Perfil vacío. Ejemplo: python perfil.py ciudad Ponferrada")
    for clave in CAMPOS:
        if d.get(clave):
            print(f"  {clave:<16} {d[clave]}")
