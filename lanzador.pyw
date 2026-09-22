"""Arranca Jarvis con un doble clic, sin terminal ni VS Code.

    Doble clic en "Jarvis" (escritorio)      arranca y abre la ventana
    Doble clic en "Apagar Jarvis"            para el servidor

Crear los dos accesos directos (una vez):
    python lanzador.pyw --instalar

POR QUÉ ASÍ Y NO UN .EXE

Un .exe con PyInstaller tendría que llevar dentro las librerías de CUDA
(varios GB), habría que regenerarlo con cada cambio, los antivirus
desconfían de los .exe sin firmar que usan el micrófono, y aun así Ollama
seguiría siendo un programa aparte. Esto es un script pequeño: el código
sigue siendo el mismo, y un cambio se nota con solo volver a abrirlo.

QUÉ HACE

  1. Si Jarvis ya está en marcha, solo abre la ventana: no arranca otro.
  2. Si Ollama no responde, lo arranca.
  3. Arranca servidor.py SIN consola. Lo que imprime va a jarvis.log,
     para poder ver qué pasó si algo falla.
  4. Espera a que cargue (Whisper tarda) y abre Jarvis como aplicación:
     ventana propia, sin barra de direcciones ni pestañas.

La extensión .pyw hace que Windows lo abra con pythonw: sin ventana negra.
"""

import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PUERTO = 8000
URL = f"http://localhost:{PUERTO}"
REGISTRO = AQUI / "jarvis.log"
ESPERA_MAX_S = 180            # Whisper y el modelo en frío pueden tardar

SIN_VENTANA = 0x08000000      # CREATE_NO_WINDOW
GRUPO_NUEVO = 0x00000200      # CREATE_NEW_PROCESS_GROUP: sobrevive al lanzador


# ---------------------------------------------------------------
# COMPROBACIONES
# ---------------------------------------------------------------

def puerto_abierto():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PUERTO)) == 0


def es_jarvis():
    """El puerto puede estar ocupado por OTRA cosa: se mira que sea Jarvis."""
    try:
        with urllib.request.urlopen(URL, timeout=2) as r:
            return b"<title>Jarvis</title>" in r.read(4000)
    except Exception:
        return False


def ollama_responde():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2):
            return True
    except Exception:
        return False


def ejecutable_ollama():
    return shutil.which("ollama") or str(
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")


def python_con_consola():
    """python.exe, no pythonw: el servidor imprime, y con pythonw print()
    puede fallar sin consola. La consola se oculta con CREATE_NO_WINDOW."""
    exe = Path(sys.executable)
    candidato = exe.with_name("python.exe")
    return str(candidato if candidato.exists() else exe)


def navegador_app():
    """Edge o Chrome en modo aplicación: ventana propia, sin pestañas."""
    for ruta in (
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ):
        if ruta.exists():
            return str(ruta)
    return None


def abrir_ventana():
    nav = navegador_app()
    if nav:
        subprocess.Popen([nav, f"--app={URL}", "--window-size=1280,820"])
    else:
        import webbrowser
        webbrowser.open(URL)


# ---------------------------------------------------------------
# ARRANCAR
# ---------------------------------------------------------------

def arrancar(avisar):
    """Hace el trabajo. `avisar(texto, fin=False, error=False)` lo cuenta."""
    if puerto_abierto():
        if es_jarvis():
            avisar("Jarvis ya estaba en marcha", fin=True)
            abrir_ventana()
            return
        avisar(f"El puerto {PUERTO} lo usa otro programa.\nCiérralo y vuelve a probar.",
               error=True)
        return

    if not ollama_responde():
        avisar("Arrancando Ollama")
        try:
            subprocess.Popen([ejecutable_ollama(), "serve"],
                             creationflags=SIN_VENTANA | GRUPO_NUEVO,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            avisar("No encuentro Ollama.\nInstálalo o arráncalo a mano.", error=True)
            return
        for _ in range(40):
            if ollama_responde():
                break
            time.sleep(0.5)
        else:
            avisar("Ollama no ha arrancado.", error=True)
            return

    avisar("Cargando voz y modelos")
    entorno = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    with open(REGISTRO, "w", encoding="utf-8") as log:
        servidor = subprocess.Popen(
            [python_con_consola(), "servidor.py"], cwd=AQUI, env=entorno,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=SIN_VENTANA | GRUPO_NUEVO)

    inicio = time.monotonic()
    while time.monotonic() - inicio < ESPERA_MAX_S:
        if servidor.poll() is not None:
            avisar(f"El servidor se ha cerrado al arrancar.\nMira {REGISTRO.name}",
                   error=True)
            return
        if puerto_abierto():
            avisar("Listo", fin=True)
            abrir_ventana()
            return
        time.sleep(0.5)
    avisar(f"Tarda demasiado en arrancar.\nMira {REGISTRO.name}", error=True)


# ---------------------------------------------------------------
# APAGAR
# ---------------------------------------------------------------

def pid_del_servidor():
    """El proceso que escucha en el puerto, SOLO si es Python: nunca se
    mata otra cosa que por casualidad estuviera en el 8000."""
    r = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                       text=True, errors="ignore", creationflags=SIN_VENTANA)
    for linea in r.stdout.splitlines():
        partes = linea.split()
        if (len(partes) >= 5 and partes[1].endswith(f":{PUERTO}")
                and partes[3].upper() in ("LISTENING", "ESCUCHANDO")):
            pid = partes[4]
            t = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, errors="ignore",
                               creationflags=SIN_VENTANA)
            if "python" in t.stdout.lower():
                return pid
    return None


def apagar(avisar):
    pid = pid_del_servidor()
    if not pid:
        avisar("Jarvis no estaba en marcha", fin=True)
        return
    # /F: el servidor no tiene nada sin guardar (SQLite confirma cada cambio)
    subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True,
                   creationflags=SIN_VENTANA)
    avisar("Jarvis apagado", fin=True)


# ---------------------------------------------------------------
# VENTANITA DE ESTADO
# Sin ella, un doble clic no daría ninguna señal durante los segundos
# que tarda en cargar Whisper, y se volvería a pulsar.
# ---------------------------------------------------------------

def ventana(tarea):
    raiz = tk.Tk()
    raiz.overrideredirect(True)
    raiz.attributes("-topmost", True)
    raiz.configure(bg="#04070b", highlightthickness=1, highlightbackground="#1c4d58")
    ancho, alto = 340, 120
    x = (raiz.winfo_screenwidth() - ancho) // 2
    y = (raiz.winfo_screenheight() - alto) // 2
    raiz.geometry(f"{ancho}x{alto}+{x}+{y}")

    tk.Label(raiz, text="J A R V I S", fg="#56d9e8", bg="#04070b",
             font=("Consolas", 16)).pack(pady=(22, 6))
    estado = tk.Label(raiz, text="", fg="#a8c8d2", bg="#04070b",
                      font=("Consolas", 10), justify="center")
    estado.pack()

    mensajes = queue.Queue()
    puntos = [0]

    def avisar(texto, fin=False, error=False):
        mensajes.put((texto, fin, error))

    def revisar():
        try:
            while True:
                texto, fin, error = mensajes.get_nowait()
                estado.config(text=texto, fg="#e06868" if error else
                              "#56d9e8" if fin else "#a8c8d2")
                estado.base = None if (fin or error) else texto
                if fin:
                    raiz.after(1200, raiz.destroy)
                elif error:
                    # El error se queda hasta que se pulse
                    raiz.bind("<Button-1>", lambda e: raiz.destroy())
                    raiz.after(15000, raiz.destroy)
        except queue.Empty:
            pass
        base = getattr(estado, "base", None)
        if base:
            puntos[0] = (puntos[0] + 1) % 4
            estado.config(text=base + "." * puntos[0])
        raiz.after(350, revisar)

    threading.Thread(target=tarea, args=(avisar,), daemon=True).start()
    revisar()
    raiz.mainloop()


# ---------------------------------------------------------------
# ACCESOS DIRECTOS
# ---------------------------------------------------------------

def instalar():
    """Crea "Jarvis" y "Apagar Jarvis" en el escritorio (el de verdad:
    con OneDrive no siempre es ~/Desktop)."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    icono = AQUI / "static" / "jarvis.ico"
    script = f'''
$s = New-Object -ComObject WScript.Shell
$esc = [Environment]::GetFolderPath("Desktop")
foreach ($p in @(@("Jarvis", ""), @("Apagar Jarvis", "--apagar"))) {{
  $l = $s.CreateShortcut((Join-Path $esc ($p[0] + ".lnk")))
  $l.TargetPath = "{pythonw}"
  $l.Arguments = '"{AQUI / "lanzador.pyw"}" ' + $p[1]
  $l.WorkingDirectory = "{AQUI}"
  $l.IconLocation = "{icono}"
  $l.Description = "Asistente de voz local"
  $l.Save()
  Write-Output (Join-Path $esc ($p[0] + ".lnk"))
}}'''
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, errors="ignore")
    print(r.stdout.strip() or r.stderr.strip())


if __name__ == "__main__":
    if "--instalar" in sys.argv:
        instalar()
    elif "--apagar" in sys.argv:
        ventana(apagar)
    else:
        ventana(arrancar)
