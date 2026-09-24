"""Descartar lo que Whisper se inventa cuando no has dicho nada.

EL PROBLEMA

Whisper NO devuelve silencio ante el silencio: devuelve una frase. Con un
segundo de nada escribió "Este es el canal de subtítulos en español de la
Iglesia..."; con ruido de fondo, "¡Suscríbete!"; con un golpe en la mesa,
"Subtítulos por la comunidad de Amara.org". Son restos de los vídeos con
los que se entrenó.

En un dictado eso es una molestia. Aquí es peligroso: si la alucinación
cae justo en una palabra que es orden ("para", "sigue", "dale"), Jarvis
actúa sin que hayas hablado.

CÓMO SE DISTINGUE

Whisper da, por cada trozo, la probabilidad de que ahí NO haya voz. Medido
con este proyecto (silencio, ruido a tres niveles, un golpe seco, y ocho
órdenes dichas de verdad):

    sin voz      0,76  0,88  0,89  0,90
    con voz      0,02  0,06  0,13  0,17  0,18  0,20  0,21  0,41

El hueco entre 0,41 y 0,76 es grande, así que el corte va en 0,6. El
avg_logprob NO sirve para esto: "Para" (voz real, media palabra) da -1,05,
peor que varias alucinaciones.

Y como segunda red, las frases que Whisper repite siempre al alucinar.
Solo se descartan si son TODO lo que se oyó: "gracias" suelto puede ser
una alucinación, pero "gracias, muy amable" es una persona hablando.

Stdlib pura: corre en el CI.
"""

import re
import unicodedata

# Por encima de esto se da por hecho que no hay voz
SIN_HABLA_MAX = 0.6

# Lo que Whisper suelta cuando no hay nada que transcribir. Vienen de los
# subtítulos de YouTube con los que se entrenó.
ALUCINACIONES = {
    "gracias", "gracias por ver el video", "gracias por ver el vídeo",
    "gracias por vernos", "suscribete", "suscribete al canal",
    "no te olvides de suscribirte", "subtitulos por la comunidad de amara org",
    "subtitulos realizados por la comunidad de amara org",
    "subtitulado por la comunidad de amara org",
    "mas informacion en www com", "hasta la proxima", "hasta luego",
    "nos vemos en el proximo video", "musica", "aplausos", "risas",
    "aqui termina el video", "eso es todo por hoy",
}

# Y los principios inconfundibles: siempre son basura, diga lo que diga detrás
PRINCIPIOS = (
    "este es el canal de subtitulos",
    "subtitulos por la comunidad",
    "subtitulos realizados por",
    "subtitulado por la comunidad",
    "gracias por ver el video",
    "gracias por ver el vídeo",
)


def _normal(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def parece_alucinacion(texto, sin_habla=None):
    """(True, motivo) si hay que tirar esta transcripción.

    `sin_habla` es la probabilidad de que NO haya voz; se pasa la MENOR de
    todos los trozos, porque basta con que uno tenga voz de verdad para que
    la frase valga.
    """
    t = _normal(texto)
    if not t:
        return True, "vacío"
    if sin_habla is not None and sin_habla > SIN_HABLA_MAX:
        return True, f"sin voz (prob {sin_habla:.2f})"
    if t in ALUCINACIONES:
        return True, f"frase típica de Whisper: {texto.strip()!r}"
    if t.startswith(PRINCIPIOS):
        return True, f"frase típica de Whisper: {texto.strip()[:40]!r}"
    return False, ""
