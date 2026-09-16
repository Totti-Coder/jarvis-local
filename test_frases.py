"""¿Parte bien las frases para la voz, sin cortar horas ni decimales?

Simula la llegada de tokens uno a uno, como hace el streaming de verdad.

Uso:  python test_frases.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")
import redactor, router, voz

fallos = 0

# (texto completo, cómo debe quedar troceado para la voz)
CASOS = [
    ("Tienes que ir al tren a las 20.40 de hoy. Nada más. ",
     ["Tienes que ir al tren a las 20.40 de hoy.", "Nada más."]),

    ("Son las 20.21 y tienes cita. Te queda poco. ",
     ["Son las 20.21 y tienes cita.", "Te queda poco."]),

    ("El pintor fue Velázquez. Es su obra maestra. ",
     ["El pintor fue Velázquez.", "Es su obra maestra."]),

    ("El Sr. García llamó ayer. Dijo que volvería. ",
     ["El Sr. García llamó ayer.", "Dijo que volvería."]),

    ("Cuesta 12.50 euros en total. Es barato. ",
     ["Cuesta 12.50 euros en total.", "Es barato."]),

    ("¿Qué tal estás? Yo bien. ",
     ["¿Qué tal estás?", "Yo bien."]),
]

print("=" * 70)
print("TROCEADO DE FRASES PARA LA VOZ")
print("=" * 70)

for texto, esperado in CASOS:
    # Se alimenta carácter a carácter, como llegan los tokens
    buf = ""
    trozos = []
    for c in texto:
        buf += c
        if voz.frase_terminada(buf):
            trozos.append(buf.strip())
            buf = ""
    if buf.strip():
        trozos.append(buf.strip())

    ok = trozos == esperado
    fallos += not ok
    print(f"\n  {'OK ' if ok else 'MAL'} {texto.strip()[:58]}")
    for t in trozos:
        print(f"      -> {t!r}")
    if not ok:
        print(f"      esperaba: {esperado}")

print("\n" + "=" * 68)
print("DESTINATARIO INVENTADO")
print("=" * 68)
# El prompt del router lleva lo que Jarvis sabe del usuario, para firmar
# los correos con su nombre. Efecto secundario: a "quiero mandar un
# correo electronico" le puso destinatario "su novia", sacado de un dato
# guardado, y contesto "no tengo a su novia en la agenda".
INVENTADOS = [
    ("su novia", "Quiero mandar un correo electrónico.",                True),
    ("Verónica", "Quiero mandar un correo.",                            True),
    ("mi jefe",  "Quiero escribir un correo",                           True),
    # estos SI los dijo: no se tocan
    ("Ana",      "Mándale un correo a Ana diciéndole que llego tarde",  False),
    ("mi jefe",  "Escríbele a mi jefe que mañana trabajo desde casa",   False),
    ("Luis",     "Manda un email a Luis con el informe",                False),
    ("",         "Quiero mandar un correo",                             False),
]
for destinatario, dicho, esperado in INVENTADOS:
    r = router.destinatario_inventado(destinatario, dicho)
    ok = r == esperado
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {destinatario!r:<12} en {dicho[:44]!r}"
          + ("" if ok else f"   esperaba {esperado}"))

print("\n" + "=" * 68)
print("ABANDONAR EL CORREO A MEDIAS")
print("=" * 68)
# Solo con frases cortas: contestar un paso con algo largo es contenido
# del correo, no una cancelacion.
DEJARLO = [
    ("déjalo", True), ("cancela", True), ("nada", True),
    ("da igual", True), ("olvídalo", True),
    ("dile que lo deje para mañana", False),
    ("que llego tarde a la cena", False),
    ("Reunión del jueves", False),
    ("no puedo ir", False),
]
for frase, esperado in DEJARLO:
    r = router.pide_dejarlo(frase)
    ok = r == esperado
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {frase!r:<32} -> {r}"
          + ("" if ok else f"   esperaba {esperado}"))

print("\n" + "=" * 68)
print("NEGATIVAS DEL MODELO AL REDACTAR")
print("=" * 68)
# El modelo se negaba a escribir en 1 de cada 4 encargos, al azar. Meter
# eso en el cuerpo seria mandarle a alguien "no puedo cumplir con esa
# solicitud". Lo dificil no es detectarlo, es NO confundir un correo con
# una negativa: "Lo siento, Ana. Me retrase" empieza igual que una.
RECHAZOS = [
    ("Lo siento, pero no puedo cumplir con esa solicitud.",            True),
    ("No puedo ayudarte con eso.",                                     True),
    ("Lo siento, pero no tengo permiso para escribir correos.",        True),
    ("No estoy autorizado a redactar ese mensaje.",                    True),
    ("Como modelo de lenguaje, no tengo la capacidad de hacerlo.",     True),
    ("I'm sorry, but I can't help with that.",                         True),
    # estos SI son correos, aunque empiecen como una negativa
    ("Lo siento, Ana. Me retrasé y llegué tarde a la cena. Un abrazo", False),
    ("Lo siento mucho. Me duele lo que pasó. Un abrazo, Pablo.",       False),
    ("Hola Luis, siento no poder ayudarte con la mudanza. Un saludo",  False),
    ("Hola Ana,\n\nLlego tarde.\n\nUn abrazo,\nPablo",                 False),
]
for texto, esperado in RECHAZOS:
    r = redactor.parece_rechazo(texto)
    ok = r == esperado
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {r!s:<5} {texto[:52]!r}")

print("\n" + "=" * 68)
print("LIMPIAR LO QUE EL MODELO AÑADE DE SU COSECHA")
print("=" * 68)
LIMPIEZA = [
    ("Aquí tienes el correo:\n\nHola Ana,\n\nVen ya.",  "Hola Ana,\n\nVen ya."),
    ('"Hola Ana, ven a casa."',                         "Hola Ana, ven a casa."),
    ("Asunto: Visita\n\nHola Ana,\n\nVen ya.",          "Hola Ana,\n\nVen ya."),
    ("Hola **Ana**, ven a *casa*.",                     "Hola Ana, ven a casa."),
    # un hueco sin rellenar enviado tal cual queda peor que no firmar
    ("Un abrazo,\n[nombre del usuario]",                "Un abrazo"),
]
for entra, esperado in LIMPIEZA:
    r = redactor.limpiar_correo(entra)
    ok = r == esperado
    fallos += not ok
    print(f"  {'OK ' if ok else 'MAL'} {entra[:44]!r}")
    if not ok:
        print(f"        esperaba {esperado!r}")
        print(f"        obtenido {r!r}")

print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
