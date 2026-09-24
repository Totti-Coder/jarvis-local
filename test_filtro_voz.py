"""Lo que Whisper se inventa en el silencio (ver filtro_voz.py).

Los números son los MEDIDOS en este proyecto con el modelo small, no
inventados: silencio, ruido a tres niveles y un golpe seco contra ocho
órdenes dichas de verdad.

    sin voz      0,76  0,88  0,89  0,90
    con voz      0,02  0,06  0,13  0,17  0,18  0,20  0,21  0,41

Stdlib pura: corre en el CI.

Uso:  python test_filtro_voz.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from filtro_voz import SIN_HABLA_MAX, parece_alucinacion

fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


print("=" * 74)
print("LO QUE WHISPER ESCRIBIÓ SIN QUE NADIE HABLARA")
print("=" * 74)
# (texto, probabilidad de que NO haya voz) tal como salió de verdad
for texto, sin_habla in [
    ("Este es el canal de subtítulos en español de la Iglesia...", 0.88),
    ("¡Suscríbete!", 0.90),
    ("Subtítulos por la comunidad de Amara.org", 0.89),
    ("Subtítulos por la comunidad de Amara.org", 0.76),   # un golpe en la mesa
    ("¡Hasta la próxima!", 0.83),                          # salió en eval --voz
    ("¡Dana!", 0.79),
]:
    tirar, motivo = parece_alucinacion(texto, sin_habla)
    comprueba(f"{texto[:44]!r:<48} sin voz {sin_habla} -> {motivo}", tirar)

print("\n  y aunque el micro capte algo de voz de fondo, por la frase:")
for texto in ["Subtítulos por la comunidad de Amara.org", "¡Suscríbete!",
              "Gracias por ver el vídeo", "Música", "gracias",
              "Este es el canal de subtítulos en español de RTVE"]:
    tirar, motivo = parece_alucinacion(texto, 0.3)      # como si hubiera voz
    comprueba(f"{texto[:44]!r:<48} -> {motivo}", tirar)

print("\n" + "=" * 74)
print("LO QUE SÍ DIJO UNA PERSONA (no se toca)")
print("=" * 74)
# Los valores medidos al decirlas de verdad
for texto, sin_habla in [
    ("Para", 0.41), ("Sigue", 0.18), ("Dale", 0.21),
    ("abre el cronometro.", 0.17), ("que tengo mañana.", 0.06),
    ("apunta a comprar pan.", 0.20), ("Avísame en 10 minutos.", 0.02),
    ("Modo trabajo.", 0.13),
    # "gracias" suelto es alucinación; con algo más, es una persona
    ("Gracias, muy amable", 0.25),
    ("Gracias por avisarme", 0.25),
    ("Ponme música", 0.20),
    ("Apunta que tengo que ver el vídeo de Ana", 0.15),
]:
    tirar, motivo = parece_alucinacion(texto, sin_habla)
    comprueba(f"{texto[:44]!r:<48} sin voz {sin_habla} -> {'TIRADO ' + motivo if tirar else 'pasa'}",
              not tirar)

print("\n" + "=" * 74)
print("CASOS LÍMITE")
print("=" * 74)
comprueba("sin texto, se tira", parece_alucinacion("", 0.1)[0])
comprueba("solo espacios, se tira", parece_alucinacion("   ", 0.1)[0])
comprueba("sin el dato de Whisper, se juzga solo por la frase",
          not parece_alucinacion("Abre el cronómetro")[0]
          and parece_alucinacion("¡Suscríbete!")[0])
comprueba(f"el corte está en {SIN_HABLA_MAX}, con margen a los dos lados",
          parece_alucinacion("Para", SIN_HABLA_MAX + 0.01)[0]
          and not parece_alucinacion("Para", SIN_HABLA_MAX - 0.01)[0])
comprueba("0,41 (lo peor medido con voz) pasa", not parece_alucinacion("Para", 0.41)[0])
comprueba("0,76 (lo mejor medido sin voz) se tira", parece_alucinacion("Para", 0.76)[0])

print("\n" + "=" * 74)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 74)
sys.exit(1 if fallos else 0)
