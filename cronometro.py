"""El cronómetro de Jarvis: se maneja hablando o con los botones del panel.

Por qué uno propio y no la app Reloj de Windows: controlar la ventana de
otro programa por voz obliga a simular clics sobre botones cuyo nombre
cambia con el idioma y la versión, y desde el móvil no se vería. Este vive
en la página, así que funciona igual en el PC que en el móvil.

Las órdenes NO pasan por el modelo. "Para" tiene que parar en el acto, y
un 8B tardaría un segundo en decidir algo que una lista de verbos resuelve
sin fallar. Además "para" es una palabra corriente ("un regalo para Ana"):
suelta solo se entiende como orden si el cronómetro está a la vista.

Stdlib pura y con el reloj inyectable: los tests no esperan de verdad.
"""

import re
import time
import unicodedata

ACCIONES = ("abrir", "empezar", "pausar", "reanudar", "reiniciar",
            "consultar", "cerrar")


def _normal(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s).split()


# ---------------------------------------------------------------
# QUÉ PIDE
# ---------------------------------------------------------------

# Verbo -> acción. Manda el PRIMER verbo de la frase: "pon un cronómetro
# para la pasta" empieza (pon), no pausa (para).
VERBOS = {
    "empezar":   {"empieza", "empiezalo", "empezar", "inicia", "inicialo",
                  "iniciar", "arranca", "arrancalo", "arrancar", "comienza",
                  "pon", "ponme", "poner", "ponlo", "activa", "activalo",
                  "enciende", "enciendelo", "encender", "dale", "start",
                  # en primera persona: "lo empiezo al cronómetro"
                  "empiezo", "enciendo", "arranco", "inicio", "pongo",
                  # de usted, como mucha gente habla a un asistente
                  # (eval --voz: "inicia" salía "inicie")
                  "inicie", "empiece", "arranque", "ponga", "active"},
    "pausar":    {"pausa", "pausalo", "pausar", "para", "paralo", "parar",
                  "deten", "detenlo", "detener", "stop", "congela",
                  "pare", "detenga", "pause"},
    "reanudar":  {"reanuda", "reanudalo", "reanudar", "continua", "continuar",
                  "sigue", "seguir", "continue", "siga", "reanude"},
    "reiniciar": {"reinicia", "reinicialo", "reiniciar", "resetea", "reset",
                  "reinicie"},
    "cerrar":    {"cierra", "cierralo", "cerrar", "quita", "quitalo",
                  "oculta", "apaga", "apagalo"},
    "consultar": {"cuanto", "lleva", "llevo", "llevamos", "marca"},
    "abrir":     {"abre", "abreme", "abrir", "muestra", "ensena", "saca"},
}
_ACCION_DE = {v: a for a, vs in VERBOS.items() for v in vs}

# "Pon el cronómetro a cero" empieza por "pon", pero es un reinicio
_A_CERO = re.compile(r"\b(a cero|desde cero|vuelve a empezar|otra vez desde)\b")

# Sueltas, sin decir "cronómetro": solo valen con el panel abierto y si la
# frase es SOLO eso. "Para" dentro de una frase larga es una preposición.
SUELTAS = {
    "pausa": "pausar", "para": "pausar", "paralo": "pausar",
    "pausalo": "pausar", "stop": "pausar", "detenlo": "pausar",
    "sigue": "reanudar", "continua": "reanudar", "reanuda": "reanudar",
    "reanudalo": "reanudar",
    "empieza": "empezar", "empiezalo": "empezar", "arranca": "empezar",
    "dale": "empezar", "venga": "empezar", "start": "empezar",
    "en marcha": "empezar", "ponlo en marcha": "empezar", "ponlo": "empezar",
    "arrancalo": "empezar", "enciendelo": "empezar", "enciende": "empezar",
    "activalo": "empezar", "activa": "empezar", "ponlo a contar": "empezar",
    "a contar": "empezar", "empieza a contar": "empezar",
    "reinicia": "reiniciar", "reinicialo": "reiniciar", "a cero": "reiniciar",
    "cierralo": "cerrar", "quitalo": "cerrar",
    "cuanto llevo": "consultar", "cuanto lleva": "consultar",
    "cuanto llevamos": "consultar", "tiempo": "consultar",
}
# Lo que se dice alrededor de una orden sin cambiarla
_RELLENO = {"jarvis", "vale", "ok", "venga", "ya", "por", "favor", "porfa",
            "ahora", "eh", "oye", "lo", "el", "la"}


# Preguntar el tiempo admite mil formas ("dime cuánto tiempo llevamos
# actualmente", "¿qué tiempo lleva?"). En vez de listarlas enteras: vale si
# habla de cuánto/tiempo y TODAS sus palabras son de preguntar la hora del
# cronómetro. "¿Cuánto cuesta el pan?" trae "cuesta" y "pan": no cuela.
_PIDE_TIEMPO = {"cuanto", "tiempo"}
_DE_PREGUNTAR = {
    "dime", "di", "me", "cuanto", "cuantos", "que", "tiempo", "hora",
    "lleva", "llevo", "llevamos", "llevas", "va", "vamos", "marca", "pone",
    "hay", "exacto", "exactamente", "exacta", "actualmente", "ahora",
    "ya", "el", "la", "lo", "del", "de", "en", "total", "minutos",
    "segundos", "jarvis", "oye", "vale", "cronometro", "por", "favor",
}


def _pregunta_tiempo(palabras):
    if not _PIDE_TIEMPO & set(palabras):
        return None
    if not all(p in _DE_PREGUNTAR for p in palabras):
        return None
    return "consultar_exacto" if any(p.startswith("exact") for p in palabras)         else "consultar"


# Lo que Whisper escribe cuando oye estos verbos. "Enciende" suena
# "en-cien-de" y salía "en 100 el cronómetro": sin verbo conocido, la frase
# iba al modelo y acababa apuntada como una TAREA llamada "cronómetro".
_OIDO_MAL = [
    (re.compile(r"\ben (?:100|cien)(?: de)?\b"), "enciende"),
    (re.compile(r"\bencienda\b"), "enciende"),
    (re.compile(r"\ben pieza\b"), "empieza"),
    (re.compile(r"\bre inicia\b"), "reinicia"),
    (re.compile(r"\bpa usa\b"), "pausa"),
    # Estos los encontró eval_ordenes.py --voz (Piper lo dice, Whisper lo oye)
    (re.compile(r"\bponen marcha\b"), "pon en marcha"),
]

# Palabras que convierten la frase en una pregunta o un comentario SOBRE el
# cronómetro, no en una orden: esas siguen su camino al conversador
_HABLA_DE = {"que", "cual", "como", "por", "para", "tenia", "tengo", "tiene",
             "compre", "comprar", "regalo", "regalaron", "funciona", "sirve",
             "es", "era"}
# Y estas, aunque lleven un verbo de orden: "¿PARA qué sirve un cronómetro?"
# lo pausaba, porque "para" es también el verbo de pararlo
_PREGUNTA_SOBRE = re.compile(r"\b(?:que es|para que|como funciona|cual es|sirve|"
                             r"tenia|regal|compr)")


def _es_crono(palabra):
    """"cronómetro", "cronometrar" y también "crono", como se dice hablando."""
    # "conómetro": Whisper se come a veces la r (eval --voz)
    return (palabra.startswith(("cronometr", "conometr"))
            or palabra in ("crono", "cronos"))


def sin_tildes(texto):
    return " ".join(_normal(texto))


# Lo que queda de una "tarea" que en realidad era una orden mal entendida
_SIN_CONTENIDO = {
    "cronometro", "temporizador", "pomodoro", "jarvis", "el", "la", "lo", "los",
    "al", "a", "de", "del", "en", "que", "un", "una", "y", "me", "ya", "100",
    "cien", "minutos", "minuto", "hayan", "pasado", "avisar", "avisame",
    "avisa", "pon", "ponme", "poner",
} | {v for vs in VERBOS.values() for v in vs}


def es_orden_sin_contenido(texto):
    """"empiezo al cronómetro" -> True: no hay nada que apuntar.
    "comprar un cronómetro nuevo" -> False: eso sí es una tarea."""
    palabras = _normal(texto)
    menciona = any(_es_crono(p) or p.startswith(("temporizador", "pomodoro"))
                   for p in palabras)
    return menciona and all(p in _SIN_CONTENIDO or p.isdigit() for p in palabras)


def trozos(texto):
    """"Empieza el cronómetro y avísame en 25 minutos" -> dos órdenes."""
    partes = re.split(r"\s*,?\s+y\s+(?:luego\s+|despues\s+)?", texto.strip())
    return [p for p in partes if p.strip()]


def orden(texto, visible=False):
    """La acción que pide la frase, o None si no va con el cronómetro."""
    frase = " ".join(_normal(texto))
    for patron, bueno in _OIDO_MAL:
        frase = patron.sub(bueno, frase)
    palabras = frase.split()

    if any(_es_crono(p) for p in palabras):
        pregunta = _pregunta_tiempo(palabras)
        if pregunta:
            return pregunta
        if _PREGUNTA_SOBRE.search(frase):
            return None
        if _A_CERO.search(frase):
            return "reiniciar"
        pedidas = [_ACCION_DE[p] for p in palabras if p in _ACCION_DE]
        # "Abre el cronómetro y empiézalo": abrir es lo de menos
        fuertes = [a for a in pedidas if a != "abrir"]
        if fuertes or pedidas:
            return (fuertes or pedidas)[0]
        # "El cronómetro" a secas es pedir verlo; "¿qué es un cronómetro?"
        # es una pregunta y sigue su camino hacia el conversador
        resto = [p for p in palabras if p not in _RELLENO and p not in ("un", "mi")]
        if len(resto) == 1:
            return "abrir"
        # Una frase corta con "cronómetro" que no se entiende del todo es,
        # casi seguro, una orden mal transcrita. Mejor abrirlo (y que diga
        # "dime empieza cuando quieras") que dejar que el modelo apunte una
        # tarea llamada "cronómetro". Si habla DEL cronómetro, no.
        if len(resto) <= 3 and not _HABLA_DE & set(palabras):
            return "abrir"
        return None

    if not visible:
        return None
    pregunta = _pregunta_tiempo(palabras)
    if pregunta:
        return pregunta
    nucleo = [p for p in palabras if p not in _RELLENO]
    # "venga" solo, sin nada más, es "dale"
    if not nucleo and "venga" in palabras:
        return "empezar"
    return SUELTAS.get(" ".join(nucleo))


# ---------------------------------------------------------------
# CÓMO SE DICE UN TIEMPO
# ---------------------------------------------------------------

def _unidad(n, uno, varios):
    return uno if n == 1 else f"{n} {varios}"


def tiempo_hablado(segundos, decimas=False):
    """125 -> "2 minutos y 5 segundos". En palabras para uno: Piper lee
    "1 minuto" como "uno minuto". Con decimas, 83.7 -> "un minuto, 23
    segundos y 7 décimas": es lo que se pide al decir "exacto"."""
    total = int(segundos)
    d = int(round((segundos - total) * 10)) % 10 if decimas else 0
    if total < 1:
        return f"{_unidad(d, 'una décima', 'décimas')}" if d else "menos de un segundo"
    h, resto = divmod(total, 3600)
    m, s = divmod(resto, 60)
    partes = []
    if h:
        partes.append(_unidad(h, "una hora", "horas"))
    if m:
        partes.append(_unidad(m, "un minuto", "minutos"))
    # Con horas, los segundos sobran al decirlo
    if s and not h:
        partes.append(_unidad(s, "un segundo", "segundos"))
    if d and not h:
        partes.append(_unidad(d, "una décima", "décimas"))
    if len(partes) == 1:
        return partes[0]
    return ", ".join(partes[:-1]) + " y " + partes[-1]


# ---------------------------------------------------------------
# EL CRONÓMETRO
# ---------------------------------------------------------------

class Cronometro:
    def __init__(self, reloj=time.monotonic):
        self.reloj = reloj
        self.visible = False
        self.acumulado = 0.0      # lo contado en tramos ya cerrados
        self.desde = None         # cuándo arrancó el tramo actual

    @property
    def corriendo(self):
        return self.desde is not None

    def transcurrido(self):
        tramo = self.reloj() - self.desde if self.corriendo else 0.0
        return self.acumulado + tramo

    def estado(self):
        """Lo que necesita el panel para pintarse y seguir contando solo."""
        return {"visible": self.visible, "corriendo": self.corriendo,
                "segundos": round(self.transcurrido(), 2)}

    def _arrancar(self):
        self.desde = self.reloj()

    def _detener(self):
        self.acumulado = self.transcurrido()
        self.desde = None

    def aplicar(self, accion):
        """Hace la acción y devuelve la frase que la cuenta."""
        lleva = tiempo_hablado(self.transcurrido())

        if accion == "abrir":
            self.visible = True
            if self.corriendo:
                return f"Ya está en marcha: lleva {lleva}."
            if self.acumulado:
                return f"Aquí lo tienes, parado en {lleva}."
            return "Aquí lo tienes. Dime empieza cuando quieras."

        if accion in ("empezar", "reanudar"):
            self.visible = True
            if self.corriendo:
                return f"Ya está en marcha: lleva {lleva}."
            # "Empieza" con tiempo ya contado sigue desde ahí, como el botón
            # de cualquier cronómetro. Para empezar de cero está "reinicia".
            sigue = self.acumulado > 0
            self._arrancar()
            return f"Sigue desde {lleva}." if sigue else "En marcha."

        if accion == "pausar":
            if not self.corriendo:
                if self.acumulado:
                    return f"Ya estaba parado en {lleva}."
                return "El cronómetro no está en marcha."
            self._detener()
            return f"Parado en {tiempo_hablado(self.acumulado)}."

        if accion == "reiniciar":
            self.visible = True
            self.acumulado = 0.0
            if self.corriendo:
                self._arrancar()
                return "Vuelta a cero, y sigue contando."
            return "A cero."

        if accion in ("consultar", "consultar_exacto"):
            if accion == "consultar_exacto":
                lleva = tiempo_hablado(self.transcurrido(), decimas=True)
            if self.corriendo:
                return f"Lleva {lleva}."
            if self.acumulado:
                return f"Está parado en {lleva}."
            return "El cronómetro está a cero."

        if accion == "cerrar":
            contado = self.transcurrido()
            self.visible = False
            self.acumulado, self.desde = 0.0, None
            if contado >= 1:
                return f"Cronómetro cerrado. Se quedó en {tiempo_hablado(contado)}."
            return "Cronómetro cerrado."

        raise ValueError(f"acción desconocida: {accion}")
