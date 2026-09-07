"""Memoria del asistente: las tareas del usuario, en SQLite.

Aquí no interviene el modelo. El modelo solo decide QUÉ función llamar;
lo que se guarda y se recuerda es literal, porque una tarea mal recordada
no sirve de nada.

El intérprete de fechas es propio y no usa dateparser: se probó y falla en
lo más común ("el jueves", "pasado mañana", "esta tarde"), y en "mañana a
las 8" se come la hora.
"""

import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent / "asistente.db"

DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
        "viernes": 4, "sabado": 5, "domingo": 6}

DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------
# BASE DE DATOS
# ---------------------------------------------------------------

@contextmanager
def _conectar():
    """Abre, hace commit y CIERRA.

    Ojo: `with sqlite3.connect(...)` hace commit pero no cierra el fichero.
    Sin este envoltorio se van acumulando conexiones abiertas y en Windows
    el .db queda bloqueado.
    """
    con = sqlite3.connect(BASE)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def preparar():
    """Crea la tabla si no existe. Se llama una vez al arrancar."""
    with _conectar() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS tareas (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                texto        TEXT    NOT NULL,
                cuando_texto TEXT,               -- lo que dijo el usuario, tal cual
                cuando_iso   TEXT,               -- ya interpretado, o NULL si no se entendió
                tiene_hora   INTEGER DEFAULT 0,  -- 0 = solo día, 1 = día y hora
                hecha        INTEGER DEFAULT 0,
                creada       TEXT    NOT NULL,
                evento_id    TEXT               -- id en Google Calendar, si se subio
            )
        """)
        # Las bases de datos ya creadas no tienen la columna: se anade sin
        # romper nada. ALTER TABLE falla si ya existe, y eso da igual.
        try:
            con.execute("ALTER TABLE tareas ADD COLUMN evento_id TEXT")
        except Exception:
            pass
        # Datos sobre el usuario: su nombre, dónde vive, a qué se dedica.
        # Van aparte de las tareas porque no son cosas que hacer y no se
        # completan nunca. Sin esta tabla, "me llamo Toti" acababa apuntado
        # como si fuera un recado.
        con.execute("""
            CREATE TABLE IF NOT EXISTS datos (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                texto  TEXT NOT NULL UNIQUE,
                creada TEXT NOT NULL
            )
        """)


# ---------------------------------------------------------------
# INTERPRETAR "CUÁNDO"
# ---------------------------------------------------------------

def _sin_tildes(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


NUMEROS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
           "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
           "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40}


def _buscar_relativo(t, ahora):
    """"en 30 minutos", "dentro de una hora", "en media hora" -> datetime."""
    if re.search(r"\bahora\b|\bya\b|ahora mismo", t):
        return ahora

    m = re.search(r"(?:en|dentro de|de aqui a)\s+"
                  r"(\d+|media|" + "|".join(NUMEROS) + r")\s*"
                  r"(min|mins|minuto|minutos|h|hora|horas|dia|dias|semana|semanas)",
                  t)
    if not m:
        return None

    cantidad, unidad = m.group(1), m.group(2)
    if cantidad == "media":
        # "media hora" = 30 min; "medio día" no se contempla, es raro hablando
        valor = 0.5
    elif cantidad.isdigit():
        valor = int(cantidad)
    else:
        valor = NUMEROS[cantidad]

    if unidad.startswith("min"):
        return ahora + timedelta(minutes=valor)
    if unidad.startswith("h"):
        return ahora + timedelta(hours=valor)
    if unidad.startswith("dia"):
        return ahora + timedelta(days=valor)
    return ahora + timedelta(weeks=valor)


def _buscar_hora(t):
    """Saca la hora de un texto.

    Devuelve (hora, minuto, ambigua) o None. `ambigua` indica que el usuario
    dijo un número del 1 al 12 sin aclarar si era mañana o tarde, así que
    todavía hay que decidir cuál de las dos quería.
    """
    # "a las 19:30", "19:30", "a las 7 y media", "a las 8"
    m = re.search(r"(\d{1,2})[:.](\d{2})", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(?:a las?|sobre las?|hacia las?)\s+(\d{1,2})", t)
        if not m:
            return None
        h, mi = int(m.group(1)), 0
        if re.search(r"y media", t):
            mi = 30
        elif re.search(r"y cuarto", t):
            mi = 15
        elif re.search(r"menos cuarto", t):
            h, mi = h - 1, 45

    ambigua = False
    if h < 12 and re.search(r"(de la|por la)\s+(tarde|noche)", t):
        h += 12                                  # "las 8 de la tarde" = 20:00
    elif h == 12 and re.search(r"(de la|por la)\s+noche", t):
        h = 0
    elif re.search(r"(de la|por la)\s+manana|de la madrugada", t):
        pass                                     # lo dijo claro: se respeta
    elif 1 <= h <= 12:
        ambigua = True                           # decidirlo luego, con el reloj

    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h, mi, ambigua
    return None


def _preferida_de_dia(h):
    """De las dos lecturas posibles, la que cae en horario de vida normal.

    Nadie queda a las cinco de la madrugada sin decirlo, así que de 1 a 7
    se entiende por la tarde. De 8 a 12 se deja como está.
    """
    return h + 12 if 1 <= h <= 7 else h


def _resolver_ambigua(h, mi, dia, ahora, dijo_el_dia):
    """Decide si "a las 8:40" son las 8:40 o las 20:40.

    La regla es la que usaría cualquiera al hablar: si esa hora todavía
    está por llegar hoy, es esa. Dicho a las 20:15, "a las 8.40" son las
    20:40 de esta noche, no las 8:40 de mañana.
    Si el usuario nombró un día futuro, no hay reloj que valga y se elige
    la lectura de horario normal.
    """
    otra = h + 12 if h < 12 else h - 12

    if dia > ahora.date():
        return _preferida_de_dia(h), mi

    # Es hoy (o no se dijo día): la primera de las dos que aún no haya pasado
    candidatas = sorted({h, otra})
    for cand in candidatas:
        momento = datetime.combine(dia, datetime.min.time()).replace(hour=cand, minute=mi)
        if momento > ahora:
            return cand, mi

    # Las dos han pasado ya hoy
    if dijo_el_dia:
        # Dijo "hoy" a propósito: se respeta, y quedará como atrasada
        return _preferida_de_dia(h), mi
    return None, mi          # sin día: la resuelve quien llama, pasando a mañana


def interpretar_cuando(texto, ahora=None):
    """Convierte "el jueves a las 7" en una fecha real.

    Devuelve (datetime, tiene_hora) o (None, False) si no se entiende.
    `ahora` se puede fijar para poder escribir tests deterministas.
    """
    if not texto:
        return None, False
    ahora = ahora or datetime.now()
    t = _sin_tildes(texto).strip()

    # Tiempo relativo: "en 30 minutos", "dentro de una hora", "en media hora"
    rel = _buscar_relativo(t, ahora)
    if rel:
        return rel, True

    hora = _buscar_hora(t)

    # "mañana" es ambigua: puede ser el día siguiente o la franja horaria.
    # Como franja solo cuenta si lleva delante "por la", "esta" o "de la"
    # ("mañana por la mañana" = día siguiente, a las nueve).
    # Las franjas se quitan del texto antes de buscar el día, para que no
    # se confundan la una con la otra.
    franjas = [(r"(?:por la|esta|de la)\s+manana", (9, 0)),
               (r"(?:por la|esta|de la|la)?\s*\btarde\b", (17, 0)),
               (r"(?:por la|esta|de la|la)?\s*\bnoche\b", (21, 0)),
               (r"\bmediodia\b", (14, 0))]
    sin_franja = t
    dia_implicito = None
    for patron, valor in franjas:
        m = re.search(patron, sin_franja)
        if m:
            if hora is None:
                hora = valor
            # "esta tarde" lleva el día dentro: es hoy. Hay que anotarlo antes
            # de borrar la franja, porque al borrarla se va también el "esta".
            if m.group(0).strip().startswith("esta"):
                dia_implicito = ahora.date()
            sin_franja = re.sub(patron, " ", sin_franja)

    dia = None
    t = sin_franja

    if re.search(r"\bpasado\s+manana\b", t):
        dia = ahora.date() + timedelta(days=2)
    elif re.search(r"\bmanana\b", t):
        dia = ahora.date() + timedelta(days=1)
    elif re.search(r"\bhoy\b", t) or re.search(r"\besta\b", t):
        dia = ahora.date()
    else:
        # Nombre de día: "el jueves", "jueves que viene"
        for nombre, idx in DIAS.items():
            if re.search(rf"\b{nombre}\b", t):
                delta = (idx - ahora.weekday()) % 7
                if delta == 0:
                    delta = 7          # "el lunes" dicho un lunes = el que viene
                if re.search(r"que viene|proximo|siguiente", t) and delta < 7:
                    delta += 7
                dia = ahora.date() + timedelta(days=delta)
                break

    if dia is None:
        # "el 15 de septiembre"
        m = re.search(r"\b(\d{1,2})\s+de\s+([a-z]+)", t)
        if m:
            num = int(m.group(1))
            for i, mes in enumerate(MESES_ES, start=1):
                if _sin_tildes(mes).startswith(m.group(2)[:4]):
                    anio = ahora.year
                    try:
                        cand = ahora.date().replace(year=anio, month=i, day=num)
                    except ValueError:
                        return None, False
                    if cand < ahora.date():
                        cand = cand.replace(year=anio + 1)
                    dia = cand
                    break

    dijo_el_dia = dia is not None
    if dia is None and dia_implicito:
        dia, dijo_el_dia = dia_implicito, True

    if not hora:
        if dia is None:
            return None, False
        # Sin hora: se guarda a las 00:00 pero se marca que no había hora
        return datetime.combine(dia, datetime.min.time()), False

    h, mi = hora[0], hora[1]
    ambigua = hora[2] if len(hora) > 2 else False
    objetivo = dia or ahora.date()

    if ambigua:
        h_resuelta, mi = _resolver_ambigua(h, mi, objetivo, ahora, dijo_el_dia)
        if h_resuelta is None:
            # Las dos lecturas han pasado hoy y no se dijo día: es mañana,
            # con la lectura de horario normal ("a las 5" dicho de noche
            # son las cinco de la tarde de mañana, no las cinco de la madrugada)
            objetivo = ahora.date() + timedelta(days=1)
            h_resuelta = _preferida_de_dia(h)
        h = h_resuelta
    elif dia is None:
        # Hora sin ambigüedad y sin día ("a las 23"): hoy, o mañana si ya pasó
        cand = datetime.combine(ahora.date(), datetime.min.time()).replace(
            hour=h, minute=mi)
        if cand < ahora:
            objetivo = ahora.date() + timedelta(days=1)

    return datetime.combine(objetivo, datetime.min.time()).replace(
        hour=h, minute=mi), True


def en_palabras(iso, tiene_hora, ahora=None, con_dia=True):
    """Convierte una fecha guardada en algo que suene bien dicho en voz alta.

    Con con_dia=False se omite el día y queda solo la hora. Sirve para cuando
    ya se ha preguntado por un día concreto: repetir "mañana" en cada una de
    las cinco tareas es cansino de escuchar.
    """
    if not iso:
        return ""
    ahora = ahora or datetime.now()
    d = datetime.fromisoformat(iso)
    dias = (d.date() - ahora.date()).days

    if not con_dia:
        return hora_hablada(d.hour, d.minute) if tiene_hora else ""

    if dias == 0:
        cuando = "hoy"
    elif dias == 1:
        cuando = "mañana"
    elif dias == 2:
        cuando = "pasado mañana"
    elif 3 <= dias <= 6:
        cuando = f"el {DIAS_ES[d.weekday()]}"
    elif dias < 0:
        cuando = f"el {d.day} de {MESES_ES[d.month - 1]}, que ya pasó"
    else:
        cuando = f"el {d.day} de {MESES_ES[d.month - 1]}"

    if tiene_hora:
        cuando += " " + hora_hablada(d.hour, d.minute)
    return cuando


def hora_hablada(h, mi):
    """La hora como la diría una persona, no como la escribiría un reloj.

    Todo esto se lee en voz alta: "20:21" sonaba a símbolos. En español se
    dice la hora en formato de doce con la franja detrás, y los cuartos y
    las medias tienen nombre propio.
    """
    if mi == 0 and h == 12:
        return "al mediodía"
    if mi == 0 and h == 0:
        return "a medianoche"

    franja = ("de la madrugada" if h < 6 else
              "de la mañana" if h < 13 else
              "de la tarde" if h < 21 else
              "de la noche")
    h12 = h % 12 or 12

    if mi == 0:
        reloj = f"las {h12}"
    elif mi == 15:
        reloj = f"las {h12} y cuarto"
    elif mi == 30:
        reloj = f"las {h12} y media"
    elif mi == 45:
        siguiente = (h12 % 12) + 1
        reloj = f"las {siguiente} menos cuarto"
    else:
        reloj = f"las {h12} y {mi}"

    # A la una se dice en singular
    if reloj.startswith("las 1 ") or reloj == "las 1":
        reloj = reloj.replace("las 1", "la una", 1)
    return f"a {reloj} {franja}"


# ---------------------------------------------------------------
# OPERACIONES
# ---------------------------------------------------------------

def anadir_tarea(texto, cuando=""):
    momento, tiene_hora = interpretar_cuando(cuando)
    with _conectar() as con:
        cur = con.execute(
            "INSERT INTO tareas (texto, cuando_texto, cuando_iso, tiene_hora, creada)"
            " VALUES (?,?,?,?,?)",
            (texto.strip(), cuando.strip() or None,
             momento.isoformat() if momento else None,
             int(tiene_hora), datetime.now().isoformat()),
        )
        tid = cur.lastrowid

    # Espejo en Google Calendar. Va DESPUÉS de guardar en SQLite y a
    # propósito: si Google falla o no hay internet, la tarea ya está a
    # salvo y el usuario no se entera de nada.
    if momento:
        try:
            import calendario
            evento = calendario.crear_evento(texto.strip(),
                                             momento.isoformat(), tiene_hora)
            if evento:
                with _conectar() as con:
                    con.execute("UPDATE tareas SET evento_id = ? WHERE id = ?",
                                (evento, tid))
        except Exception as e:
            print(f"[calendar] no se pudo sincronizar: {e}")

    if momento:
        return f"Apuntado: {texto}, {en_palabras(momento.isoformat(), tiene_hora)}."
    if cuando:
        # Se guarda igual, pero se avisa de que no se entendió el cuándo
        return f"Apuntado: {texto}. No he entendido cuándo, lo dejo sin fecha."
    return f"Apuntado: {texto}."


FRANJAS_HORARIAS = {
    "madrugada": (0, 6),
    "manana": (6, 13),
    "mediodia": (13, 16),
    "tarde": (13, 21),
    "noche": (21, 24),
}


def _franja_de(t):
    """Detecta "por la tarde", "esta noche"... y devuelve su horario.

    "mañana" solo cuenta como franja si lleva delante "por la", "esta" o
    "de la": en "¿qué tengo mañana?" es el día siguiente, no el horario.
    """
    if re.search(r"(?:por la|esta|de la)\s+manana", t):
        return FRANJAS_HORARIAS["manana"]
    for nombre in ("madrugada", "mediodia", "tarde", "noche"):
        if re.search(r"\b" + nombre + r"\b", t):
            return FRANJAS_HORARIAS[nombre]
    return None


RANGO = re.compile(
    r"(?:entre|de|desde)\s+(?:las?\s+)?(\d{1,2})(?:[:.](\d{2}))?"
    r"\s*(?:y|a|hasta)\s+(?:las?\s+)?(\d{1,2})(?:[:.](\d{2}))?")


def _buscar_rango(t):
    """"entre las 2 y las 5 de la tarde" -> (14, 0, 17, 0), y el resto del texto.

    Devuelve (h_ini, m_ini, h_fin, m_fin, resto) o None. El `resto` es la
    frase sin el rango, para poder averiguar después de qué día hablaba
    ("mañana entre las 2 y las 5").
    """
    m = RANGO.search(t)
    if not m:
        return None
    h0, m0 = int(m.group(1)), int(m.group(2) or 0)
    h1, m1 = int(m.group(3)), int(m.group(4) or 0)
    if not (0 <= h0 <= 23 and 0 <= h1 <= 23 and m0 < 60 and m1 < 60):
        return None

    # La franja va al final y vale para las dos horas: "entre las 2 y las 5
    # de la tarde" son las dos Y las cinco de la tarde, no solo las cinco.
    cola = t[m.end():]
    if re.search(r"(de la|por la)\s+(tarde|noche)", cola):
        h0 += 12 if h0 < 12 else 0
        h1 += 12 if h1 < 12 else 0
    elif re.search(r"(de la|por la)\s+manana|de la madrugada", cola):
        pass                        # lo dijo claro, se respetan tal cual
    else:
        # Sin franja: cada hora a su lectura razonable (1 a 7 = tarde)
        h0, h1 = _preferida_de_dia(h0), _preferida_de_dia(h1)
        # "entre las 10 y las 2" cruza el mediodía: el final es por la tarde
        if h1 < h0 and h1 < 12:
            h1 += 12

    if (h1, m1) <= (h0, m0):
        return None                 # rango imposible: mejor no filtrar mal

    # Del resto se quitan las franjas: ya se han usado arriba, y si se dejan,
    # "de la mañana" se leería luego como una hora suelta y movería el día.
    resto = t[:m.start()] + " " + cola
    resto = re.sub(r"(de la|por la)\s+(manana|tarde|noche|madrugada)", " ", resto)
    return h0, m0, h1, m1, resto.strip()


def _fin_de_semana(t, ahora):
    """"El fin de semana" son viernes, sábado y domingo enteros.

    Se cuenta el VIERNES porque para quien pregunta el finde empieza al
    salir de trabajar el viernes, no el sábado por la mañana.

    Y si ya se está dentro (viernes, sábado o domingo), "este fin de
    semana" es el de ahora mismo, no el de dentro de siete días: en
    domingo por la mañana, preguntar por el finde es preguntar por hoy.

    Devuelve None si la frase no habla de esto.
    """
    if not re.search(r"\bfin(es)? de semana\b|\bfinde\b", t):
        return None

    hoy = ahora.date()
    dia = hoy.weekday()                       # lunes=0 ... domingo=6
    if dia >= 4:                              # ya es viernes, sábado o domingo
        viernes = hoy - timedelta(days=dia - 4)
    else:
        viernes = hoy + timedelta(days=4 - dia)

    if re.search(r"\bproximo\b|\bsiguiente\b|que viene", t):
        viernes += timedelta(days=7)

    inicio = datetime.combine(viernes, datetime.min.time())
    # Hasta el domingo a las 23:59:59, igual que un día suelto: la
    # medianoche del lunes metería dentro cosas de la semana siguiente.
    fin = datetime.combine(viernes + timedelta(days=3),
                           datetime.min.time()) - timedelta(seconds=1)
    return inicio, fin


def interpretar_ventana(texto, ahora=None):
    """Convierte "en 30 minutos" o "mañana" en un intervalo (desde, hasta).

    Hace falta porque no es lo mismo preguntar por un DÍA ("¿qué tengo
    mañana?", que abarca el día entero) que por un RATO ("¿qué tengo en
    media hora?", que va de ahora hasta dentro de media hora) o por un
    TRAMO ("entre las dos y las cinco").
    Devuelve (None, None) si no se entiende o si no se pidió filtro.
    """
    if not texto:
        return None, None
    ahora = ahora or datetime.now()
    t = _sin_tildes(texto).strip()

    # El fin de semana se mira lo primero: son tres días seguidos y no
    # encaja en nada de lo de abajo, que va de un día o de un rato.
    finde = _fin_de_semana(t, ahora)
    if finde:
        return finde

    # Rato: de ahora hasta el momento pedido
    rel = _buscar_relativo(t, ahora)
    if rel:
        return (ahora, rel) if rel > ahora else (rel, ahora)

    # Tramo entre dos horas. Se mira antes que nada porque "entre las 2 y
    # las 5" contiene horas sueltas que confundirían al resto del análisis.
    rango = _buscar_rango(t)
    if rango:
        h0, m0, h1, m1, resto = rango
        # El día sale de lo que quede de frase: "mañana entre las 2 y las 5"
        dia = ahora.date()
        if resto:
            otro, _ = interpretar_cuando(resto, ahora)
            if otro:
                dia = otro.date()
        base = datetime.combine(dia, datetime.min.time())
        return base.replace(hour=h0, minute=m0), base.replace(hour=h1, minute=m1)

    momento, _ = interpretar_cuando(texto, ahora)
    if not momento:
        return None, None

    # Día entero, de las 00:00 a las 23:59:59. El final NO puede ser la
    # medianoche siguiente: una tarea guardada a las 00:00 del día de después
    # caería dentro, y "¿qué tengo hoy?" acababa incluyendo cosas de mañana.
    inicio = datetime.combine(momento.date(), datetime.min.time())
    fin = inicio + timedelta(days=1) - timedelta(seconds=1)

    # Si además dijo una franja ("mañana por la tarde"), se estrecha a ella.
    # Antes se ignoraba y "mañana por la tarde" devolvía el día completo,
    # gimnasio de las siete de la mañana incluido.
    franja = _franja_de(t)
    if franja:
        h0, h1 = franja
        inicio = inicio.replace(hour=h0)
        fin = inicio.replace(hour=h1 - 1, minute=59, second=59)
    return inicio, fin


PALABRAS_VACIAS = {"el", "la", "los", "las", "un", "una", "de", "del", "a",
                   "que", "mi", "mis", "lo", "al", "y", "o", "para", "con"}


def listar_tareas(cuando="", texto="", ahora=None):
    """Qué hay pendiente. Se puede filtrar por momento, por nombre, o los dos.

    El filtro por nombre hace falta para "¿a qué hora tengo el test?": eso
    no pregunta por una franja horaria, pregunta por UNA tarea concreta.
    Sin él, el modelo se inventaba un `cuando` para poder llamar igualmente.
    """
    ahora = ahora or datetime.now()
    with _conectar() as con:
        filas = con.execute(
            "SELECT * FROM tareas WHERE hecha = 0 ORDER BY"
            " cuando_iso IS NULL, cuando_iso"
        ).fetchall()

    # Filtro por nombre: se comparan palabras con contenido, ignorando
    # artículos y preposiciones ("el test de mates" casa con "test mates")
    if texto:
        busca = {p for p in _sin_tildes(texto).split()
                 if p not in PALABRAS_VACIAS and len(p) > 2}
        if busca:
            def puntua(f):
                return len(busca & set(_sin_tildes(f["texto"]).split()))
            con_algo = [f for f in filas if puntua(f)]
            if con_algo:
                filas = sorted(con_algo, key=puntua, reverse=True)
            else:
                nombre = texto.strip()
                return f"No tienes nada apuntado sobre {nombre}."

    desde, hasta = interpretar_ventana(cuando, ahora)
    if desde:
        filas = [f for f in filas if f["cuando_iso"]
                 and desde <= datetime.fromisoformat(f["cuando_iso"]) <= hasta]

    if not filas:
        return "No tienes nada apuntado." if not cuando else f"No tienes nada apuntado para {cuando}."

    # Si se preguntó por un día y todo cae en ese día, no hace falta
    # repetirlo cinco veces: basta con la hora de cada cosa.
    dias_distintos = {datetime.fromisoformat(f["cuando_iso"]).date()
                      for f in filas if f["cuando_iso"]}
    repetir_dia = not (desde and len(dias_distintos) <= 1)

    partes = []
    for f in filas[:6]:      # más de seis, dichas en voz alta, no se retienen
        p = f["texto"]
        if f["cuando_iso"]:
            cuando_txt = en_palabras(f["cuando_iso"], f["tiene_hora"],
                                     ahora, con_dia=repetir_dia)
            if cuando_txt:
                p += " " + cuando_txt
        partes.append(p)

    resto = len(filas) - len(partes)
    cabecera = "Tienes: " if repetir_dia else f"{cuando.capitalize()} tienes: "
    texto = cabecera + "; ".join(partes) + "."
    if resto > 0:
        texto += f" Y {resto} cosa más." if resto == 1 else f" Y {resto} cosas más."
    return texto


def completar_tarea(texto):
    """Marca como hecha la tarea que más se parezca a lo que dijo el usuario."""
    busca = set(_sin_tildes(texto).split())
    with _conectar() as con:
        filas = con.execute("SELECT * FROM tareas WHERE hecha = 0").fetchall()
        mejor, puntos = None, 0
        for f in filas:
            palabras = set(_sin_tildes(f["texto"]).split())
            comunes = len(busca & palabras)
            if comunes > puntos:
                mejor, puntos = f, comunes
        if not mejor:
            return "No encuentro esa tarea en tu lista."
        con.execute("UPDATE tareas SET hecha = 1 WHERE id = ?", (mejor["id"],))

    # Si estaba en Google Calendar, se quita también de allí
    if mejor["evento_id"]:
        try:
            import calendario
            calendario.borrar_evento(mejor["evento_id"])
        except Exception as e:
            print(f"[calendar] no se pudo borrar el evento: {e}")

    return f"Hecho, quito {mejor['texto']} de la lista."


def resumen_del_dia(ahora=None):
    """Qué contarle al usuario nada más entrar. Cadena vacía si no hay nada.

    Junta tres cosas: lo atrasado (que es lo más urgente), lo de hoy, y un
    recuento de lo que no tiene fecha para que no se pierda en el olvido.
    """
    ahora = ahora or datetime.now()
    with _conectar() as con:
        filas = con.execute(
            "SELECT * FROM tareas WHERE hecha = 0 ORDER BY cuando_iso"
        ).fetchall()

    atrasadas, hoy, sin_fecha = [], [], []
    for f in filas:
        if not f["cuando_iso"]:
            sin_fecha.append(f)
            continue
        d = datetime.fromisoformat(f["cuando_iso"])
        if d.date() < ahora.date():
            atrasadas.append(f)
        elif d.date() == ahora.date():
            hoy.append(f)

    partes = []
    if hoy:
        def con_hora(f):
            if not f["tiene_hora"]:
                return f["texto"]
            d = datetime.fromisoformat(f["cuando_iso"])
            return f"{f['texto']} {hora_hablada(d.hour, d.minute)}"

        cosas = "; ".join(con_hora(f) for f in hoy[:5])
        partes.append(f"Hoy tienes: {cosas}.")
    if atrasadas:
        n = len(atrasadas)
        if n == 1:
            partes.append(f"Y se te pasó {atrasadas[0]['texto']}.")
        else:
            partes.append(f"Y tienes {n} cosas atrasadas.")
    if sin_fecha and not partes:
        # Solo se mencionan si no hay nada más; si no, es ruido al entrar.
        n = len(sin_fecha)
        partes.append(f"Tienes {n} cosa apuntada sin fecha." if n == 1
                      else f"Tienes {n} cosas apuntadas sin fecha.")

    if not partes:
        return ""

    a = ahora.hour
    saludo = "Buenos días" if a < 13 else ("Buenas tardes" if a < 21 else "Buenas noches")
    return f"{saludo}. " + " ".join(partes)


def recordar_dato(texto):
    """Guarda algo que el usuario cuenta sobre sí mismo."""
    texto = " ".join(texto.split()).strip(" .")
    if not texto:
        return "No he entendido qué querías que recordara."
    with _conectar() as con:
        ya = con.execute("SELECT 1 FROM datos WHERE lower(texto) = lower(?)",
                         (texto,)).fetchone()
        if ya:
            return "Eso ya lo tenía apuntado."
        con.execute("INSERT INTO datos (texto, creada) VALUES (?,?)",
                    (texto, datetime.now().isoformat()))
    return "Vale, me acuerdo."


# Empiezos que delatan una frase SIN sujeto: "se llama Toti", "vive en Madrid".
# Son el formato viejo, de cuando el prompt le pegaba "El usuario" delante.
SIN_SUJETO = ("se ", "es ", "esta ", "vive ", "tiene ", "trabaja ", "estudia ",
              "le gusta", "prefiere", "odia", "sabe ", "quiere", "necesita")


def como_frase(dato):
    """Convierte un dato guardado en una frase que se entienda sola.

    Antes se guardaban trozos ("se llama Toti") y el prompt les añadía
    "El usuario" delante. Eso rompía con la gente del entorno: "mi novia se
    llama Verónica" acababa guardado como "se llama Verónica", y al leerlo
    salía "El usuario se llama Verónica", que dice justo lo contrario.
    Ahora se guardan frases completas ("su novia se llama Verónica") y esto
    solo pone el sujeto a las que vienen del formato antiguo.
    """
    d = dato.strip()
    if not d:
        return ""
    if _sin_tildes(d).startswith(SIN_SUJETO):
        return f"El usuario {d}"
    return d[0].upper() + d[1:]


def datos_conocidos(limite=25):
    """Lo que Jarvis sabe del usuario, para meterlo en el prompt.

    Aquí está la diferencia entre un asistente que te conoce y uno que
    empieza de cero en cada conversación.
    """
    with _conectar() as con:
        filas = con.execute(
            "SELECT texto FROM datos ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
    return [f["texto"] for f in reversed(filas)]


def olvidar_dato(texto):
    """Borra un dato. El usuario tiene que poder rectificar."""
    busca = set(_sin_tildes(texto).split())
    with _conectar() as con:
        filas = con.execute("SELECT * FROM datos").fetchall()
        mejor, puntos = None, 0
        for f in filas:
            comunes = len(busca & set(_sin_tildes(f["texto"]).split()))
            if comunes > puntos:
                mejor, puntos = f, comunes
        if not mejor:
            return "No tengo nada apuntado que se parezca a eso."
        con.execute("DELETE FROM datos WHERE id = ?", (mejor["id"],))
    return f"Vale, olvido que {mejor['texto']}."


def que_hora_es():
    a = datetime.now()
    # hora_hablada devuelve "a las ocho y media de la tarde"; aquí sobra el "a"
    reloj = hora_hablada(a.hour, a.minute)[2:]
    verbo = "Es" if reloj.startswith("la una") else "Son"
    return (f"{verbo} {reloj}, del {DIAS_ES[a.weekday()]} "
            f"{a.day} de {MESES_ES[a.month - 1]}.")
