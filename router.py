"""Decidir QUE hacer con lo que ha dicho el usuario.

Dos piezas:

  - el router: una llamada al modelo con las herramientas conectadas y un
    prompt seco, que solo decide cual llamar.
  - los guardas: ~12 filtros deterministas entre lo que propone el modelo
    y lo que se ejecuta de verdad. Cada uno salio de un fallo real, y
    todos estan documentados con el caso que los provoco.

La regla de fondo: el modelo propone, el codigo dispone. El modelo elige
QUE funcion llamar; la fecha, la hora y el texto los calcula codigo
determinista y se guardan literales.
"""

import re
import unicodedata
from datetime import datetime

import ollama

import correo
import memoria
import tiempo
import sistema
from ajustes import MODELO_LLM, NOMBRE, NUM_CTX, PROMPT_SISTEMA


# Dos llamadas al modelo, no una. Se probó a pasarle las herramientas
# directamente al asistente y se estropea la conversación: empieza a
# contestar "no tengo una función para eso" a preguntas normales, o
# narra en voz alta que no le hace falta ninguna herramienta.
# Separando router y conversador se obtuvo 9/9 de enrutado y 5/5 de
# conversación útil.
#
# IMPORTANTE: las dos llamadas deben usar el MISMO num_ctx. Si cambia,
# Ollama recarga el modelo entero en cada turno: medido, 17,3 s por
# pregunta contra 1,2 s.
# ---------------------------------------------------------------

HERRAMIENTAS = [
    {"type": "function", "function": {
        "name": "que_hora_es",
        "description": "Consulta el reloj del ordenador para saber la hora y la fecha de ahora mismo.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "anadir_tarea",
        "description": "Guarda una tarea o recordatorio nuevo en la lista del usuario.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string", "description": "Qué hay que hacer, sin el momento"},
            "cuando": {"type": "string",
                       "description": "Copia LITERAL de lo que dijo el usuario sobre CUÁNDO, "
                                      "sin traducirlo, resumirlo ni cambiarlo. Si dijo "
                                      "'a las 8.40', pon 'a las 8.40'. Si dijo 'el jueves a "
                                      "las 5', pon 'el jueves a las 5'. NUNCA inventes un "
                                      "momento que el usuario no haya dicho: si no dijo "
                                      "cuándo, déjalo vacío."}},
            "required": ["texto"]}}},
    {"type": "function", "function": {
        "name": "listar_tareas",
        "description": "Consulta las tareas pendientes que el usuario tiene guardadas.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string",
                      "description": "Nombre de UNA tarea concreta, si el usuario "
                                     "pregunta por algo en particular: '¿a qué hora "
                                     "tengo el test?' -> 'test'. Vacío si pregunta "
                                     "en general por lo que tiene pendiente."},
            "cuando": {"type": "string",
                       "description": "Copia LITERAL y COMPLETA de las palabras del usuario "
                                      "sobre el momento, sin traducirlas, resumirlas ni "
                                      "acortarlas. Si dice 'mañana por la tarde', pon "
                                      "'mañana por la tarde' ENTERO, no solo 'mañana': "
                                      "la franja del día importa tanto como el día. "
                                      "Otros ejemplos: 'hoy', 'en 30 minutos', 'esta noche', "
                                      "'el jueves por la mañana'. Si da dos horas, copia el "
                                      "tramo COMPLETO: 'entre las 2 y las 5 de la tarde', "
                                      "'mañana de 9 a 11'. Déjalo vacío SOLO si no "
                                      "mencionó ningún momento ('¿qué tengo pendiente?')."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "el_tiempo",
        "description": "El tiempo que hace o va a hacer en un sitio: temperatura, "
                       "lluvia, si hará frío o calor. Úsala SIEMPRE para el tiempo, "
                       "en vez de buscar en internet.",
        "parameters": {"type": "object", "properties": {
            "lugar": {"type": "string",
                      "description": "Ciudad o pueblo. Vacío si no lo dice: se usa "
                                     "donde vive"},
            "cuando": {"type": "string",
                       "description": "'hoy', 'mañana', 'el jueves'... Vacío para ahora"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "buscar_en_web",
        "description": "Busca en internet información ACTUAL que no puedes saber: "
                       "noticias, resultados deportivos, clasificaciones, "
                       "precios, cotizaciones, qué ha pasado hoy, o cualquier cosa "
                       "posterior a tu entrenamiento. NO la uses para cultura general, "
                       "historia, definiciones ni cosas que ya sabes.",
        "parameters": {"type": "object", "properties": {
            "consulta": {"type": "string",
                         "description": "Qué buscar, en pocas palabras y como se "
                                        "escribiría en un buscador: 'clasificación "
                                        "Liga española', 'precio del bitcoin'"}},
            "required": ["consulta"]}}},
    {"type": "function", "function": {
        "name": "abrir_programa",
        "description": "Abre una aplicación del ordenador: el navegador, Spotify, "
                       "la calculadora, el explorador de archivos, VS Code, el "
                       "bloc de notas, el correo, los ajustes o la terminal.",
        "parameters": {"type": "object", "properties": {
            "programa": {"type": "string",
                         "description": "Qué abrir, con las palabras del usuario: "
                                        "'spotify', 'el navegador', 'la calculadora'"}},
            "required": ["programa"]}}},
    {"type": "function", "function": {
        "name": "enviar_correo",
        "description": "Escribe y manda un correo por Gmail a alguien de la agenda "
                       "del usuario. Úsalo cuando pida escribir, mandar o enviar "
                       "un correo, un email o un mensaje a una persona.",
        "parameters": {"type": "object", "properties": {
            "destinatario": {"type": "string",
                             "description": "A quién, con el nombre que usó el "
                                            "usuario: 'Ana', 'mi jefe'. NUNCA una "
                                            "dirección de correo: se saca de su agenda."},
            "asunto": {"type": "string",
                       "description": "Asunto corto, de menos de ocho palabras"},
            "mensaje": {"type": "string",
                        "description": "El correo ya redactado y completo, con "
                                       "saludo y despedida, a partir de lo que "
                                       "pidió el usuario. Escríbelo tú bien, no "
                                       "copies su frase tal cual."}},
            "required": ["destinatario", "mensaje"]}}},
    {"type": "function", "function": {
        "name": "leer_correos",
        "description": "Mira la bandeja de entrada de Gmail del usuario: si "
                       "le ha llegado algo nuevo, quién le ha escrito, de qué "
                       "van los correos o un resumen de lo recibido. Es para "
                       "correos que LE HAN LLEGADO a él. Para escribir y "
                       "mandar uno se usa enviar_correo, no esta.",
        "parameters": {"type": "object", "properties": {
            "solo_nuevos": {"type": "boolean",
                            "description": "true si pregunta por lo nuevo, lo "
                                           "sin leer o lo que le ha llegado; "
                                           "false si pide sus últimos correos "
                                           "en general"},
            "de": {"type": "string",
                   "description": "Nombre de la persona, si pregunta si le ha "
                                  "escrito alguien concreto: 'Ana', 'mi jefe'. "
                                  "Vacío si pregunta en general."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "cerrar_programa",
        "description": "Cierra una aplicación que está abierta ahora mismo: "
                       "'cierra spotify', 'cierra el navegador', 'quita smite'. "
                       "Se cierra con normalidad, así que si hay algo sin guardar "
                       "la aplicación lo preguntará.",
        "parameters": {"type": "object", "properties": {
            "programa": {"type": "string",
                         "description": "Qué cerrar, con las palabras del usuario"}},
            "required": ["programa"]}}},
    {"type": "function", "function": {
        "name": "ejecutar_atajo",
        "description": "Lanza una tarea que el usuario tiene configurada en su "
                       "fichero de atajos: copias de seguridad, scripts, rutinas "
                       "suyas. Usalo cuando pida algo por su nombre y no encaje "
                       "en ninguna otra herramienta.",
        "parameters": {"type": "object", "properties": {
            "nombre": {"type": "string",
                       "description": "Nombre del atajo, con las palabras del usuario"}},
            "required": ["nombre"]}}},
    {"type": "function", "function": {
        "name": "control_sistema",
        "description": "Controla el ordenador: apagarlo, reiniciarlo, bloquear la "
                       "pantalla, cancelar un apagado programado, o subir, bajar "
                       "y silenciar el volumen.",
        "parameters": {"type": "object", "properties": {
            "accion": {"type": "string",
                       "enum": ["apagar", "reiniciar", "suspender", "bloquear",
                                "cancelar", "subir volumen", "bajar volumen",
                                "silenciar", "captura", "minimizar"],
                       "description": "Exactamente una de las opciones de la lista"}},
            "required": ["accion"]}}},
    {"type": "function", "function": {
        "name": "recordar_dato",
        "description": "Guarda un dato que el usuario cuenta SOBRE SÍ MISMO o su "
                       "entorno, y que no es algo que tenga que hacer: cómo se "
                       "llama, dónde vive, a qué se dedica, sus gustos, sus "
                       "alergias, nombres de familiares. No es una tarea: no se "
                       "hace ni se completa, solo se recuerda.",
        "parameters": {"type": "object", "properties": {
            "dato": {"type": "string",
                     "description": "El dato como FRASE COMPLETA en tercera "
                                    "persona, diciendo SIEMPRE de quién se habla. "
                                    "'me llamo Toti' -> 'se llama Toti'. "
                                    "'mi novia se llama Verónica' -> 'su novia se "
                                    "llama Verónica'. 'mi jefe es Luis' -> 'su jefe "
                                    "es Luis'. NUNCA pierdas de quién es el dato: "
                                    "'se llama Verónica' a secas sería un error, "
                                    "porque parece que el usuario se llama así."}},
            "required": ["dato"]}}},
    {"type": "function", "function": {
        "name": "completar_tarea",
        "description": "Quita una tarea de la lista, porque el usuario ya la ha "
                       "hecho o porque pide borrarla, cancelarla o anularla. "
                       "Sirve tanto para 'ya he comprado el pan' como para "
                       "'bórrame el dentista de mañana'.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string",
                      "description": "Qué tarea quitar, con las palabras del "
                                     "usuario pero SIN el verbo de borrar: si "
                                     "dice 'bórrame el test de mañana', pon "
                                     "'el test de mañana'"}},
            "required": ["texto"]}}},
]

PROMPT_ROUTER = """Decide si la frase del usuario necesita una de tus herramientas.

Usa anadir_tarea cuando el usuario cuente algo que tiene que hacer:
"recuérdame X", "apunta X", "tengo que X", "no se me olvide X".

Usa listar_tareas cuando pregunte por lo que tiene pendiente:
"¿qué tengo que hacer?", "¿qué tengo hoy?", "¿qué me queda?",
"¿qué tenía apuntado?", "mis tareas", "mi agenda".

Usa completar_tarea cuando diga que ya ha hecho algo, O cuando pida QUITAR
algo que ya tenía apuntado:
"ya he X", "ya está lo de X", "hecho lo de X", "terminé X",
"quita X", "bórrame X", "borra lo de X", "elimina X", "cancela X",
"anula lo de X", "ya no tengo que X", "olvídate de X".

OJO: "bórrame el test de mañana" NO es una tarea nueva llamada "borrarme el
test". Es quitar de la lista el test que ya estaba apuntado.

Usa listar_tareas TAMBIÉN cuando pregunte por UNA tarea concreta, poniendo
su nombre en "texto" y dejando "cuando" vacío:
"¿a qué hora tengo el test?"     -> texto="test"
"¿cuándo tengo el dentista?"     -> texto="dentista"
"¿a qué hora es la reunión?"     -> texto="reunión"
Eso pregunta por su agenda, NO por el reloj.

Usa que_hora_es SOLO cuando pregunte qué hora es AHORA o en qué fecha
estamos, sin referirse a ninguna tarea suya.

Usa abrir_programa cuando pida abrir una aplicación:
"abre spotify", "pon música", "ábreme el navegador", "abre la calculadora".

Usa enviar_correo cuando pida escribir o mandar un correo a alguien:
"mándale un correo a Ana diciendo que llego tarde", "escríbele a mi jefe".
Tú REDACTAS el mensaje completo, con saludo y despedida, a partir de lo
que te ha dicho. El destinatario va con su NOMBRE, nunca con su dirección.

Usa cerrar_programa cuando pida cerrar una que ya está abierta:
"cierra spotify", "cierra el navegador", "quítame el discord", "sal del juego".

Usa control_sistema para el ordenador en sí:
"apaga el ordenador", "reinicia", "bloquea la pantalla", "cancela el apagado",
"sube el volumen", "baja el volumen", "silencia".

OJO con apagar y reiniciar: solo si lo PIDE. "El ordenador va lento" o
"¿se apaga solo?" NO son órdenes de apagado.

Usa el_tiempo para el tiempo, no busques en internet:
"¿qué tiempo hace?", "¿va a llover mañana?", "¿qué temperatura hay en Bilbao?",
"¿hace frío en Madrid?". Si no dice el sitio, deja lugar vacío.

Usa recordar_dato cuando cuente algo sobre sí mismo o sobre su gente, que no
hay que hacer: "me llamo X", "soy X", "vivo en X", "mi novia se llama X",
"mi hermana es X", "soy alérgico a X", "me gusta X", "trabajo en X".
Eso no son tareas: no se hacen ni se completan, solo se recuerdan.

El dato va en tercera persona y SIEMPRE con su sujeto:
"me llamo Toti"                -> "se llama Toti"
"mi novia se llama Verónica"   -> "su novia se llama Verónica"
"mi hermana vive en Vigo"      -> "su hermana vive en Vigo"
Nunca lo recortes a "se llama Verónica": se perdería de quién hablas.

anadir_tarea SOLO si el usuario dice QUÉ hay que hacer. Si la frase no
nombra ninguna acción concreta, no la apuntes. "¿Tengo algo que hacer esta
noche?" es una CONSULTA (listar_tareas), no una tarea llamada "algo que
hacer". Nunca apuntes una tarea cuyo texto sea "algo", "cosas" o "vale".

NO USES NINGUNA HERRAMIENTA en estos casos, que son conversación normal:
"¿qué tal has pasado el día?"   -> te pregunta cómo estás
"¿cómo ha ido la mañana?"       -> te pregunta cómo estás
"¿qué has hecho hoy?"           -> te pregunta por ti, no por su agenda
"¿te acuerdas de lo de ayer?"   -> charla sobre el pasado
"mañana es viernes, ¿no?"       -> comentario, no pregunta la hora
"hoy hace buen día"             -> comentario

Una PREGUNTA sobre el pasado o sobre cómo estás NUNCA es una tarea nueva.
Solo se apunta una tarea si el usuario ENCARGA algo que hay que hacer.
Que una frase contenga "hoy", "mañana" o "el día" no la convierte en tarea.

Para cualquier otra cosa (charla, cultura, chistes, opiniones, saludos,
preguntas de conocimiento) NO uses ninguna herramienta y no respondas nada."""

FUNCIONES = {
    "que_hora_es": lambda **k: memoria.que_hora_es(),
    "anadir_tarea": lambda texto="", cuando="", **k: memoria.anadir_tarea(texto, cuando),
    "listar_tareas": lambda cuando="", texto="", **k: memoria.listar_tareas(cuando, texto),
    "completar_tarea": lambda texto="", **k: memoria.completar_tarea(texto),
    "recordar_dato": lambda dato="", **k: memoria.recordar_dato(dato),
    "abrir_programa": lambda programa="", **k: sistema.abrir_programa(programa),
    "cerrar_programa": lambda programa="", **k: sistema.cerrar_programa(programa),
    "control_sistema": lambda accion="", **k: sistema.ejecutar_accion(accion),
    "ejecutar_atajo": lambda nombre="", **k: sistema.ejecutar_atajo(nombre),
    "el_tiempo": lambda lugar="", cuando="", **k: _el_tiempo(lugar, cuando),
}


def _el_tiempo(lugar, cuando):
    """Sin lugar, el de casa; y si tampoco se sabe, se pregunta."""
    sitio = (lugar or "").strip() or tiempo.donde_vive()
    if not sitio:
        return ("¿De qué ciudad? Si me dices dónde vives, lo recuerdo "
                "para la próxima.")
    return tiempo.el_tiempo(sitio, cuando)


def prompt_con_fecha():
    """El prompt de sistema con la fecha de hoy metida dentro.

    El modelo no tiene reloj: sin esto se inventa en qué día vive y calcula
    mal cualquier cosa relativa ("dentro de dos semanas", "el mes que viene").
    Se recalcula en cada turno para que siga valiendo si el asistente lleva
    horas abierto y ha pasado la medianoche.
    """
    a = datetime.now()
    partes = [PROMPT_SISTEMA,
              f"Hoy es {memoria.DIAS_ES[a.weekday()]} "
              f"{a.day} de {memoria.MESES_ES[a.month - 1]} de {a.year}, "
              f"y son las {a.hour}:{a.minute:02d}."]

    # Lo que Jarvis sabe del usuario. Esto es lo que separa un asistente que
    # te conoce de uno que empieza de cero en cada conversación: si le has
    # dicho cómo te llamas, tiene que poder responder cuando se lo preguntes.
    datos = memoria.datos_conocidos()
    if datos:
        partes.append(
            "LO QUE SABES DEL USUARIO\n" +
            "\n".join(f"- {memoria.como_frase(d)}" for d in datos) +
            "\n\nEso es TODO lo que sabes de él, y es fiable: si la respuesta "
            "está ahí, dala directamente sin decir que no tienes información. "
            "Si no está, dilo y ya. No inventes nada sobre su vida ni sobre "
            "las personas que menciona.")
    return "\n\n".join(partes)


# Verbos con los que alguien pide de verdad que le apunten algo. Si no
# aparece ninguno, una frase interrogativa no es un encargo.
VERBOS_TAREA = (
    "apunta", "apunte", "anota", "anote", "añade", "agrega", "mete",
    "recuerdame", "recuérdame", "recuerda", "acuerdate", "acuérdate",
    "tengo que", "he de", "no se me olvide", "no me deje", "avisame", "avísame",
)


def _sin_tildes(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def es_pregunta(t):
    """¿La frase (ya sin tildes y en minúsculas) es una pregunta?

    No basta con mirar los signos: Whisper los pierde a menudo, sobre todo
    si la entonación no sube al final (eval_router.py --voz: "¿tengo algo
    esta noche?" llegó como "Tengo algo esta noche." y se APUNTÓ como
    tarea). Por eso también cuenta la forma: "tengo algo", "hay algo".
    """
    return t.startswith("¿") or t.endswith("?") or bool(
        re.match(r"^(que|como|cuando|cuanto|cual|quien|donde|por que)\b", t)
        or es_consulta_de_agenda(t))


def es_consulta_de_agenda(t):
    """"Tengo algo esta noche", "hay algo mañana", "me queda algo": con o
    sin signos, preguntan por la agenda. Nadie apunta nada diciendo así."""
    return bool(re.match(
        r"^(?:y |oye |jarvis |entonces )*(?:tengo|tenemos|hay|me queda|queda)"
        r" (?:algo|alguna|algun|planes|tareas|cosas|pendiente)\b", t.lstrip("¿")))


def parece_encargo(frase):
    """¿Es esto un encargo de apuntar algo, o solo una pregunta de charla?

    Se probó a dejarlo en manos del modelo y acertaba 5 de 10: convertía
    "¿cómo ha ido la mañana?" en una tarea llamada "¿cómo ha ido la mañana?".
    Una pregunta solo es un encargo si además lleva un verbo de encargo
    ("¿me apuntas comprar pan?" sí; "¿qué tal el día?" no).
    """
    t = _sin_tildes(frase).strip()
    if not es_pregunta(t):
        return True
    return any(v in t for v in (_sin_tildes(v) for v in VERBOS_TAREA))


# Frases que el modelo mete como "tarea" cuando en realidad no hay tarea.
# "Vale, perfecto" no es un recado, y "algo que hacer" no dice qué hacer.
TAREAS_VACIAS = {
    "algo", "algo que hacer", "cosas", "cosas que hacer", "nada", "esto",
    "eso", "vale", "perfecto", "gracias", "ok", "si", "no", "bien",
    "una tarea", "tarea", "recordatorio", "hacer algo", "lo de siempre",
}


# Muletillas y acuses de recibo. No son datos sobre nadie: son ruido de
# conversación que el modelo intentaba guardar como si fueran hechos.
RELLENO = {
    "vale", "perfecto", "gracias", "muy amable", "ok", "okay", "de acuerdo",
    "genial", "estupendo", "muy bien", "bien", "claro", "si", "no", "ya",
    "entendido", "correcto", "eso es", "exacto", "nada", "adios", "hasta luego",
}


def dato_vacio(texto):
    """¿Este 'dato' dice algo del usuario, o es solo una muletilla?

    Salió de la evaluación: "Vale, perfecto" se guardaba como un hecho
    sobre el usuario. Se contamina la memoria y encima se la lee luego en
    el prompt de cada turno.
    """
    # La puntuación se quita de CADA palabra, no solo de los extremos:
    # "vale, perfecto" se partía en ["vale,", "perfecto"] y "vale," con la
    # coma pegada no coincidía con nada de la lista.
    palabras = [p.strip(".,;:¿?¡!\"'") for p in _sin_tildes(texto).split()]
    palabras = [p for p in palabras if p]
    if not palabras:
        return "el dato viene vacío"
    if all(p in RELLENO for p in palabras):
        return f"{texto!r} es una muletilla, no un dato"
    if len("".join(palabras)) < 4:
        return f"{texto!r} es demasiado corto"
    return None


def tarea_vacia(texto):
    """¿Esta 'tarea' dice de verdad qué hay que hacer?

    Salió de un caso real: "¿tengo algo que hacer esta noche?" se apuntó
    como una tarea llamada "algo que hacer". Una tarea sin contenido no
    sirve para nada y además ensucia la lista.
    """
    t = _sin_tildes(texto).strip(" .,¿?¡!")
    if not t:
        return "el texto viene vacío"
    if t in TAREAS_VACIAS:
        return f"{texto!r} no dice qué hay que hacer"
    if len(t) < 3:
        return f"{texto!r} es demasiado corto"
    return None


# Verbos con los que se pide información, no se cuenta un dato
VERBOS_PREGUNTA = ("explica", "explicame", "dime", "cuentame", "describe",
                   "define", "sabes", "conoces", "puedes decirme", "que es")


# Marcas de que la frase habla del usuario o de su gente. Sin alguna de
# estas, no es un dato personal por mucho que lo parezca.
MARCAS_PERSONALES = (
    r"\bme\b", r"\bmi\b", r"\bmis\b", r"\bmio\b", r"\bmia\b", r"\bconmigo\b",
    r"\bsoy\b", r"\bestoy\b", r"\btengo\b", r"\bvivo\b", r"\btrabajo\b",
    r"\bllamo\b", r"\bnaci\b", r"\bprefiero\b", r"\bodio\b", r"\bjuego\b",
    r"\bestudio\b", r"\bquiero\b", r"\bsuelo\b",
)


def parece_dato_personal(frase):
    """¿Está el usuario contándome algo suyo, o pidiéndome información?

    Tres cosas distintas que no son un dato personal, y las tres pasaban:
      - "¿Me podrías decir mi nombre?"  pregunta por el dato, no lo aporta
      - "Explícame qué es una API"      pide información
      - "Hoy hace buen día"             habla del tiempo, no de él

    Ese último era el peor: escribía en la memoria una observación sobre
    el clima, y luego se la leía en el prompt de todos los turnos.
    """
    t = _sin_tildes(frase).strip()
    if es_pregunta(t):
        return "recuerda" in t          # "recuerda que soy X" sí vale
    if any(t.startswith(v) for v in VERBOS_PREGUNTA):
        return False
    # Tiene que hablar de él o de los suyos
    return any(re.search(m, t) for m in MARCAS_PERSONALES)


# Señales de que la pregunta va de algo que CAMBIA y no está en el modelo.
# Sin una de estas, buscar en internet es gastar dos segundos y una conexión
# para algo que ya sabe: se midió que buscaba hasta la capital de Francia.
SENALES_ACTUALIDAD = (
    # el momento
    "ahora", "actual", "hoy", "esta semana", "este mes", "ultimo", "ultima",
    "ultimos", "ultimas", "reciente", "novedad", "todavia", "ya ha", "va ganando",
    "que tal va", "como va", "sigue siendo",
    # cosas que cambian solas
    "tiempo hace", "temperatura", "llueve", "lloviendo", "pronostico", "clima",
    "precio", "cuanto cuesta", "cotiza", "bolsa", "euro", "dolar", "bitcoin",
    "noticia", "noticias", "ha pasado", "ha ganado", "gano el", "resultado",
    "resultados", "clasificacion", "partido", "marcador", "estreno", "cartelera",
    "horario", "abierto", "cerrado", "trafico", "vuelo",
)


def merece_busqueda(frase):
    """¿Justifica esta pregunta salir a internet?

    El modelo, si le das una herramienta de búsqueda, la usa para todo:
    medido, buscaba "capital de Francia" y "quién pintó Las Meninas".
    Cada búsqueda son dos segundos y medio y una conexión, así que solo
    se sale fuera cuando la pregunta pide algo que cambia con el tiempo.
    """
    t = _sin_tildes(frase)
    # Si lo pide con todas las letras, se busca y no se discute. Este filtro
    # existe para que el modelo no busque por su cuenta cosas que ya sabe,
    # no para llevarle la contraria al usuario. Pasaba de verdad: a
    # "tienes que buscar en internet cuáles son los mejores trabajos" le
    # contestaba de memoria, y encima empezando por "no tengo acceso a
    # internet" cuando sí lo tiene.
    # El \w{0,4} del final recoge los pronombres pegados: búscaLO,
    # consúltaMELO, míraLA. En español van dentro de la palabra y una
    # lista cerrada de formas se queda corta siempre.
    if re.search(r"\b(busca|buscar|mira|mirar|consulta|consultar|averigua|"
                 r"averiguar|investiga|investigar|informate)\w{0,5}\b"
                 r"[^.]{0,30}"
                 r"\b(internet|la red|la web|google|online|en linea)\b", t):
        return True
    if any(s in t for s in SENALES_ACTUALIDAD):
        return True
    # Un año reciente también cuenta: "quién ganó la liga en 2026"
    return bool(re.search(r"\b20[2-9]\d\b", t))


# Verbos con los que se pide QUITAR algo de la lista, no añadirlo
VERBOS_BORRAR = ("borra", "borrame", "borrarme", "borrar", "borralo", "borrala",
                 "quita", "quitame", "quitar", "quitalo", "quitala",
                 "elimina", "eliminame", "eliminar", "cancela", "cancelame",
                 "cancelar", "anula", "anular", "olvidate de", "ya no tengo que")


def pide_borrar(frase):
    """¿Está pidiendo quitar algo de la lista?

    Salió de un caso real: "vale, puedes borrarme ese test de mañana" se
    apuntó como una tarea NUEVA llamada "borrarme ese test". El modelo solo
    conocía "ya he hecho X" y "quita X" como formas de completar.
    """
    t = _sin_tildes(frase)
    return any(re.search(r"\b" + v.replace(" ", r"\s+"), t) for v in VERBOS_BORRAR)


def sin_verbo_borrar(texto):
    """Quita el verbo de borrar del texto, para poder buscar la tarea.

    "borrarme ese test" no casa con "un test de matemáticas" tan bien como
    "ese test", porque la palabra del verbo mete ruido en la comparación.
    """
    t = texto
    for v in sorted(VERBOS_BORRAR, key=len, reverse=True):
        t = re.sub(r"\b" + v.replace(" ", r"\s+") + r"\b", " ",
                   t, flags=re.IGNORECASE)
    # también sobran las muletillas del principio
    t = re.sub(r"^\s*(vale|oye|jarvis|puedes|por favor|me)\b[\s,]*", " ",
               t.strip(), flags=re.IGNORECASE)
    return " ".join(t.split()) or texto


def cuando_inventado(cuando, frase):
    """¿Se ha sacado el modelo ese "cuándo" de la manga?

    Caso real: a "¿a qué hora tengo el test de matemáticas?" respondió con
    cuando='a las 8 de la mañana'. El usuario no dijo ninguna hora, así que
    el filtro no encontraba nada y contestaba "no tienes nada apuntado para
    a las 8 de la mañana", que suena a alucinación porque lo es.

    La comprobación es simple: alguna palabra con contenido del "cuándo"
    tiene que aparecer también en lo que dijo el usuario.
    """
    if not cuando or not cuando.strip():
        return False
    hueca = {"a", "las", "la", "de", "del", "el", "en", "por", "y", "los"}
    palabras = [p for p in _sin_tildes(cuando).split() if p not in hueca]
    if not palabras:
        return False
    dicho = _sin_tildes(frase)
    return not any(p in dicho for p in palabras)


# Verbos con los que se ORDENA algo al ordenador. Sin uno de estos, la
# frase es un comentario y no debe mover nada.
VERBOS_SISTEMA = (
    r"apag", r"reinici", r"suspend", r"bloque", r"cancel",
    r"\bsube\b", r"\bsubir\b", r"\bsubeme\b",
    r"\bbaja\b", r"\bbajar\b", r"\bbajame\b",
    r"silenci", r"\bmutea", r"minimiz", r"captur",
    r"\bhaz\b", r"\bhazme\b", r"\bpon\b", r"\bponme\b",
    r"\bquita\b", r"\bcierra\b", r"\bmuestra\b", r"\benseñame\b",
    r"\bvolumen\b",
)


AFIRMATIVAS = {"si", "sip", "vale", "confirmo", "confirmado", "adelante",
               "hazlo", "dale", "claro", "afirmativo", "correcto", "eso",
               "ok", "okay", "venga", "exacto", "efectivamente", "porfa"}

NEGATIVAS = {"no", "nop", "nunca", "jamas", "para", "cancela", "cancelalo",
             "cancelar", "anula", "olvidalo", "dejalo", "mejor", "espera"}


def solo_es_respuesta(frase):
    """¿Es la frase solo un "sí" o un "no", sin nada más?

    Sin esto, un "sí" suelto se enrutaba a completar_tarea, y "sí, hazlo"
    llegó a proponer cerrar_programa: un asentimiento perdido podía cerrarte
    una aplicación. Si no hay nada pendiente que confirmar, un monosílabo
    no debe mover nada.
    """
    palabras = re.sub(r"[^\w\s]", " ", _sin_tildes(frase)).split()
    if not palabras or len(palabras) > 3:
        return False
    if palabras[0] not in AFIRMATIVAS and palabras[0] not in NEGATIVAS:
        return False
    # "sí, apaga el ordenador" sí lleva orden dentro: eso no es un monosílabo
    return not any(p in VERBOS_TAREA or re.search(r"^(abre|cierra|apaga|"
                   r"reinicia|sube|baja|pon|quita|busca)", p) for p in palabras[1:])


# Comandos que manda el popup del correo. En una constante porque se
# miran en dos sitios (el bucle normal y el de interrupcion), y tenerlos
# escritos dos veces ya hizo que uno se quedara sin actualizar.
CMDS_BORRADOR = ("borrador_enviar", "borrador_cancelar", "borrador_destinatario")


def pide_dejarlo(frase):
    """¿Quiere abandonar el correo que se está montando a medias?

    Se exige que sea una frase CORTA: "déjalo" abandona, pero "dile que
    lo deje para mañana" es el texto del correo, no una cancelación.
    Contestar un paso con algo largo siempre es contenido.
    """
    b = _sin_tildes(frase or "").strip(" .,!¡?¿")
    if not b or len(b.split()) > 3:
        return False
    return bool(re.search(r"\b(dejalo|dejemoslo|cancela|cancelalo|olvidalo|"
                          r"olvidate|da igual|no importa|nada)\b", b))


def es_afirmacion(frase):
    """¿Ha dicho que sí a lo que se le acaba de preguntar?

    Se mira solo la PRIMERA palabra, que es la que decide: "sí, hazlo",
    "vale, adelante" y "sí por favor" son que sí; "no, déjalo" es que no.
    Ante cualquier otra cosa se entiende que no, porque apagar el
    ordenador equivocándose no tiene arreglo.
    """
    palabras = re.sub(r"[^\w\s]", " ", _sin_tildes(frase)).split()
    if not palabras:
        return False
    if palabras[0] in NEGATIVAS:
        return False
    return palabras[0] in AFIRMATIVAS


def pide_accion_sistema(frase):
    """¿Es una orden para el ordenador, o solo un comentario sobre él?

    Salió de la evaluación: "el PC se calienta mucho" proponía subir el
    volumen, y "va muy lento" reiniciar. Son quejas. Una orden lleva verbo
    de mando, o va envuelta en una petición ("puedes bajar el volumen").
    """
    t = _sin_tildes(frase)
    if es_pregunta(t) and not re.search(r"\b(puedes|podrias|me)\b", t):
        return False
    return any(re.search(v, t) for v in VERBOS_SISTEMA)


def pide_apagar(frase):
    """¿Está pidiendo apagar o reiniciar, o solo lo ha mencionado?

    Apagar es la única acción de la que no se vuelve: si había trabajo sin
    guardar, se pierde. Y Whisper se equivoca —en este proyecto se le ha
    visto oír "qué te harás" donde se dijo "qué tal has"—, así que no basta
    con que el modelo lo proponga: la frase tiene que contener de verdad un
    verbo de apagado en imperativo o en petición.
    """
    t = _sin_tildes(frase)
    if es_pregunta(t) and not re.search(r"\b(puedes|podrias|me)\b", t):
        return False          # "¿se apaga solo?" no es una orden
    # Con "se" delante es algo que LE PASA al ordenador, no una orden. Hacía
    # falta sin depender de los signos: Whisper escribió "Se apaga solo el
    # ordenador." sin interrogación, y el router propuso apagarlo
    if re.search(r"\bse (?:me |te |le |nos |les )?(?:apaga|apago|apagara|reinicia|"
                 r"reinicio|suspende|bloquea|bloqueo)\b", t):   # "se ME reinicia"
        return False
    # Con los enclíticos: "¿puedes apagarME el ordenador?" es una petición
    # legítima, y sin "apagarme" en la lista se ignoraba
    return bool(re.search(
        r"\b(apaga|apagame|apagalo|apagar|apagarme|apagarlo|apague|apagas|"
        r"reinicia|reiniciame|reinicialo|reiniciar|reiniciarme|reiniciarlo|"
        r"reinicie|reinicias|suspende|suspender)\b", t))


def prompt_router():
    """El prompt del router con lo que se sabe del usuario.

    Hace falta sobre todo para los correos: sin saber cómo se llama, el
    modelo los firmaba con "[nombre del usuario]", un hueco sin rellenar
    que se habría enviado tal cual.
    """
    datos = memoria.datos_conocidos()
    if not datos:
        return PROMPT_ROUTER
    return (PROMPT_ROUTER + "\n\nDATOS DEL USUARIO (para firmar correos y "
            "para saber de quién habla):\n" +
            "\n".join(f"- {memoria.como_frase(d)}" for d in datos) +
            "\nNunca dejes huecos como [nombre] en un correo: si no sabes "
            "algo, no lo pongas.")


def enrutar(pregunta):
    """¿Hace falta una herramienta? Devuelve (nombre, argumentos) o (None, None)."""
    r = ollama.chat(
        model=MODELO_LLM,
        messages=[{"role": "system", "content": prompt_router()},
                  {"role": "user", "content": pregunta}],
        tools=HERRAMIENTAS,
        options={"num_ctx": NUM_CTX, "temperature": 0.0},
    )
    llamadas = r.message.tool_calls or []
    if not llamadas:
        return None, None

    nombre = llamadas[0].function.name
    args = dict(llamadas[0].function.arguments)

    # Un "sí" o un "no" suelto no es una orden. Solo significa algo cuando
    # responde a una pregunta, y de eso se encarga el turno anterior.
    if solo_es_respuesta(pregunta):
        print(f"[router] descarto {nombre}: {pregunta!r} es solo un sí o un no")
        return None, None

    # "Abre el administrador de TAREAS" no es apuntar una tarea. La palabra
    # coincide y el router picaba: se apuntó "abrir el administrador de
    # tareas" en la agenda en vez de abrirlo. Si la frase empieza pidiendo
    # abrir algo, es abrir algo.
    if nombre in ("anadir_tarea", "listar_tareas") and re.match(
            r"^\s*(abre|abreme|abrir|abra|lanza|lanzame|ejecuta|arranca|pon)\b",
            _sin_tildes(pregunta)):
        objeto = re.sub(r"^\s*\w+\s*", "", pregunta.strip(), count=1)
        print(f"[router] {nombre} -> abrir_programa: {pregunta!r} pide abrir algo")
        return "abrir_programa", {"programa": objeto or pregunta}

    # Pedir que se borre algo NUNCA puede acabar creando una tarea nueva.
    # Es el fallo más desconcertante de todos: pides quitar el dentista y
    # te quedas con dos apuntes en vez de ninguno.
    if nombre == "anadir_tarea" and pide_borrar(pregunta):
        limpio = sin_verbo_borrar(args.get("texto") or pregunta)
        print(f"[router] anadir_tarea -> completar_tarea: {pregunta!r} pide borrar")
        return "completar_tarea", {"texto": limpio}

    # Red de seguridad: apuntar es lo único que deja rastro permanente en la
    # base de datos, así que es donde más molesta equivocarse.
    if nombre == "anadir_tarea":
        # "Tengo algo esta noche" sin signos: es consultar, no apuntar
        if es_consulta_de_agenda(_sin_tildes(pregunta).strip()):
            print(f"[router] anadir_tarea -> listar_tareas: {pregunta!r} pregunta por la agenda")
            return "listar_tareas", {"cuando": momento_en(pregunta)}
        if not parece_encargo(pregunta):
            print(f"[router] descarto anadir_tarea: {pregunta!r} es una pregunta")
            return None, None
        motivo = tarea_vacia(args.get("texto", ""))
        if motivo:
            print(f"[router] descarto anadir_tarea: {motivo}")
            return None, None

    if nombre == "recordar_dato":
        if not parece_dato_personal(pregunta):
            print(f"[router] descarto recordar_dato: {pregunta!r} pide, no cuenta")
            return None, None
        # se mira lo que dijo el usuario Y lo que el modelo quiere guardar
        motivo = dato_vacio(args.get("dato", "")) or dato_vacio(pregunta)
        if motivo:
            print(f"[router] descarto recordar_dato: {motivo}")
            return None, None

    if nombre == "listar_tareas" and cuando_inventado(args.get("cuando", ""), pregunta):
        print(f"[router] quito cuando={args.get('cuando')!r}: no lo dijo el usuario")
        args["cuando"] = ""

    # Lo contrario del guard de arriba: el modelo a veces se DEJA el momento
    # cuando la pregunta lleva rodeo. "¿Qué planes tengo PARA el fin de
    # semana?" salía sin filtro, y sin filtro se contesta con toda la lista
    # pendiente: el examen del martes incluido. Se recupera de la frase
    # original, que es la única fuente que no depende de que el modelo copie.
    if nombre == "listar_tareas" and not (args.get("cuando") or "").strip():
        rescatado = momento_en(pregunta)
        if rescatado:
            print(f"[router] recupero cuando={rescatado!r}: estaba en la frase")
            args["cuando"] = rescatado

    # Ninguna acción del sistema se dispara con un comentario. "El PC se
    # calienta mucho" llegó a proponer subir el volumen, y "va lento" a
    # reiniciar: son quejas, no órdenes. Tiene que haber verbo de mando.
    if nombre == "control_sistema":
        accion = _sin_tildes(args.get("accion", ""))
        if not pide_accion_sistema(pregunta):
            print(f"[router] descarto {accion!r}: {pregunta!r} es un comentario")
            return None, None
        # Y apagar o reiniciar, además, exigen su verbo concreto
        if ("apagar" in accion or "reiniciar" in accion) and not pide_apagar(pregunta):
            print(f"[router] descarto {accion!r}: {pregunta!r} no lo pide claramente")
            return None, None

    # El tiempo tiene su propia herramienta: datos exactos y en un segundo,
    # en vez de leer páginas y resumirlas. Si el modelo tira de búsqueda
    # igualmente, se corrige aquí
    # Y al revés: con la herramienta del tiempo delante, el modelo la usaba
    # para cualquier frase con "hoy" ("¿qué día es hoy?", "hoy hace buen
    # día"). Solo vale si la frase pregunta de verdad por el tiempo.
    if nombre == "el_tiempo" and not tiempo.es_del_tiempo(pregunta):
        if re.search(r"\b(que dia|que hora|en que fecha|que fecha)\b",
                     _sin_tildes(pregunta)):
            print(f"[router] el_tiempo -> que_hora_es: {pregunta!r} pregunta por el día")
            return "que_hora_es", {}
        print(f"[router] descarto el_tiempo: {pregunta!r} no pregunta por el tiempo")
        return None, None

    # "¿Qué has hecho hoy?" pregunta al asistente, no a la agenda
    if nombre == "listar_tareas" and re.search(
            r"\b(que has hecho|que tal (?:has|ha|te|el)|como (?:ha ido|estas))\b",
            _sin_tildes(pregunta)):
        print(f"[router] descarto listar_tareas: {pregunta!r} es charla")
        return None, None

    if nombre == "buscar_en_web" and tiempo.es_del_tiempo(pregunta):
        print(f"[router] buscar_en_web -> el_tiempo: {pregunta!r} pregunta por el tiempo")
        return "el_tiempo", {"lugar": tiempo.lugar_en(pregunta),
                             "cuando": momento_en(pregunta)}

    if nombre == "buscar_en_web" and not merece_busqueda(pregunta):
        print(f"[router] descarto buscar_en_web: {pregunta!r} no pide nada actual")
        return None, None

    return nombre, args


# Momentos que se buscan en la frase cuando el router no puso ninguno.
# Cada par es (lo que se busca sin tildes, cómo se dice bien): la
# respuesta empieza con esto en voz alta, y "Fin de semana tienes..."
# sonaba a telegrama. De más largo a más corto, porque "próximo fin de
# semana" tiene que ganarle a "fin de semana" o filtraría por la
# semana equivocada.
FRASES_MOMENTO = [
    ("proximo fin de semana",  "el próximo fin de semana"),
    ("fin de semana que viene", "el fin de semana que viene"),
    ("fin de semana siguiente", "el próximo fin de semana"),
    ("proximo finde",          "el próximo fin de semana"),
    ("finde que viene",        "el fin de semana que viene"),
    ("fin de semana",          "el fin de semana"),
    ("finde",                  "el fin de semana"),
    ("pasado manana",          "pasado mañana"),
    ("manana por la manana",   "mañana por la mañana"),
    ("manana por la tarde",    "mañana por la tarde"),
    ("manana por la noche",    "mañana por la noche"),
    ("esta noche",             "esta noche"),
    ("esta tarde",             "esta tarde"),
    ("esta manana",            "esta mañana"),
    ("manana",                 "mañana"),
    ("hoy",                    "hoy"),
]


def destinatario_inventado(destinatario, pregunta):
    """¿El router se ha sacado el destinatario de la manga?

    El prompt del router lleva lo que Jarvis sabe del usuario, para que
    los correos los firme con su nombre de verdad. Efecto secundario:
    a "quiero mandar un correo electrónico", sin mencionar a nadie, le
    puso destinatario "su novia" — sacado de un dato guardado.

    La comprobación es tonta a propósito: si ninguna palabra del
    destinatario aparece en lo que se dijo, no lo dijo.
    """
    propuesto = set(re.findall(r"[a-z0-9]{3,}", _sin_tildes(destinatario or "")))
    if not propuesto:
        return False
    dicho = set(re.findall(r"[a-z0-9]{3,}", _sin_tildes(pregunta or "")))
    return not (propuesto & dicho)


def momento_en(pregunta):
    """Devuelve el momento que aparezca en la frase, o cadena vacía.

    Solo se usa cuando el router ya decidió que se consulta la agenda y
    encima dejó el filtro vacío: no decide nada por su cuenta, solo
    rellena un hueco que el modelo se dejó.
    """
    t = _sin_tildes(pregunta or "")
    for buscar, decir in FRASES_MOMENTO:
        if re.search(r"\b" + re.escape(buscar) + r"\b", t):
            return decir
    return ""


def ejecutar(nombre, args):
    """Ejecuta la herramienta. El resultado ya viene redactado para decirlo."""
    funcion = FUNCIONES.get(nombre)
    if not funcion:
        return None
    try:
        # Los argumentos vienen del modelo: pueden faltar o venir anidados.
        limpios = {k: (v if isinstance(v, str) else str(v))
                   for k, v in (args or {}).items() if v is not None}
        return funcion(**limpios)
    except Exception as e:
        print(f"Error en la herramienta {nombre}: {e}")
