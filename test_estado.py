"""El cronómetro y el temporizador sobreviven a un reinicio (ver estado.py).

Un pomodoro que desaparece al cerrar la pestaña no sirve para concentrarse,
que es justo para lo que se pone. Aquí se comprueba que el servidor los
guarda, que al volver cuentan también el rato que estuvo apagado, y que uno
que venció mientras tanto se dice en vez de tragárselo.

Base de datos temporal y reloj a mano. Stdlib pura: corre en el CI.

Uso:  python test_estado.py
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import memoria

memoria.BASE = Path(__file__).parent / "_test_estado.db"
if memoria.BASE.exists():
    os.remove(memoria.BASE)
memoria.preparar()

import cronometro  # noqa: E402
import estado  # noqa: E402

estado.preparar()

HOY = datetime(2026, 10, 20, 12, 0)
fallos = []


def comprueba(titulo, condicion, detalle=""):
    ok = bool(condicion)
    print(f"  {'OK ' if ok else 'MAL'} {titulo}" + (f"\n        {detalle}" if detalle and not ok else ""))
    if not ok:
        fallos.append(titulo)


def crono_de_prueba(reloj):
    return cronometro.Cronometro(reloj=lambda: reloj[0])


print("=" * 72)
print("EL CRONÓMETRO SIGUE DONDE ESTABA")
print("=" * 72)
t = [1000.0]
c = crono_de_prueba(t)
c.aplicar("empezar")
t[0] += 90
estado.guardar_crono(c, HOY)

# El servidor estuvo cinco minutos apagado
vuelta = estado.cargar_crono(HOY + timedelta(minutes=5), reloj=lambda: 0.0)
comprueba("vuelve en marcha", vuelta.corriendo)
comprueba("y cuenta también lo que pasó con el servidor apagado: 90 + 300",
          round(vuelta.transcurrido()) == 390, round(vuelta.transcurrido()))

c.aplicar("pausar")
estado.guardar_crono(c, HOY)
quieto = estado.cargar_crono(HOY + timedelta(hours=3), reloj=lambda: 0.0)
comprueba("uno PARADO no se pone a contar solo",
          not quieto.corriendo and round(quieto.transcurrido()) == 90,
          (quieto.corriendo, quieto.transcurrido()))
comprueba("y si estaba a la vista, sigue a la vista", quieto.visible)

c.aplicar("cerrar")
estado.guardar_crono(c, HOY)
cerrado = estado.cargar_crono(HOY, reloj=lambda: 0.0)
comprueba("cerrado vuelve a cero y oculto",
          not cerrado.visible and cerrado.transcurrido() == 0)

estado.limpiar()
nuevo = estado.cargar_crono(HOY, reloj=lambda: 0.0)
comprueba("sin nada guardado, uno nuevo y vacío",
          not nuevo.visible and not nuevo.corriendo and nuevo.transcurrido() == 0)

print("\n" + "=" * 72)
print("EL TEMPORIZADOR, IGUAL")
print("=" * 72)
comprueba("sin nada guardado, no hay temporizador",
          estado.cargar_temporizador(HOY) is None)

estado.guardar_temporizador(HOY + timedelta(minutes=25), 1500)
fin, segundos, vencido = estado.cargar_temporizador(HOY + timedelta(minutes=1))
comprueba("uno en marcha vuelve con su hora de fin y sin vencer",
          fin == HOY + timedelta(minutes=25) and segundos == 1500 and not vencido,
          (fin, segundos, vencido))

fin, segundos, vencido = estado.cargar_temporizador(HOY + timedelta(minutes=40))
comprueba("si venció con el servidor apagado, se nota", vencido)
f = estado.frase_vencido(segundos, fin, HOY + timedelta(minutes=40))
comprueba(f"y se dice cuándo fue: {f}", "hace 15 minutos" in f, f)
f = estado.frase_vencido(segundos, fin, fin + timedelta(seconds=20))
comprueba(f"si acaba de pasar, se dice así: {f}", "acaba de terminar" in f, f)

estado.guardar_temporizador(None, 0)
comprueba("cancelado, no queda nada", estado.cargar_temporizador(HOY) is None)

print("\n" + "=" * 72)
print("UN ESTADO CORRUPTO NO ROMPE EL ARRANQUE")
print("=" * 72)
with memoria._conectar() as con:
    con.execute("INSERT INTO estado (clave, valor) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (estado.CLAVE_CRONO, "esto no es json"))
    con.execute("INSERT INTO estado (clave, valor) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (estado.CLAVE_TEMPORIZADOR, '{"fin": "ayer por la tarde"}'))
comprueba("un cronómetro ilegible se ignora y se empieza de cero",
          estado.cargar_crono(HOY, reloj=lambda: 0.0).transcurrido() == 0)
comprueba("una fecha ilegible tampoco revienta",
          estado.cargar_temporizador(HOY) is None)

os.remove(memoria.BASE)

print("\n" + "=" * 72)
print(f"  {'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLOS'}")
print("=" * 72)
sys.exit(1 if fallos else 0)
