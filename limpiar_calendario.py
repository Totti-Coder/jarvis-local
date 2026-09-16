"""Quita del calendario los eventos que dejaron las pruebas.

POR QUE HACE FALTA ESTO

`ver_tareas.py --borrar` vaciaba SQLite y NO tocaba Google Calendar. El
evento_id solo vive en SQLite, asi que al borrar la fila se perdia la
unica forma de encontrar el evento: quedaba huerfano para siempre y
Jarvis ya no podia quitarlo, porque para el esa tarea no existia.

Eso ya esta arreglado —ahora --borrar quita primero los eventos— pero los
huerfanos de antes siguen ahi. Esto los limpia.

COMO DECIDE QUE BORRAR

Te ENSENA lo que ha encontrado y te pide confirmacion. Nunca borra solo.

Los eventos creados desde ahora llevan una marca oculta, asi que se
identifican sin dudar. Los de antes no la tienen: para esos hay que
darle los nombres a mano, y aun asi te los lista antes de tocarlos.

Uso:
    python limpiar_calendario.py                   ve que hay, sin borrar
    python limpiar_calendario.py --pruebas         borra lo que dejaron los tests
    python limpiar_calendario.py --marcados        borra los que creo Jarvis
    python limpiar_calendario.py --nombre dentista borra los que se llamen asi
    python limpiar_calendario.py --dias 120        cuanto hacia atras mirar
"""

import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

import calendario

DIAS_ATRAS = 120

# Lo que dejaron los tests. Durante semanas, test_resumen y test_ventanas
# escribieron en el calendario real (ya no: memoria solo toca Google si el
# servidor se lo engancha). Tienen una huella inconfundible: estos nombres
# Y el reloj congelado de los tests, que fecha todo el 31 de agosto o el 1
# de septiembre de 2026, aunque se crearan otro dia. Exigir las dos cosas
# a la vez evita llevarse un "dentista" tuyo de verdad.
NOMBRES_PRUEBA = {"comprar pan", "dentista", "llamar al banco",
                  "sacar la basura", "ver la peli"}
FECHAS_PRUEBA = {"2026-08-31", "2026-09-01"}


def es_de_las_pruebas(evento):
    titulo = (evento.get("summary") or "").strip().lower()
    inicio = evento.get("start", {})
    fecha = (inicio.get("dateTime") or inicio.get("date") or "")[:10]
    return titulo in NOMBRES_PRUEBA and fecha in FECHAS_PRUEBA


DIAS_ADELANTE = 365


def argumento(bandera, defecto=None):
    if bandera in sys.argv:
        i = sys.argv.index(bandera)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return defecto


def cuando_de(evento):
    inicio = evento.get("start", {})
    return inicio.get("dateTime") or inicio.get("date") or "?"


def main():
    if not calendario.conectar():
        print("No hay acceso al calendario.")
        print("Si el permiso ha caducado: python calendario.py")
        return 1

    dias = int(argumento("--dias", DIAS_ATRAS))
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    eventos = calendario.listar_eventos(ahora - timedelta(days=dias),
                                        ahora + timedelta(days=DIAS_ADELANTE))
    print(f"{len(eventos)} eventos entre hace {dias} días y dentro de un año.\n")

    nombre = argumento("--nombre")
    if "--pruebas" in sys.argv:
        objetivo = [e for e in eventos if es_de_las_pruebas(e)]
        criterio = "dejaron los tests (nombre y fecha de prueba)"
    elif nombre:
        objetivo = [e for e in eventos
                    if nombre.lower() in (e.get("summary") or "").lower()]
        criterio = f"se llaman como {nombre!r}"
    elif "--marcados" in sys.argv:
        objetivo = [e for e in eventos if calendario.es_de_jarvis(e)]
        criterio = "llevan la marca de Jarvis"
    else:
        # Sin bandera: solo informa, agrupando por nombre para que se vea
        # de un vistazo que ha dejado cada tanda de pruebas.
        cuenta = {}
        for e in eventos:
            titulo = e.get("summary") or "(sin título)"
            marca = "·jarvis" if calendario.es_de_jarvis(e) else ""
            cuenta[titulo + marca] = cuenta.get(titulo + marca, 0) + 1
        print("Qué hay, agrupado por nombre:")
        for titulo, n in sorted(cuenta.items(), key=lambda x: -x[1]):
            print(f"  {n:>4}  {titulo}")
        pruebas = sum(es_de_las_pruebas(e) for e in eventos)
        print("\nPara borrar:")
        if pruebas:
            print(f"  python limpiar_calendario.py --pruebas    "
                  f"({pruebas} restos de los tests)")
        print("  python limpiar_calendario.py --marcados")
        print("  python limpiar_calendario.py --nombre dentista")
        return 0

    if not objetivo:
        print(f"No hay ninguno que {criterio}.")
        return 0

    print(f"{len(objetivo)} eventos que {criterio}:\n")
    for e in objetivo[:15]:
        print(f"  {cuando_de(e)[:16]}  {e.get('summary')}")
    if len(objetivo) > 15:
        print(f"  ... y {len(objetivo) - 15} más")

    print(f"\nEsto borra {len(objetivo)} eventos de tu Google Calendar.")
    print("Van a la papelera de Google, así que se pueden recuperar allí.")
    if input("Escribe BORRAR para confirmar: ").strip() != "BORRAR":
        print("Cancelado, no se ha tocado nada.")
        return 0

    hechos = 0
    for e in objetivo:
        if calendario.borrar_evento(e["id"]):
            hechos += 1
    print(f"\nBorrados {hechos} de {len(objetivo)}.")
    if hechos < len(objetivo):
        print("(los que fallaron ya no estaban)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
