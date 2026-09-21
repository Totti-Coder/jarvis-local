"""Horas por cliente: "empiezo con Acme", "¿cuántas horas llevo este mes?".

POR QUÉ

Quien factura por horas pierde dinero por las horas que no apunta: una
llamada de veinte minutos, una revisión rápida, un correo largo. Apuntarlas
a mano es justo lo que se deja para luego y se olvida. Hablando cuesta
tres palabras.

Jarvis NO factura: desde julio de 2027 eso exige software certificado
(Verifactu). Lo que hace es dejar las horas listas para pasarlas al programa
de facturación: por voz se consultan y en CSV se exportan.

CÓMO

  - Cada sesión es una fila en SQLite (cliente, inicio, fin). La que está
    en marcha tiene fin NULL: sobrevive a reiniciar el servidor.
  - Empezar con otro cliente cierra la anterior. Nadie trabaja para dos a
    la vez, y así nunca se solapan.
  - Las órdenes se reconocen con patrones fijos, sin el modelo, igual que
    el cronómetro. "He terminado" suelto solo cuenta si hay una sesión
    abierta: sin ella, sigue su camino (puede ser una tarea hecha).

Stdlib pura y con "ahora" inyectable: los tests no dependen del reloj.

Uso por consola:
    python horas.py                    horas de este mes, por cliente
    python horas.py --csv 2026-09      exporta ese mes a CSV
"""

import csv
import difflib
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import memoria

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Una sesión abierta más de esto casi seguro es un olvido: se avisa al cerrarla
SESION_SOSPECHOSA_H = 12

# Cada cuánto se recuerda que sigue abierta (3 h, 6 h, 9 h...). Olvidarse de
# parar es el error más caro: infla la factura o, si se nota, obliga a
# reconstruir la jornada de memoria.
RECORDAR_CADA_H = 3

# Parecido mínimo para dar por hecho que "Akme" es Acme. Por debajo, se
# pregunta si es un cliente nuevo. Medido en test_horas_cliente.py
PARECIDO_MIN = 0.8


def carpeta_documentos():
    """La carpeta Documentos DE VERDAD, no la que se supone.

    Path.home() / "Documents" parece obvio y falla: con OneDrive, Windows
    mueve Documentos a OneDrive/Documentos, y la carpeta de siempre queda
    ahí, vacía y sin que el usuario la mire. Jarvis diría "te lo he dejado
    en Documentos" y no lo encontrarías. Se le pregunta a Windows, que es
    quien lo sabe (SHGetKnownFolderPath, el mismo dato que usa el
    Explorador).
    """
    if sys.platform == "win32":
        try:
            import ctypes
            import uuid
            documentos = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")
            guid = (ctypes.c_byte * 16).from_buffer_copy(documentos.bytes_le)
            ruta = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(guid), 0, None, ctypes.byref(ruta)) == 0:
                try:
                    return Path(ruta.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(ruta)
        except Exception as e:
            print(f"[horas] no se pudo preguntar por Documentos: {e}")
    return Path.home() / "Documents"


def carpeta_csv():
    return carpeta_documentos() / "Jarvis"


# ---------------------------------------------------------------
# BASE DE DATOS
# ---------------------------------------------------------------

def preparar():
    with memoria._conectar() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS sesiones (
            id      INTEGER PRIMARY KEY,
            cliente TEXT NOT NULL,          -- como se dijo la primera vez
            clave   TEXT NOT NULL,          -- sin tildes ni mayusculas
            inicio  TEXT NOT NULL,
            fin     TEXT)""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_sesiones_clave "
                    "ON sesiones (clave)")


def _normal(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def clave_de(nombre):
    return _normal(nombre)


def abierta():
    """La sesión en marcha, o None."""
    with memoria._conectar() as con:
        f = con.execute("SELECT id, cliente, clave, inicio FROM sesiones "
                        "WHERE fin IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if not f:
        return None
    return {"id": f[0], "cliente": f[1], "clave": f[2],
            "inicio": datetime.fromisoformat(f[3])}


def clientes():
    """{clave: nombre} de todos los clientes con alguna sesión."""
    with memoria._conectar() as con:
        filas = con.execute("SELECT clave, cliente FROM sesiones "
                            "GROUP BY clave ORDER BY MIN(id)").fetchall()
    return {c: n for c, n in filas}


def cliente_parecido(dicho, conocidos):
    """La clave del cliente conocido al que se refiere, o None si es nuevo.

    Whisper no escribe igual un nombre propio dos días seguidos: "Acme",
    "Akme", "Acmé". Sin esto cada variante era un cliente distinto y las
    horas se repartían entre ellos.
    """
    clave = clave_de(dicho)
    if not clave:
        return None
    if clave in conocidos:
        return clave
    # "García López" cuando el cliente es "García"
    for c in conocidos:
        if clave.startswith(c + " "):
            return c
    suena = _como_suena(clave)
    mejor, nota = None, 0.0
    for c in conocidos:
        otro = _como_suena(c)
        # En nombres cortos una letra pesa demasiado: "Eva" (eba) contra
        # "BBVA" (ba) daba 0,8. Ahí se exige que suenen igual del todo.
        if min(len(suena), len(otro)) <= 4:
            r = 1.0 if suena == otro else 0.0
        else:
            r = difflib.SequenceMatcher(None, suena, otro).ratio()
        if r > nota:
            mejor, nota = c, r
    return mejor if nota >= PARECIDO_MIN else None


def _como_suena(clave):
    """Cómo se pronuncia en español, que es lo que Whisper oye.

    Comparar letras daba "Akme"/"Acme" = 0,75, justo en el límite, igual
    que "Marina"/"Marta" (0,73), que son personas distintas. Comparando el
    sonido, "Akme" y "Acme" son idénticos y el margen se abre.
    """
    s = clave.replace(" ", "")
    for a, b in (("ch", "#"), ("qu", "k"), ("ll", "y"), ("x", "ks")):
        s = s.replace(a, b)
    s = re.sub(r"c(?=[ei])", "s", s)         # ce, ci -> se, si
    s = re.sub(r"gu(?=[ei])", "g", s)        # gue, gui -> ge, gi
    s = s.replace("c", "k").replace("z", "s").replace("v", "b").replace("h", "")
    s = re.sub(r"(.)\1+", r"\1", s)          # mercadonna -> mercadona
    return s.replace("#", "ch")


def nombre_bonito(dicho):
    # "acme" -> "Acme", pero "BBVA" o "McKinsey" se quedan como se dijeron
    return dicho[:1].upper() + dicho[1:] if dicho.islower() else dicho


def empezar(cliente, ahora=None):
    ahora = ahora or datetime.now()
    clave = clave_de(cliente)
    conocido = clientes().get(clave)
    nombre = conocido or nombre_bonito(cliente.strip())

    antes = abierta()
    if antes and antes["clave"] == clave:
        return (f"Ya estás con {antes['cliente']} desde hace "
                f"{duracion_hablada((ahora - antes['inicio']).total_seconds())}.")

    frase = ""
    if antes:
        frase = f"Cierro {antes['cliente']}: {_cerrar(antes, ahora)}. "
    with memoria._conectar() as con:
        con.execute("INSERT INTO sesiones (cliente, clave, inicio) "
                    "VALUES (?, ?, ?)", (nombre, clave, ahora.isoformat()))
    return frase + f"Empiezo a contar para {nombre}."


def _cerrar(sesion, fin):
    """Cierra la sesión y devuelve cuánto duró, dicho."""
    with memoria._conectar() as con:
        con.execute("UPDATE sesiones SET fin = ? WHERE id = ?",
                    (fin.isoformat(), sesion["id"]))
    segundos = (fin - sesion["inicio"]).total_seconds()
    dicho = duracion_hablada(segundos)
    if segundos > SESION_SOSPECHOSA_H * 3600:
        dicho += (". Ojo, ha estado abierta mucho tiempo: si se te olvidó "
                  "pararla, corrígela en el CSV")
    return dicho


def parar(ahora=None, fin=None):
    """Cierra la sesión abierta, ahora o a la hora que se diga."""
    ahora = ahora or datetime.now()
    s = abierta()
    if not s:
        return "No estabas contando horas para nadie."
    if fin is None:
        return f"Terminado con {s['cliente']}: {_cerrar(s, ahora)}."
    # La hora se repite al decirlo: si se entendió "las 7 de la mañana"
    # donde se quería "de la tarde", se oye en el acto
    return (f"Terminado con {s['cliente']} {momento_hablado(fin, ahora)}: "
            f"{_cerrar(s, fin)}.")


def momento_hablado(momento, ahora):
    """"a las 7 de la tarde", o "ayer a las 7 de la tarde"."""
    hora = memoria.hora_hablada(momento.hour, momento.minute)
    dias = (ahora.date() - momento.date()).days
    if dias == 0:
        return hora
    if dias == 1:
        return f"ayer {hora}"
    return f"el {memoria.DIAS_ES[momento.weekday()]} {hora}"


def fin_dicho(texto, inicio, ahora):
    """"Terminé a las 7" -> el momento, MIRANDO HACIA ATRÁS.

    El intérprete de la agenda lleva "a las 7" al futuro, que es lo que
    toca al apuntar una tarea. Aquí es al revés: se terminó en algún
    momento entre el inicio de la sesión y ahora.

    Si caben varias lecturas (las 7 de ayer por la tarde y las 7 de esta
    mañana), gana LA PRIMERA tras el inicio: quien olvidó parar suele
    darse cuenta tarde, no a la media hora. Y se repite en voz alta.
    """
    t = memoria._sin_tildes(texto)
    hora = memoria._buscar_hora(t)
    if not hora:
        return None
    h, mi = hora[0], hora[1]
    ambigua = hora[2] if len(hora) > 2 else False
    lecturas = {h, (h + 12) % 24} if ambigua else {h}

    dias = [inicio.date() + timedelta(days=i)
            for i in range((ahora.date() - inicio.date()).days + 1)]
    if re.search(r"\bayer\b", t):
        dias = [d for d in dias if d == ahora.date() - timedelta(days=1)]
    elif re.search(r"\bhoy\b", t):
        dias = [d for d in dias if d == ahora.date()]

    candidatos = sorted(
        datetime.combine(d, datetime.min.time()).replace(hour=x, minute=mi)
        for d in dias for x in lecturas)
    validos = [c for c in candidatos if inicio < c <= ahora]
    return validos[0] if validos else None


def recordatorio(ahora=None):
    """(clave, frase) si toca recordar que hay una sesión abierta, o None.

    La clave va a la tabla de avisos: con dos pestañas abiertas, solo una
    lo dice, y cada tramo (3 h, 6 h...) se dice una vez.
    """
    ahora = ahora or datetime.now()
    s = abierta()
    if not s:
        return None
    tramos = int((ahora - s["inicio"]).total_seconds() // (RECORDAR_CADA_H * 3600))
    if tramos < 1:
        return None
    lleva = duracion_hablada((ahora - s["inicio"]).total_seconds())
    return (f"h:{s['id']}:{tramos}",
            f"Sigues contando horas para {s['cliente']}: llevas {lleva}. "
            f"Si ya habías terminado, dime a qué hora, por ejemplo: "
            f"terminé a las 7.")


def aviso_al_entrar(ahora=None):
    """Si al abrir Jarvis sigue abierta una sesión de otro día, se dice."""
    ahora = ahora or datetime.now()
    s = abierta()
    if not s or s["inicio"].date() >= ahora.date():
        return None
    return (f"Ojo: la sesión de {s['cliente']} sigue abierta desde "
            f"{momento_hablado(s['inicio'], ahora)}. Si terminaste antes, "
            f"dime a qué hora.")


def sesiones(desde, hasta, ahora=None):
    """Las sesiones que tocan [desde, hasta), recortadas a esa ventana.

    Una que empezó el 31 a las 23:00 y acabó el 1 a la 01:00 cuenta una
    hora en cada mes. La abierta cuenta hasta ahora.
    """
    ahora = ahora or datetime.now()
    with memoria._conectar() as con:
        filas = con.execute(
            "SELECT cliente, clave, inicio, fin FROM sesiones "
            "WHERE inicio < ? AND (fin IS NULL OR fin > ?) ORDER BY inicio",
            (hasta.isoformat(), desde.isoformat())).fetchall()
    salida = []
    for cliente, clave, ini, fin in filas:
        a = max(datetime.fromisoformat(ini), desde)
        b = min(datetime.fromisoformat(fin) if fin else ahora, hasta)
        # La abierta sale aunque acabe de empezar: si no, "¿cuántas horas
        # llevo?" justo después de "empiezo con Acme" diría que ninguna
        if b > a or (fin is None and b == a):
            salida.append({"cliente": cliente, "clave": clave, "inicio": a,
                           "fin": b, "abierta": fin is None})
    return salida


def totales(desde, hasta, ahora=None):
    """{clave: (nombre, segundos)}, de más a menos horas."""
    t = {}
    for s in sesiones(desde, hasta, ahora):
        nombre, seg = t.get(s["clave"], (s["cliente"], 0))
        t[s["clave"]] = (nombre, seg + (s["fin"] - s["inicio"]).total_seconds())
    return dict(sorted(t.items(), key=lambda kv: -kv[1][1]))


def exportar_csv(desde, hasta, ruta, ahora=None):
    """Una fila por sesión. Punto y coma y coma decimal: lo que abre bien
    Excel en español sin tocar nada."""
    filas = sesiones(desde, hasta, ahora)
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["cliente", "fecha", "inicio", "fin", "horas"])
        for s in filas:
            horas = (s["fin"] - s["inicio"]).total_seconds() / 3600
            # Inyección de fórmulas: Excel ejecuta una celda que empieza
            # por = + - @ como fórmula. Por voz no llega ninguno de esos
            # caracteres, pero el CSV no debe depender de eso.
            cliente = s["cliente"]
            if cliente[:1] in ("=", "+", "-", "@", "\t", "\r"):
                cliente = "'" + cliente
            w.writerow([cliente, f"{s['inicio']:%d/%m/%Y}",
                        f"{s['inicio']:%H:%M}",
                        f"{s['fin']:%H:%M}" + (" (en curso)" if s["abierta"] else ""),
                        f"{horas:.2f}".replace(".", ",")])
    return len(filas)


# ---------------------------------------------------------------
# CÓMO SE DICE
# ---------------------------------------------------------------

def duracion_hablada(segundos):
    """Al minuto: en horas de trabajo los segundos no importan."""
    minutos = int(round(segundos / 60))
    if minutos < 1:
        return "menos de un minuto"
    h, m = divmod(minutos, 60)
    if h and m == 30:
        return "una hora y media" if h == 1 else f"{h} horas y media"
    partes = []
    if h:
        partes.append("una hora" if h == 1 else f"{h} horas")
    if m:
        partes.append("un minuto" if m == 1 else f"{m} minutos")
    return " y ".join(partes)


# ---------------------------------------------------------------
# DE QUÉ PERIODO HABLA
# ---------------------------------------------------------------

def periodo(texto, ahora=None):
    """(desde, hasta, cómo se dice). Si no nombra ninguno, este mes."""
    ahora = ahora or datetime.now()
    t = _normal(texto)
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    lunes = hoy - timedelta(days=hoy.weekday())
    mes = hoy.replace(day=1)

    if re.search(r"\bayer\b", t):
        return hoy - timedelta(days=1), hoy, "ayer"
    if re.search(r"\bhoy\b", t):
        return hoy, hoy + timedelta(days=1), "hoy"
    if re.search(r"\bsemana pasada\b", t):
        return lunes - timedelta(days=7), lunes, "la semana pasada"
    if re.search(r"\b(esta|la) semana\b", t):
        return lunes, lunes + timedelta(days=7), "esta semana"
    if re.search(r"\bmes pasado\b", t):
        anterior = (mes - timedelta(days=1)).replace(day=1)
        return anterior, mes, "el mes pasado"
    if re.search(r"\beste ano\b", t):
        return hoy.replace(month=1, day=1), hoy.replace(year=hoy.year + 1, month=1, day=1), "este año"
    for i, nombre in enumerate(MESES, 1):
        if re.search(rf"\b{nombre}\b", t):
            # Un mes que aún no ha llegado es el del año pasado
            anio = ahora.year if i <= ahora.month else ahora.year - 1
            desde = datetime(anio, i, 1)
            hasta = datetime(anio + (i == 12), i % 12 + 1, 1)
            return desde, hasta, f"en {nombre}"
    siguiente = (mes + timedelta(days=32)).replace(day=1)
    return mes, siguiente, "este mes"


# ---------------------------------------------------------------
# QUÉ PIDE
# ---------------------------------------------------------------

_ARTICULOS = r"(?:(?:el|la|los|las) )?(?:(?:cliente|clienta|proyecto|empresa) )?(?:(?:de|del) )?"

_EMPEZAR = re.compile(
    r"^(?:vale |venga |jarvis |oye )*"
    # "ponme" y "me pongo" solo con "a trabajar": "ponme a Spotify" no es
    # un cliente. Y sin "a" como preposición, por lo mismo.
    r"(?:(?:empiezo|empezamos|comienzo|arranco|fichame|ficho|empiezo a contar|"
    r"empieza a contar(?: horas)?|cuenta horas|apunta horas)(?: a trabajar| a currar)?|"
    r"(?:ponme|me pongo) a (?:trabajar|currar))"
    r" (?:con|para|en) " + _ARTICULOS +
    r"(?P<cliente>[a-z0-9][a-z0-9 ]{0,40})$")

_PARAR = re.compile(
    r"^(?:vale |venga |jarvis |oye |ya )*"
    r"(?:termino|terminamos|he terminado|hemos terminado|acabo|he acabado|"
    r"termine|acabe|pare|lo deje|"
    r"paro|dejo|deja de contar|para de contar|cierra la sesion|fin de la jornada)"
    r"(?: de trabajar| de currar| de contar| horas)?"
    r"(?: (?:con|para) .{1,40}| por hoy| la jornada| por ahora)?$")

# "Terminé a las 7", "he acabado con Acme a las 6 de la tarde", "paré ayer
# a las 8": cerrar a una hora pasada. Es lo que arregla un olvido.
_PARAR_A = re.compile(
    r"^(?:vale |venga |jarvis |oye |ya |pues |no )*"
    r"(?:termine|he terminado|acabe|he acabado|pare|lo deje|deje de trabajar|"
    r"terminamos|hemos terminado)"
    r"(?: de trabajar| de currar)?(?: (?:con|para) [a-z0-9 ]{1,40}?)?"
    r"(?: (?:ayer|hoy))? (?:a las?|sobre las?|hacia las?|a eso de las?) .+$")

_CONSULTA = re.compile(
    r"\b(cuantas horas|cuanto (?:tiempo )?(?:he|llevo|llevamos|hemos) "
    r"(?:trabajado|currado)|cuanto he trabajado|horas (?:llevo|he hecho|he trabajado))\b")

_LLEVO_AHORA = re.compile(r"^(?:jarvis )?cuanto (?:tiempo )?llevo(?: (?:con|trabajando|currando).*)?$")

_EXPORTAR = re.compile(r"\b(exporta|exportame|sacame|pasame|dame) (?:el |un )?"
                       r"(?:informe|csv|excel|resumen|las horas)")


def _cliente_en(t, conocidos):
    """El cliente del que habla una consulta: None si de todos.

    Se prueban TODOS los "con/para/de/en" de la frase, no solo el primero:
    en "¿cuántas horas de trabajo llevo con Acme?" el primero es "de
    trabajo". Gana el que es un cliente conocido; si ninguno lo es, el
    primero que no sea un periodo ("en septiembre", "este mes").
    """
    cortes = r"\b(?:este|esta|hoy|ayer|en|de|con|para|durante|desde|" + "|".join(MESES) + r")\b"
    candidatos = []
    # Cada preposición por separado: un finditer con el resto de la frase
    # se comería las siguientes ("de trabajo llevo CON ACME")
    for m in re.finditer(r"\b(?:con|para|de|en) ", t):
        resto = re.sub("^" + _ARTICULOS, "", t[m.end():])
        resto = re.split(cortes, resto)[0]
        resto = re.sub(r"\b(?:el|la|los|las|mes|semana|ano|pasad[oa]|trabajo|horas)\b", " ", resto)
        resto = " ".join(resto.split())
        if resto:
            candidatos.append(resto)
    for c in candidatos:
        for clave in conocidos:
            if c == clave or c.startswith(clave + " "):
                return clave
    return candidatos[0] if candidatos else None


def orden(texto, hay_abierta=False, conocidos=()):
    """(acción, datos) o None si la frase no va con las horas."""
    t = _normal(texto)
    if not t:
        return None

    m = _EMPEZAR.match(t)
    if m:
        cliente = m.group("cliente").strip()
        # "empiezo con el informe" no es un cliente... salvo que lo sea.
        # Sin una lista de clientes previa no hay forma de saberlo, así que
        # lo que se dice detrás de "empiezo con" SE TOMA como cliente, y
        # por eso se confirma diciendo el nombre en voz alta.
        return ("empezar", cliente)

    if _EXPORTAR.search(t) and "hora" in t:
        return ("exportar", None)

    if _CONSULTA.search(t):
        return ("consultar", _cliente_en(t, conocidos))

    if hay_abierta and _LLEVO_AHORA.match(t):
        return ("actual", None)

    # Parar solo con algo abierto: sin eso "he terminado" es otra cosa
    if hay_abierta and _PARAR_A.match(t):
        return ("parar_a", None)
    if hay_abierta and _PARAR.match(t):
        return ("parar", None)
    return None


def responder(accion, dato, texto, ahora=None):
    """Hace lo pedido y devuelve la frase."""
    ahora = ahora or datetime.now()
    if accion == "empezar":
        return empezar(dato, ahora)
    if accion == "parar":
        return parar(ahora)
    if accion == "parar_a":
        s = abierta()
        if not s:
            return "No estabas contando horas para nadie."
        fin = fin_dicho(texto, s["inicio"], ahora)
        if fin is None:
            return (f"Esa hora no me cuadra: la sesión de {s['cliente']} empezó "
                    f"{momento_hablado(s['inicio'], ahora)}. Dime otra hora.")
        return parar(ahora, fin)
    if accion == "actual":
        s = abierta()
        return (f"Llevas {duracion_hablada((ahora - s['inicio']).total_seconds())} "
                f"con {s['cliente']}.")

    desde, hasta, cuando = periodo(texto, ahora)
    if accion == "exportar":
        nombre = f"horas_{desde:%Y-%m-%d}_a_{hasta - timedelta(days=1):%Y-%m-%d}.csv"
        carpeta = carpeta_csv()
        n = exportar_csv(desde, hasta, carpeta / nombre, ahora)
        if not n:
            return f"No hay horas {cuando} que exportar."
        print(f"[horas] CSV -> {carpeta / nombre}")
        return (f"Te he dejado las horas de {cuando.removeprefix('en ')} en "
                f"Documentos, carpeta Jarvis: {n} {'sesión' if n == 1 else 'sesiones'}.")

    t = totales(desde, hasta, ahora)
    if dato:
        dato = cliente_parecido(dato, clientes()) or dato
        nombre, seg = t.get(dato, (clientes().get(dato, dato.title()), 0))
        if not seg:
            return f"No tienes horas con {nombre} {cuando}."
        return f"Con {nombre} llevas {duracion_hablada(seg)} {cuando}."
    if not t:
        return f"No has apuntado horas {cuando}."
    partes = [f"{n}, {duracion_hablada(s)}" for n, s in t.values()]
    if len(partes) == 1:
        return f"{cuando.capitalize()}: {partes[0]}."
    total = duracion_hablada(sum(s for _, s in t.values()))
    return f"{cuando.capitalize()}: {'; '.join(partes)}. En total, {total}."


# ---------------------------------------------------------------
# CONSOLA
# ---------------------------------------------------------------

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    memoria.preparar()
    preparar()
    if "--csv" in sys.argv:
        i = sys.argv.index("--csv")
        anio, mes = map(int, sys.argv[i + 1].split("-"))
        desde = datetime(anio, mes, 1)
        hasta = datetime(anio + (mes == 12), mes % 12 + 1, 1)
        ruta = Path(f"horas_{anio}-{mes:02d}.csv")
        n = exportar_csv(desde, hasta, ruta)
        print(f"{n} sesiones -> {ruta.resolve()}")
    else:
        print(responder("consultar", None, "este mes"))
