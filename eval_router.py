"""Set de evaluación del router: ¿elige bien la herramienta?

Es la pieza menos determinista del asistente. Un modelo de 8B acierta casi
siempre, pero "casi" no es medible a ojo: hace falta un número.

Uso:
    python eval_router.py            resumen y fallos
    python eval_router.py --todo     muestra también los aciertos
"""

import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
import servidor

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
    # preguntar por UNA tarea concreta, no por una franja
    ("¿A qué hora tengo el test de matemáticas?",    "listar_tareas", "consultar"),
    ("¿Cuándo tengo el dentista?",                   "listar_tareas", "consultar"),
    ("¿A qué hora es la reunión?",                   "listar_tareas", "consultar"),
    ("¿Cuándo tengo lo del banco?",                  "listar_tareas", "consultar"),

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

    # ---- conversación: NADA debe tocar la agenda ----
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
]

ver_todo = "--todo" in sys.argv

aciertos = 0
por_categoria = Counter()
total_categoria = Counter()
fallos = []

print("=" * 74)
print(f"EVALUACIÓN DEL ROUTER   ({len(CASOS)} frases, modelo {servidor.MODELO_LLM})")
print("=" * 74)

for frase, esperada, cat in CASOS:
    try:
        obtenida, args = servidor.enrutar(frase)
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
        if not ok:
            print(f"      {'':<11} esperaba: {esperada or 'conversación'}")

print("\n" + "-" * 74)
print("POR CATEGORÍA")
print("-" * 74)
for cat in ("apuntar", "consultar", "completar", "reloj", "recordar",
            "buscar", "charla"):
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
