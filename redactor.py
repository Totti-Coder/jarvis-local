"""Escribir el correo a partir de lo que pide el usuario.

No dictas el correo palabra por palabra: dices QUE quieres decir
("un mensaje formal pidiendole que venga a mi casa") y esto lo redacta.

El modelo se negaba a escribir en 1 de cada 4 encargos, al azar y sin que
hubiera una frase concreta que lo disparara. De ahi el reintento con la
respuesta empezada: con "Hola Ana," ya puesto en su turno, no puede
arrancar con "lo siento, no puedo".
"""

import re

import ollama

import memoria
from ajustes import MODELO_LLM, NUM_CTX


PROMPT_REDACTOR = """Escribes los correos del usuario. Te dan un encargo y
devuelves SOLO el texto del correo, listo para enviar.

REGLAS
- Devuelve únicamente el cuerpo. Nada de "Asunto:", ni comillas, ni
  markdown, ni explicaciones tuyas, ni comentarios sobre lo que has hecho.
- Saludo, cuerpo y despedida. Breve: tres o cuatro frases como mucho.
- El tono lo marca el encargo. Si pide algo formal, trata de usted.
- NO INVENTES NADA que no esté en el encargo: ni fechas, ni horas, ni
  sitios, ni motivos, ni nombres de personas. Si el encargo no dice
  cuándo, el correo no dice cuándo.
- Firma con el nombre del usuario. Si no lo sabes, termina sin firma:
  no te inventes un nombre, y nunca escribas huecos como [nombre].
- Si el encargo ya viene redactado como un mensaje entero, respétalo y
  límitate a darle forma de correo.
- Son correos normales entre conocidos: escríbelos sin más.
- En español."""

# El modelo a veces contesta que no en vez de escribir. Meter eso en el
# cuerpo sería mandarle a alguien "Lo siento, pero no puedo cumplir con
# esa solicitud", así que se detecta y se vuelve a intentar.
# Lo que distingue una negativa de un correo NO es cómo empieza, sino de
# qué habla: una negativa habla de LA PETICIÓN ("no puedo cumplir con esa
# solicitud"), un correo habla con el destinatario. Mirando solo el
# principio se descartaban correos buenos: "Lo siento, Ana. Me retrasé y
# llegué tarde a la cena" empieza igual que una negativa y es el correo.
RECHAZO = re.compile(
    r"(?i)(no puedo (cumplir|ayudarte con (eso|esto)|generar|redactar|crear)|"
    r"no voy a (poder )?(escribir|redactar|generar)|"
    r"no (tengo|dispongo de) (permiso|la capacidad|la posibilidad)|"
    r"no estoy (autorizad|programad|capacitad)|"
    r"esa (solicitud|petici[oó]n)|con esa solicitud|"
    r"como (modelo de lenguaje|asistente de ia|una ia)\b|"
    r"i'?m sorry,? (but )?i (can'?t|cannot)|i cannot (fulfill|comply))")


def parece_rechazo(texto):
    """¿Ha contestado que no, en vez de escribir el correo?

    Además de la frase delatora se exige que sea corto y de una sola
    tirada: un correo de verdad tiene saludo y despedida y ocupa varias
    líneas, así que uno que mencione de pasada "no puedo ayudarte" se
    salva de que lo tomen por una negativa.
    """
    t = (texto or "").strip()
    if not t or len(t) > 220 or "\n" in t:
        return False
    return bool(RECHAZO.search(t))


def nombre_del_usuario():
    """Cómo se llama el usuario, para firmar los correos. "" si no consta.

    Se queda con el PRIMERO que se guardó. Si hay varios es que algo se
    apuntó mal —pasó: contar quién era la novia acabó guardado como el
    nombre del propio usuario— y el más viejo suele ser el bueno.
    """
    for dato in memoria.datos_conocidos():
        m = re.search(r"(?i)(?:me llamo|se llama|soy)\s+"
                      r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,20})", dato)
        if m:
            return m.group(1).capitalize()
    return ""


def saludo_para(destinatario, encargo):
    """El saludo con el que se le arranca la respuesta al modelo.

    Sale del encargo: si pide algo formal, se abre de usted. Los dos son
    válidos sin saber si quien recibe es hombre o mujer, que no consta.
    """
    quien = (destinatario or "").strip()
    if re.search(r"(?i)\bformal|\bseri[oa]|\bde usted|\beducad|\bprofesional",
                 encargo or ""):
        return f"Buenos días{', ' + quien if quien else ''}:\n\n"
    return f"Hola{' ' + quien if quien else ''},\n\n"


def redactar_correo(encargo, destinatario="", asunto=""):
    """Convierte un encargo hablado en el texto de un correo.

    El usuario dice "mándale un mensaje formal pidiéndole que venga a mi
    casa", no el correo palabra por palabra. Dictar un correo entero es
    incómodo y Whisper se come cosas; describir lo que quieres es lo
    natural. Esto lo escribe.

    Lo que salga va SIEMPRE al popup para revisarlo antes de enviarlo,
    así que un borrador flojo se arregla en dos segundos con el teclado.
    Devuelve el encargo tal cual si el modelo falla: mejor eso que nada.
    """
    # Aquí se le pasa SOLO el nombre, no todo lo que Jarvis sabe del
    # usuario. Para redactar no hace falta nada más que la firma, y
    # meterle la vida entera tenía dos efectos malos: colaba datos
    # personales en correos que no venían a cuento, y con ciertos temas
    # el modelo se cerraba en banda y contestaba "no puedo cumplir con
    # esa solicitud" en vez de escribir. Medido: 1 de 3 encargos salía
    # con todos los datos; 3 de 3 pasándole solo el nombre.
    sistema = PROMPT_REDACTOR
    quien = nombre_del_usuario()
    if quien:
        sistema += f"\n\nEl usuario se llama {quien}: firma con ese nombre."
    else:
        sistema += "\n\nNo sabes cómo se llama el usuario: termina sin firma."

    cabecera = (f"Para: {destinatario or 'un conocido'}\n"
                f"Asunto: {asunto or '(sin asunto)'}\n")

    # Dos intentos. El primero le deja elegir el registro: para un encargo
    # formal escribe "Estimada Ana" por su cuenta, y eso se pierde si se
    # le dicta el saludo.
    #
    # El segundo es la red de seguridad, y funciona porque le EMPIEZA la
    # respuesta: con "Hola Ana," ya puesto en su turno, no puede arrancar
    # con "lo siento, no puedo cumplir con esa solicitud". El modelo se
    # negaba en 1 de cada 4 encargos, al azar y sin que hubiera una frase
    # concreta que lo disparara. Medido con el arranque puesto: 6 de 6.
    saludo = saludo_para(destinatario, encargo)
    intentos = [
        (cabecera + f"Encargo: {encargo}", ""),
        (cabecera + f"Contenido que debe transmitir el correo: {encargo}", saludo),
    ]

    for n, (peticion, arranque) in enumerate(intentos, 1):
        mensajes = [{"role": "system", "content": sistema},
                    {"role": "user", "content": peticion}]
        if arranque:
            mensajes.append({"role": "assistant", "content": arranque})
        try:
            r = ollama.chat(
                model=MODELO_LLM, messages=mensajes,
                # Más bajo que en la conversación: aquí no se busca gracia,
                # sino que diga lo encargado y nada más.
                options={"num_ctx": NUM_CTX, "temperature": 0.2},
            )
            # Con arranque, el modelo devuelve solo la continuación
            texto = arranque + (r["message"]["content"] or "").strip()
        except Exception as e:
            print(f"[correo] no se pudo redactar: {e}")
            return encargo

        if parece_rechazo(texto):
            print(f"[correo] intento {n}: se negó ({texto[:50]!r})")
            continue
        limpio = limpiar_correo(texto)
        if limpio:
            return limpio

    # Los dos fallaron: se deja lo dicho tal cual. Queda soso, pero el
    # popup está delante y se arregla escribiendo.
    print("[correo] no hubo manera: se deja el encargo tal cual")
    return encargo


def limpiar_correo(texto):
    """Quita lo que el modelo añade de su cosecha y no es el correo."""
    # A veces contesta con el correo entre comillas, o precedido de
    # "Aquí tienes el correo:". Nada de eso se envía.
    texto = re.sub(r"(?im)^\s*(aqu[íi] tienes|te he escrito|este es el correo)"
                   r"[^\n:]*:\s*", "", texto).strip()
    texto = re.sub(r"(?im)^\s*(asunto|subject)\s*:.*$", "", texto).strip()
    texto = re.sub(r"^[\"“”'`]+|[\"“”'`]+$", "", texto).strip()
    # Markdown: se leería en voz alta y quedaría fatal por escrito
    texto = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", texto)
    # Huecos sin rellenar: "[nombre del usuario]" enviado tal cual es peor
    # que no firmar. Se quitan aunque el prompt diga que no los ponga.
    texto = re.sub(r"\[[^\]]{2,40}\]", "", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip(" ,\n")
