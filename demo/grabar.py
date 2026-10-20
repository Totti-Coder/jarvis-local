"""Graba la demo y monta el GIF del README. Sin tocar la pantalla.

POR QUE ASI

El primer intento capturaba una ZONA DE LA PANTALLA. Cuando la ventana de
la demo perdio el primer plano, en los fotogramas aparecio lo que habia
detras: el navegador del usuario, con su sesion y una IP. Un GIF que va a
un repositorio publico no puede capturar nada que no sea la pagina que se
quiere enseniar.

Aqui el navegador va SIN VENTANA (headless) y los fotogramas los sirve el
propio navegador por el protocolo de DevTools. No existe ventana, asi que
no hay nada que se pueda colar: lo unico que se dibuja es la pagina.

Uso:  python grabar.py salida.gif [segundos]      (0 segundos = una prueba)
"""
import asyncio
import base64
import io
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets
from PIL import Image

SALIDA = Path(sys.argv[1])
SEGUNDOS = float(sys.argv[2]) if len(sys.argv) > 2 else 32
FPS = 4
PUERTO = 9223
ANCHO, ALTO = 1280, 800
ANCHO_FINAL = 900
PAGINA = "http://localhost:8016"
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")

# Perfil nuevo cada vez: si no, la pagina recupera la conversacion de la
# grabacion anterior y el GIF empieza con frases de otra toma
shutil.rmtree(SALIDA.parent / "perfil_grabacion", ignore_errors=True)

navegador = subprocess.Popen([
    str(EDGE),
    "--headless=new",                      # sin ventana: nada que capturar
    f"--remote-debugging-port={PUERTO}",
    f"--window-size={ANCHO},{ALTO}",
    f"--user-data-dir={SALIDA.parent / 'perfil_grabacion'}",
    "--no-first-run", "--disable-sync", "--hide-scrollbars",
    "--force-device-scale-factor=1",
    "--enable-unsafe-swiftshader",         # el HUD es Three.js: hace falta WebGL
    # Microfono de pega, para que la pagina no pida el de verdad
    "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    PAGINA,
])


def esperar_pagina():
    """La direccion por la que se le piden los fotogramas."""
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PUERTO}/json/list") as r:
                for t in json.load(r):
                    if t.get("type") == "page" and "8016" in t.get("url", ""):
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    return None


async def grabar(direccion):
    fotogramas = []
    async with websockets.connect(direccion, max_size=None) as ws:
        contador = 0

        async def pedir(metodo, parametros=None):
            nonlocal contador
            contador += 1
            await ws.send(json.dumps({"id": contador, "method": metodo,
                                      "params": parametros or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == contador:
                    return r.get("result", {})

        await pedir("Page.enable")
        # Lienzo fijo: el GIF siempre sale del mismo tamanio
        await pedir("Emulation.setDeviceMetricsOverride",
                    {"width": ANCHO, "height": ALTO, "deviceScaleFactor": 1,
                     "mobile": False})
        await asyncio.sleep(3.5)           # que cargue, conecte y arranque el guion
        t0 = time.monotonic()
        while True:
            r = await pedir("Page.captureScreenshot", {"format": "png"})
            fotogramas.append(Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB"))
            if time.monotonic() - t0 >= SEGUNDOS:
                break
            await asyncio.sleep(1 / FPS)
    return fotogramas


direccion = esperar_pagina()
if not direccion:
    navegador.terminate()
    sys.exit("El navegador no abrio la pagina")
fotogramas = asyncio.run(grabar(direccion))
navegador.terminate()

print(f"{len(fotogramas)} fotogramas de {fotogramas[0].size}")
if SEGUNDOS == 0:
    prueba = SALIDA.with_suffix(".prueba.png")
    fotogramas[0].save(prueba)
    gris = fotogramas[0].convert("L")
    datos = list(gris.getdata())
    print(f"prueba: {prueba}  brillo medio {sum(datos)/len(datos):.1f}"
          f"  max {max(datos)}  colores {len(fotogramas[0].getcolors(999999) or [])}")
    sys.exit(0)

escala = ANCHO_FINAL / fotogramas[0].width
pequenos = [f.resize((ANCHO_FINAL, int(f.height * escala)), Image.LANCZOS)
            for f in fotogramas]
# Una paleta comun para todos: con una por fotograma el GIF pesa el doble
# y los colores parpadean
paleta = pequenos[len(pequenos) // 2].quantize(colors=96, method=Image.MEDIANCUT)
convertidos = [f.quantize(palette=paleta, dither=Image.FLOYDSTEINBERG)
               for f in pequenos]
convertidos[0].save(SALIDA, save_all=True, append_images=convertidos[1:],
                    duration=int(1000 / FPS), loop=0, optimize=True)
print(f"{SALIDA}  {SALIDA.stat().st_size / 1_000_000:.2f} MB")
