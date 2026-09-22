"""Set de evaluación del router: ¿elige bien la herramienta?

Es la pieza menos determinista del asistente. Un modelo de 8B acierta casi
siempre, pero "casi" no es medible a ojo: hace falta un número.

Uso:
    python eval_router.py            resumen y fallos
    python eval_router.py --todo     muestra también los aciertos
    python eval_router.py --voz      las frases DICHAS por Piper y oídas por
                                     Whisper: el router recibe lo que Whisper
                                     escribe, con sus errores, como en el uso real
"""

import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
import ajustes, router

# (frase, herramienta esperada, categoría)
# None = debe contestar conversando, sin tocar ninguna herramienta.
CASOS = [
    # ---- apuntar tareas ----
    ("Recuérdame comprar pan mañana",                "anadir_tarea", "apuntar"),
    ("Apunta que tengo dentista el jueves a las 5",  "anadir_tarea", "apuntar"),
    ("Tengo que llamar a mi madre esta tarde",       "anadir_tarea", "apuntar"),
    ("No se me olvide sacar la basura a las diez",   "anadir_tarea", "apuntar"),
    ("Dentista el jueves a las cinco",               "anadir_tarea", "apuntar"),
    ("Reunión mañana a las diez",                    "anadir_tarea", "apuntar"),
    ("¿Me apuntas llamar al banco mañana?",          "anadir_tarea", "apuntar"),
    ("Añade comprar leche a la lista",               "anadir_tarea", "apuntar"),

    # ---- consultar la agenda ----
    ("¿Qué tengo que hacer hoy?",                    "listar_tareas", "consultar"),
    ("¿Qué tengo mañana?",                           "listar_tareas", "consultar"),
    ("¿Qué tengo mañana por la tarde?",              "listar_tareas", "consultar"),
    ("¿Qué tengo en media hora?",                    "listar_tareas", "consultar"),
    ("¿Tengo algo esta noche?",                      "listar_tareas", "consultar"),
    ("¿Tengo algo que hacer esta noche?",            "listar_tareas", "consultar"),
    ("¿Qué me queda por hacer?",                     "listar_tareas", "consultar"),
    ("Dime mis tareas pendientes",                   "listar_tareas", "consultar"),
    ("¿Qué tengo entre las dos y las cinco?",        "listar_tareas", "consultar"),
    # el fin de semana son tres días, y con rodeo el modelo se dejaba el filtro
    ("¿Qué tengo el fin de semana?",                 "listar_tareas", "consultar"),
    ("¿Qué planes tengo para el fin de semana?",     "listar_tareas", "consultar"),
    ("¿Tengo algo este finde?",                      "listar_tareas", "consultar"),
    # preguntar por UNA tarea concreta, no por una franja
    ("¿A qué hora tengo el test de matemáticas?",    "listar_tareas", "consultar"),
    ("¿Cuándo tengo el dentista?",                   "listar_tareas", "consultar"),
    ("¿A qué hora es la reunión?",                   "listar_tareas", "consultar"),
    ("¿Cuándo tengo lo del banco?",                  "listar_tareas", "consultar"),
    # SIN signos: así las escribe Whisper cuando la entonación no sube
    # (eval_router.py --voz). "Tengo algo esta noche." se apuntaba como tarea
    ("Tengo algo esta noche",                        "listar_tareas", "consultar"),
    ("Hay algo mañana por la tarde",                 "listar_tareas", "consultar"),

    # ---- completar ----
    ("Ya he comprado el pan",                        "completar_tarea", "completar"),
    ("Marca como hecho lo del dentista",             "completar_tarea", "completar"),
    ("Ya está lo de llamar al banco",                "completar_tarea", "completar"),
    # borrar es lo mismo que completar: quitar de la lista
    ("Vale, puedes borrarme ese test de mañana",     "completar_tarea", "completar"),
    ("Bórrame el dentista",                          "completar_tarea", "completar"),
    ("Quita lo del pan",                             "completar_tarea", "completar"),
    ("Cancela la reunión de mañana",                 "completar_tarea", "completar"),
    ("Ya no tengo que llamar al banco",              "completar_tarea", "completar"),
    # por fecha: el dia manda, no el nombre
    ("Quita lo del 1 de septiembre",                 "completar_tarea", "completar"),
    ("Borra todo lo que tenía el 31 de agosto",      "completar_tarea", "completar"),
    ("Elimina las tareas de mañana",                 "completar_tarea", "completar"),
    ("Quita el dentista del 1 de septiembre",        "completar_tarea", "completar"),

    # ---- reloj ----
    ("¿Qué hora es?",                                "que_hora_es", "reloj"),
    ("¿Qué día es hoy?",                             "que_hora_es", "reloj"),
    ("¿En qué fecha estamos?",                       "que_hora_es", "reloj"),

    # ---- datos sobre el usuario: memoria, no agenda ----
    ("Me llamo Toti",                                "recordar_dato", "recordar"),
    ("Soy desarrollador de software",                "recordar_dato", "recordar"),
    ("Vivo en Madrid",                               "recordar_dato", "recordar"),
    ("Mi hermana se llama Ana",                      "recordar_dato", "recordar"),
    ("Recuerda que soy alérgico al marisco",         "recordar_dato", "recordar"),

    # ---- buscar en internet: solo lo que cambia ----
    ("¿Cómo va el Barcelona en la Liga ahora mismo?", "buscar_en_web", "buscar"),
    ("¿Qué tiempo hace en Madrid?",                   "buscar_en_web", "buscar"),
    ("¿Cuánto cuesta el bitcoin?",                    "buscar_en_web", "buscar"),
    ("¿Qué ha pasado hoy en las noticias?",           "buscar_en_web", "buscar"),
    ("¿Quién ganó la Liga en 2026?",                  "buscar_en_web", "buscar"),

    # ---- control del ordenador ----
    ("Abre Spotify",                                 "abrir_programa", "sistema"),
    # "administrador de TAREAS" no es apuntar una tarea
    ("Abre el administrador de tareas",              "abrir_programa", "sistema"),
    ("Abre el panel de control",                     "abrir_programa", "sistema"),
    ("Sí, abre la calculadora",                      "abrir_programa", "sistema"),
    ("Reinicia el ordenador",                        "control_sistema", "sistema"),
    ("Cierra Spotify",                               "cerrar_programa", "sistema"),
    ("Ciérrame el navegador",                        "cerrar_programa", "sistema"),
    ("Quítame el Discord",                           "cerrar_programa", "sistema"),
    ("Cierra la calculadora",                        "cerrar_programa", "sistema"),
    ("Ábreme el navegador",                          "abrir_programa", "sistema"),
    ("Abre la calculadora",                          "abrir_programa", "sistema"),
    ("Sube el volumen",                              "control_sistema", "sistema"),
    ("Bloquea la pantalla",                          "control_sistema", "sistema"),
    ("Apaga el ordenador",                           "control_sistema", "sistema"),
    ("Cancela el apagado",                           "control_sistema", "sistema"),
    # peticiones con pronombre pegado: "apagarME" no estaba en la lista y se
    # ignoraban (siguen pidiendo confirmación antes de apagar)
    ("¿Puedes apagarme el ordenador?",               "control_sistema", "sistema"),
    ("¿Me reinicias el ordenador?",                  "control_sistema", "sistema"),

    # ---- correo ----
    ("Mándale un correo a Ana diciéndole que llego tarde", "enviar_correo", "correo"),
    ("Escríbele a mi jefe que mañana trabajo desde casa",  "enviar_correo", "correo"),
    ("Manda un email a Luis con el informe",               "enviar_correo", "correo"),
    # sin decir a quién ni qué: Jarvis lo va preguntando paso a paso
    ("Quiero mandar un correo electrónico",                "enviar_correo", "correo"),
    ("Escribe un correo",                                  "enviar_correo", "correo"),
    # leer y escribir son la misma palabra ("correo") con verbos opuestos
    ("¿Tengo correos nuevos?",                             "leer_correos", "correo"),
    ("¿Qué me han mandado hoy?",                           "leer_correos", "correo"),
    ("Léeme los correos",                                  "leer_correos", "correo"),
    ("Resúmeme lo que me ha llegado al correo",            "leer_correos", "correo"),
    ("¿Me ha escrito Ana?",                                "leer_correos", "correo"),
    ("¿Quién me ha escrito?",                              "leer_correos", "correo"),
    ("Mira mi bandeja de entrada",                         "leer_correos", "correo"),

    # ---- conversación: NADA debe tocar la agenda ----
    # estas mencionan apagar pero NO son ordenes: apagar es irreversible
    ("El ordenador va muy lento",                    None, "charla"),
    ("¿Se apaga solo el ordenador?",                 None, "charla"),
    ("Se apaga solo el ordenador",                   None, "charla"),   # sin signos
    ("Se me reinicia el portátil cada dos por tres", None, "charla"),
    ("Ayer se me apagó el ordenador",                None, "charla"),
    ("El PC se calienta mucho",                      None, "charla"),
    ("¿Qué tal has pasado el día?",                  None, "charla"),
    ("¿Cómo ha ido la mañana?",                      None, "charla"),
    ("¿Qué has hecho hoy?",                          None, "charla"),
    ("¿Te acuerdas de lo de ayer?",                  None, "charla"),
    ("Mañana es viernes, ¿no?",                      None, "charla"),
    ("Hoy hace buen día",                            None, "charla"),
    ("¿Qué tal estás?",                              None, "charla"),
    ("¿Quién pintó Las Meninas?",                    None, "charla"),
    ("Cuéntame un chiste",                           None, "charla"),
    ("Explícame qué es una API",                     None, "charla"),
    ("¿Cuál es la capital de Francia?",              None, "charla"),
    ("Gracias, muy amable",                          None, "charla"),
    ("Vale, perfecto",                               None, "charla"),
    ("¿Me podrías decir mi nombre?",                 None, "charla"),
    # un "sí" o un "no" suelto no es una orden: solo vale respondiendo
    ("Sí",                                           None, "charla"),
    ("Sí, hazlo",                                    None, "charla"),
    ("No, déjalo",                                   None, "charla"),
]

ver_todo = "--todo" in sys.argv
con_voz = "--voz" in sys.argv
if con_voz:
    from eval_ordenes import transcriptor
    oir = transcriptor()

aciertos = 0
por_categoria = Counter()
total_categoria = Counter()
fallos = []

print("=" * 74)
print(f"EVALUACIÓN DEL ROUTER   ({len(CASOS)} frases, modelo {ajustes.MODELO_LLM}"
      f"{', con voz' if con_voz else ''})")
print("=" * 74)

for frase, esperada, cat in CASOS:
    texto = oir(frase) if con_voz else frase
    try:
        obtenida, args = router.enrutar(texto)
    except Exception as e:
        obtenida, args = f"ERROR:{type(e).__name__}", {}

    ok = obtenida == esperada
    aciertos += ok
    total_categoria[cat] += 1
    por_categoria[cat] += ok

    if not ok:
        fallos.append((frase, esperada, obtenida, cat))
    if ver_todo or not ok:
        marca = "OK " if ok else "MAL"
        print(f"  {marca} [{cat:<9}] {frase[:44]:<46} -> {obtenida or 'conversación'}")
        if con_voz and texto != frase:
            print(f"      {'':<11} oyó: {texto!r}")
        if not ok:
            print(f"      {'':<11} esperaba: {esperada or 'conversación'}")

print("\n" + "-" * 74)
print("POR CATEGORÍA")
print("-" * 74)
for cat in ("apuntar", "consultar", "completar", "reloj", "recordar",
            "buscar", "sistema", "correo", "charla"):
    if not total_categoria[cat]:
        continue
    n, t = por_categoria[cat], total_categoria[cat]
    barra = "#" * round(n / t * 22)
    print(f"  {cat:<11} {n:>2}/{t:<2}  {100*n//t:>3}%  {barra}")

pct = 100 * aciertos // len(CASOS)
print("\n" + "=" * 74)
print(f"  ACIERTO GLOBAL: {aciertos}/{len(CASOS)} = {pct}%")

# El fallo más caro es apuntar algo que no era una tarea: es lo único
# que deja rastro permanente en la base de datos.
falsos_apuntes = [f for f in fallos if f[2] == "anadir_tarea"]
if falsos_apuntes:
    print(f"\n  ⚠  {len(falsos_apuntes)} frases apuntadas por error "
          f"(el fallo que ensucia la agenda):")
    for frase, esp, obt, cat in falsos_apuntes:
        print(f"       {frase!r}")
print("=" * 74)

sys.exit(0 if pct >= 90 else 1)
