"""Evaluación de las órdenes fijas: ¿se entiende lo que se dice de verdad?

Igual que eval_router.py mide al modelo, esto mide lo que NO pasa por él:
cronómetro, temporizador, horas y rutinas (ver ordenes.py). Cada frase
lleva la orden que debería salir; "modelo" quiere decir que NO es una orden
fija y debe seguir hacia el LLM.

Los fallos que motivaron esto llegaron en capturas: "en 100 el cronómetro"
(Whisper por "enciende") acabó apuntado como tarea. Mejor encontrarlos
antes, y por eso hay dos modos:

    python eval_ordenes.py          el texto tal cual
    python eval_ordenes.py --voz    Piper lo DICE y Whisper lo transcribe:
                                    se evalúa lo que Whisper escribe, con
                                    sus números, sus comas y sus errores

El modo con voz necesita GPU y los modelos: corre en local, no en el CI.
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import ordenes
import rutinas

AQUI = Path(__file__).parent
RUTINAS = rutinas.cargar(AQUI / "atajos.EJEMPLO.json")   # nunca las tuyas
CLIENTES = {"acme": "Acme", "garcia": "García"}

# (frase, esperado, estado). Estado: "panel" (cronómetro a la vista),
# "temp" (temporizador en marcha), "sesion" (contando horas)
CASOS = [
    # ---- cronómetro ----
    ("Abre el cronómetro", "cronometro:abrir", ""),
    ("Enciende el cronómetro", "cronometro:empezar", ""),
    ("Arranca el cronómetro", "cronometro:empezar", ""),
    ("Pon en marcha el cronómetro", "cronometro:empezar", ""),
    ("Inicia el cronómetro", "cronometro:empezar", ""),
    ("Empieza a cronometrar", "cronometro:empezar", ""),
    ("Pon el crono", "cronometro:empezar", ""),
    ("Para el cronómetro", "cronometro:pausar", ""),
    ("Detén el cronómetro", "cronometro:pausar", ""),
    ("Sigue con el cronómetro", "cronometro:reanudar", ""),
    ("Reinicia el cronómetro", "cronometro:reiniciar", ""),
    ("Pon el cronómetro a cero", "cronometro:reiniciar", ""),
    ("¿Cuánto lleva el cronómetro?", "cronometro:consultar", ""),
    ("¿Cuánto tiempo llevamos?", "cronometro:consultar", "panel"),
    ("Cierra el cronómetro", "cronometro:cerrar", ""),
    ("Inicie el cronómetro", "cronometro:empezar", ""),      # de usted
    ("Pare el cronómetro", "cronometro:pausar", ""),
    ("Para", "cronometro:pausar", "panel"),
    ("Sigue", "cronometro:reanudar", "panel"),
    ("Dale", "cronometro:empezar", "panel"),

    # ---- temporizador ----
    ("Pon un temporizador de 10 minutos", "temporizador:poner:600", ""),
    ("Temporizador de cinco minutos", "temporizador:poner:300", ""),
    ("Avísame en 20 minutos", "temporizador:poner:1200", ""),
    ("Avísame dentro de una hora", "temporizador:poner:3600", ""),
    ("¿Me avisas en media hora?", "temporizador:poner:1800", ""),
    ("Avísame a los 25 minutos", "temporizador:poner:1500", ""),
    ("Ponme un pomodoro", "temporizador:poner:1500", ""),
    ("Pon una alarma en 10 minutos", "temporizador:poner:600", ""),
    ("Pon una alarma de 5 minutos", "temporizador:poner:300", ""),
    ("Despiértame en 20 minutos", "temporizador:poner:1200", ""),
    ("Cuenta atrás de tres minutos", "temporizador:poner:180", ""),
    ("Recuérdame en 10 minutos", "temporizador:poner:600", ""),
    ("¿Cuánto queda?", "temporizador:consultar", "temp"),
    ("¿Cuánto le queda a la alarma?", "temporizador:consultar", "temp"),
    ("Cancela el temporizador", "temporizador:cancelar", "temp"),
    ("Quita la alarma", "temporizador:cancelar", "temp"),

    # ---- dos órdenes a la vez ----
    ("Empieza el cronómetro y avísame en 25 minutos",
     "encadenadas:cronometro:empezar+temporizador:poner:1500", ""),
    ("Para el cronómetro y cancela el temporizador",
     "encadenadas:cronometro:pausar+temporizador:cancelar", "temp"),

    # ---- horas ----
    ("Empiezo con Acme", "horas:empezar", ""),
    ("Me pongo a trabajar para García", "horas:empezar", ""),
    ("He terminado", "horas:parar", "sesion"),
    ("Terminé a las siete", "horas:parar_a", "sesion"),
    ("¿Cuántas horas llevo este mes?", "horas:consultar", ""),
    ("¿Cuántas horas llevo con Acme?", "horas:consultar", ""),
    ("Ayer trabajé dos horas para García", "horas:anadir", ""),
    ("Quítale media hora a Acme", "horas:restar", ""),
    ("Borra la última sesión", "horas:borrar_ultima", ""),
    ("Exporta las horas de este mes", "horas:exportar", ""),

    # ---- rutinas ----
    ("Modo trabajo", "rutina:modo trabajo", ""),
    ("Pon el modo trabajo", "rutina:modo trabajo", ""),
    ("Empezamos", "rutina:modo trabajo", ""),
    ("Quita el modo trabajo", "quitar_rutina:modo trabajo", ""),
    ("Sal del modo trabajo", "quitar_rutina:modo trabajo", ""),
    ("Hora de comer", "rutina:hora de comer", ""),

    # ---- NO son órdenes fijas: tienen que llegar al modelo ----
    ("¿Qué hora es?", "modelo", ""),
    ("Apunta comprar pan mañana", "modelo", ""),
    ("Recuérdame llamar a Ana a las cinco", "modelo", ""),
    ("Avísame a las siete", "modelo", ""),
    ("Pon una alarma a las siete", "modelo", ""),
    ("Recuérdame sacar la pizza en 10 minutos", "modelo", ""),
    ("Cuéntame un chiste", "modelo", ""),
    ("¿Qué es un cronómetro?", "modelo", ""),
    ("¿Para qué sirve un temporizador?", "modelo", ""),
    ("Tengo que comprar un cronómetro nuevo", "modelo", ""),
    ("He terminado el informe", "modelo", "sesion"),
    ("Para mañana apunta el dentista", "modelo", "panel"),
    ("¿Cuánto cuesta el pan?", "modelo", "temp"),
    ("¿Qué tengo esta semana?", "modelo", ""),
    ("Abre Spotify", "modelo", ""),
    ("Cierra el navegador", "modelo", ""),
    ("Quita el dentista", "modelo", ""),
    ("Mándale un correo a Ana", "modelo", ""),
]


def etiqueta(det):
    if det is None:
        return "modelo"
    tipo, dato = det
    if tipo == "encadenadas":
        return "encadenadas:" + "+".join(
            f"cronometro:{d}" if t == "cronometro" else
            f"temporizador:{d[0]}" + (f":{int(d[1])}" if d[0].startswith("poner") else "")
            for t, d in dato)
    if tipo == "temporizador":
        return f"temporizador:{dato[0]}" + (f":{int(dato[1])}" if dato[0] == "poner" else "")
    if tipo == "cronometro":
        return f"cronometro:{dato}"
    if tipo == "horas":
        return f"horas:{dato[0]}"
    return f"{tipo}:{dato['nombre']}"


def estado_de(clave):
    return ordenes.Estado(crono_visible="panel" in clave,
                          temporizador_activo="temp" in clave,
                          sesion_abierta="sesion" in clave,
                          clientes=CLIENTES, rutinas=RUTINAS)


def transcriptor():
    """Piper dice la frase y Whisper la transcribe, como en el uso real."""
    import numpy as np
    from piper import PiperVoice

    import ajustes
    import escucha
    voz = PiperVoice.load(str(AQUI / "voces" / f"{ajustes.VOZ_PIPER}.onnx"))

    def oir(frase):
        trozos = list(voz.synthesize(frase))
        audio = np.concatenate([t.audio_float_array for t in trozos])
        origen = trozos[0].sample_rate
        # a 16 kHz, que es lo que espera Whisper (el micro graba así)
        x = np.linspace(0, len(audio), int(len(audio) * 16000 / origen), endpoint=False)
        audio16 = np.interp(x, np.arange(len(audio)), audio).astype(np.float32)
        return escucha.transcribir(audio16, escucha.stt_bueno)
    return oir


if __name__ == "__main__":
    con_voz = "--voz" in sys.argv
    oir = transcriptor() if con_voz else None
    aciertos, fallos = 0, []
    print("=" * 78)
    print(f"ÓRDENES FIJAS ({len(CASOS)} frases{', dichas por Piper y oídas por Whisper' if con_voz else ''})")
    print("=" * 78)
    for frase, esperado, clave in CASOS:
        texto = oir(frase) if con_voz else frase
        obtenido = etiqueta(ordenes.detectar(texto, estado_de(clave)))
        ok = obtenido == esperado
        aciertos += ok
        if not ok:
            fallos.append((frase, texto, esperado, obtenido))
        if not ok or "--todo" in sys.argv:
            oido = f"  (oyó: {texto!r})" if con_voz and texto != frase else ""
            print(f"  {'OK ' if ok else 'MAL'} {frase!r:<46}{oido}")
            if not ok:
                print(f"        esperaba {esperado}  ·  salió {obtenido}")
    pct = 100 * aciertos // len(CASOS)
    print("\n" + "=" * 78)
    print(f"  ACIERTO: {aciertos}/{len(CASOS)} = {pct}%")
    print("=" * 78)
    sys.exit(0 if not fallos else 1)
