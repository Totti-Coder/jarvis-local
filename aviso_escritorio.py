"""Un aviso en la esquina de la pantalla, sin navegador de por medio.

PARA QUÉ

Los recordatorios y el temporizador tienen que llegar aunque no tengas
ninguna pestaña de Jarvis abierta: si solo avisan con la página delante,
no sirven para acordarse de nada.

Jarvis lo DICE en voz alta por los altavoces, que es lo suyo en un
asistente de voz. Pero si estabas con los cascos puestos, en una llamada o
con la música alta, la voz se pierde y no queda rastro. Esto es el rastro.

POR QUÉ UNA VENTANA Y NO UNA NOTIFICACIÓN DE WINDOWS

Las notificaciones nativas necesitan que la aplicación esté registrada en
el sistema (un AppUserModelID), y sin eso unas veces salen y otras no.
Tkinter viene con Python, se ve siempre, y además se puede pintar con los
colores del resto de Jarvis.

Va en su propio proceso para no mezclar una interfaz gráfica con el
servidor: si algo falla aquí, el asistente sigue.

Uso:
    python aviso_escritorio.py "Te recuerdo: dentista a las 5"
    from aviso_escritorio import mostrar; mostrar("...")
"""

import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SEGUNDOS = 12          # lo que se queda a la vista si no lo tocas
ANCHO, ALTO = 380, 104


def mostrar(texto):
    """Saca el aviso sin bloquear. Devuelve True si se pudo lanzar."""
    if sys.platform != "win32":
        return False
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    try:
        subprocess.Popen(
            [str(pythonw if pythonw.exists() else sys.executable),
             str(AQUI / "aviso_escritorio.py"), texto],
            creationflags=0x08000000)          # sin consola
        return True
    except Exception as e:
        print(f"[aviso] no se pudo mostrar en pantalla: {e}")
        return False


def _ventana(texto):
    import tkinter as tk

    raiz = tk.Tk()
    raiz.overrideredirect(True)            # sin barra de título
    raiz.attributes("-topmost", True)
    raiz.configure(bg="#04070b", highlightthickness=1, highlightbackground="#56d9e8")

    # Abajo a la derecha, por encima de la barra de tareas
    x = raiz.winfo_screenwidth() - ANCHO - 24
    y = raiz.winfo_screenheight() - ALTO - 70
    raiz.geometry(f"{ANCHO}x{ALTO}+{x}+{y}")

    tk.Label(raiz, text="J A R V I S", fg="#56d9e8", bg="#04070b",
             font=("Consolas", 9)).pack(anchor="w", padx=14, pady=(10, 2))
    tk.Label(raiz, text=texto, fg="#d4eef4", bg="#04070b",
             font=("Consolas", 11), wraplength=ANCHO - 28, justify="left").pack(
        anchor="w", padx=14)

    # Se va sola, o al hacer clic: un aviso que hay que cerrar a mano es
    # otra cosa que hacer, justo lo contrario de lo que se busca
    raiz.bind("<Button-1>", lambda e: raiz.destroy())
    raiz.after(int(SEGUNDOS * 1000), raiz.destroy)
    raiz.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _ventana(" ".join(sys.argv[1:]))
