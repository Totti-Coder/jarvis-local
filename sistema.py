"""Control del ordenador por voz: abrir programas, volumen, apagar.

POR QUÉ ESTO ES UNA LISTA BLANCA Y NO UN "EJECUTA LO QUE TE DIGA"

Whisper se equivoca al transcribir. En este mismo proyecto se ha visto
convertir "qué tal has pasado el día" en "qué te harás pasado el día".
Una cadena con esa tasa de error no puede tener permiso para ejecutar
comandos arbitrarios: bastaría una frase mal oída para hacer algo que
no se puede deshacer.

Así que el modelo ELIGE de un catálogo cerrado, nunca COMPONE un comando.
Nada de shell=True, nada de pasar texto del usuario a la línea de órdenes.
Lo que no está en estas listas, no se puede hacer.
"""

import os
import platform
import shutil
import subprocess
import unicodedata

ES_WINDOWS = platform.system() == "Windows"

# Segundos de margen antes de apagar o reiniciar. No es un capricho: da
# tiempo a decir "cancela" si el asistente entendió mal.
MARGEN_APAGADO = 45


def _sin_tildes(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------
# PROGRAMAS
# Cada entrada: nombre hablado -> (cómo se lanza, sinónimos)
# Se lanzan por nombre de ejecutable o por URI del sistema, nunca
# concatenando texto que venga del usuario.
# ---------------------------------------------------------------

PROGRAMAS = {
    "navegador":    (["cmd", "/c", "start", "", "https://www.google.com"],
                     ["chrome", "google chrome", "internet", "el navegador", "edge"]),
    "explorador":   (["explorer"],
                     ["explorador de archivos", "carpetas", "mis archivos"]),
    "calculadora":  (["calc"],
                     ["la calculadora"]),
    "bloc de notas": (["notepad"],
                      ["notas", "notepad", "el bloc"]),
    "spotify":      (["cmd", "/c", "start", "", "spotify:"],
                     ["musica", "la musica"]),
    "correo":       (["cmd", "/c", "start", "", "mailto:"],
                     ["email", "el correo", "gmail"]),
    "terminal":     (["cmd", "/c", "start", "", "cmd"],
                     ["consola", "cmd", "simbolo del sistema"]),
    # "panel de control" NO va aquí: es un programa distinto, más abajo
    "ajustes":      (["cmd", "/c", "start", "", "ms-settings:"],
                     ["configuracion", "opciones", "los ajustes"]),
    "vs code":      (["code"],
                     ["visual studio code", "vscode", "el editor"]),
    "calendario":   (["cmd", "/c", "start", "", "https://calendar.google.com"],
                     ["mi calendario", "google calendar", "la agenda"]),

    # Herramientas de Windows que NO dejan acceso directo en el menú Inicio,
    # así que hay que nombrarlas aquí o no se encuentran.
    # Algunas piden permisos de administrador y lanzarlas directamente da
    # WinError 740. Con "start" es Windows quien las abre, y enseña el aviso
    # de permisos como haría si las abrieras tú desde el menú.
    "administrador de tareas": (["cmd", "/c", "start", "", "taskmgr"],
                                ["gestor de tareas", "los procesos",
                                 "el administrador"]),
    "panel de control":        (["cmd", "/c", "start", "", "control"],
                                ["el panel"]),
    "editor del registro":     (["cmd", "/c", "start", "", "regedit"],
                                ["el registro"]),
    "monitor de recursos":     (["cmd", "/c", "start", "", "resmon"],
                                ["los recursos"]),
    "administrador de discos": (["cmd", "/c", "start", "", "diskmgmt.msc"],
                                ["los discos"]),
    "servicios":               (["cmd", "/c", "start", "", "services.msc"],
                                ["los servicios"]),
    "paint":                   (["mspaint"], ["dibujar"]),
    "recortes":                (["cmd", "/c", "start", "", "ms-screenclip:"],
                                ["recortar", "herramienta de recortes"]),
}


# ---------------------------------------------------------------
# PROGRAMAS INSTALADOS
#
# La lista de arriba cubre lo básico, pero no sabe de tus juegos ni de tus
# programas. En vez de pedirte que los enumeres, se leen los accesos
# directos del menú Inicio: eso ES la lista de lo que tienes instalado.
#
# Sigue siendo una lista blanca. Solo se puede abrir algo que ya existe
# instalado en el equipo, nunca un comando compuesto por el modelo.
# ---------------------------------------------------------------

_instalados = None      # se explora una vez y se guarda

_IGNORAR = ("desinstalar", "uninstall", "readme", "leeme", "manual",
            "ayuda", "help", "documentacion", "web site", "sitio web",
            "configurar", "repair", "reparar")


def programas_instalados(refrescar=False):
    """Diccionario {nombre en minúsculas: ruta del acceso directo}."""
    global _instalados
    if _instalados is not None and not refrescar:
        return _instalados
    _instalados = {}
    if not ES_WINDOWS:
        return _instalados

    from pathlib import Path as _P
    carpetas = [
        _P(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        _P(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    for base in carpetas:
        if not base.is_dir():
            continue
        try:
            for lnk in base.rglob("*.lnk"):
                nombre = _sin_tildes(lnk.stem)
                if any(x in nombre for x in _IGNORAR):
                    continue
                # El primero gana: los de usuario van antes que los del sistema
                _instalados.setdefault(nombre, str(lnk))
        except Exception as e:
            print(f"[sistema] explorando {base}: {e}")

    # Los juegos de Steam van aparte: no dejan acceso directo en el menú
    try:
        for nombre, uri in juegos_steam().items():
            _instalados[nombre] = uri
    except Exception as e:
        print(f"[sistema] buscando juegos de Steam: {e}")
    return _instalados


def juegos_steam():
    """Los juegos de Steam instalados. {nombre: uri para lanzarlo}.

    No aparecen en el menú Inicio, así que hay que leerlos de Steam: dónde
    está se saca del registro, y las bibliotecas de libraryfolders.vdf,
    porque mucha gente los tiene en otro disco (aquí estaban en D:).
    Se lanzan con steam://rungameid, que es la forma oficial.
    """
    if not ES_WINDOWS:
        return {}
    try:
        import winreg
    except ImportError:
        return {}

    raiz = None
    for hive, clave in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, clave) as k:
                for campo in ("SteamPath", "InstallPath"):
                    try:
                        raiz = winreg.QueryValueEx(k, campo)[0]
                        break
                    except FileNotFoundError:
                        pass
            if raiz:
                break
        except (FileNotFoundError, OSError):
            pass
    if not raiz:
        return {}

    import glob as _g
    import re as _re
    bibliotecas = [os.path.join(raiz, "steamapps")]
    vdf = os.path.join(raiz, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        try:
            t = open(vdf, encoding="utf-8", errors="ignore").read()
            bibliotecas += [os.path.join(p.replace("\\\\", "\\"), "steamapps")
                            for p in _re.findall(r'"path"\s+"([^"]+)"', t)]
        except Exception:
            pass

    salida = {}
    for b in bibliotecas:
        for m in _g.glob(os.path.join(b, "appmanifest_*.acf")):
            try:
                t = open(m, encoding="utf-8", errors="ignore").read()
                nom = _re.search(r'"name"\s+"([^"]+)"', t)
                aid = _re.search(r'"appid"\s+"(\d+)"', t)
            except Exception:
                continue
            if not (nom and aid):
                continue
            nombre = nom.group(1)
            # Los redistribuibles no son juegos
            if "redistributable" in nombre.lower() or "proton" in nombre.lower():
                continue
            salida[_sin_tildes(nombre)] = f"steam://rungameid/{aid.group(1)}"
    return salida


# Palabras que no distinguen nada. Contarlas hacía que "administrador DE
# tareas" encontrara "Acerca DE Java", que no tiene nada que ver.
_VACIAS = {"de", "del", "la", "el", "los", "las", "un", "una", "y", "en",
           "para", "con", "mi", "me", "al", "lo"}


def _utiles(texto):
    return {p for p in texto.replace("-", " ").split()
            if p not in _VACIAS and len(p) > 2}


def _buscar_instalado(n):
    """Busca en lo instalado. Devuelve (nombre bonito, ruta) o (None, None)."""
    inst = programas_instalados()
    if not inst:
        return None, None
    if n in inst:
        return n, inst[n]

    palabras = _utiles(n)
    if not palabras:
        return None, None

    # Coincidencia por palabras con contenido: "smite" encuentra "SMITE 2".
    # A igualdad de coincidencias gana el nombre más corto, que suele ser
    # el programa en sí y no un accesorio suyo.
    mejor, puntos = None, 0
    for nombre in inst:
        comunes = len(palabras & _utiles(nombre))
        if comunes and (comunes > puntos or
                        (comunes == puntos and mejor and len(nombre) < len(mejor))):
            mejor, puntos = nombre, comunes
    if mejor:
        return mejor, inst[mejor]

    # Último intento: que lo pedido esté contenido entero en algún nombre
    for nombre in sorted(inst, key=len):
        if n in nombre:
            return nombre, inst[nombre]
    return None, None


def _buscar_programa(nombre):
    """Encuentra el programa: primero en la lista fija, luego en lo instalado.

    Se puntúan TODOS los candidatos y gana el mejor, en vez de quedarse con
    el primero que encaje a medias. Sin esto, "el editor del registro" caía
    en VS Code, porque "el editor" es uno de sus alias.
    """
    n = _sin_tildes(nombre).strip()
    if not n:
        return None, None

    pedidas = _utiles(n)
    mejor, mejor_orden, mejor_punto = None, None, 0
    for clave, (orden, alias) in PROGRAMAS.items():
        for cand in [_sin_tildes(clave)] + [_sin_tildes(a) for a in alias]:
            if cand == n:
                return clave, orden                  # exacto: no hay más que hablar
            # Se puntúa por palabras con contenido en común
            punto = len(pedidas & _utiles(cand))
            # Que el nombre entero del programa aparezca en lo pedido vale más
            if cand in n or n in cand:
                punto += 1
            if punto > mejor_punto:
                mejor, mejor_orden, mejor_punto = clave, orden, punto
    if mejor_punto >= 2:
        return mejor, mejor_orden

    bonito, ruta = _buscar_instalado(n)
    if ruta:
        # Se abre el acceso directo con el propio Windows: así funciona
        # igual con juegos de Steam, apps de la tienda o instaladores raros.
        return bonito, ["cmd", "/c", "start", "", ruta]
    # Si nada instalado encaja, vale la mejor de la lista fija aunque
    # solo coincidiera en una palabra
    if mejor:
        return mejor, mejor_orden
    return None, None


def abrir_programa(nombre):
    """Abre un programa de la lista. Devuelve la frase que dirá Jarvis."""
    clave, orden = _buscar_programa(nombre)
    if not clave:
        return (f"No encuentro {nombre} instalado. "
                f"Si lo tienes con otro nombre, dímelo como aparece.")

    # El ejecutable tiene que existir; si no, se avisa en vez de fallar mudo
    exe = orden[0]
    if exe not in ("cmd", "explorer") and not shutil.which(exe):
        return f"No encuentro {clave} instalado en este ordenador."

    try:
        subprocess.Popen(orden, shell=False,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[sistema] no se pudo abrir {clave}: {e}")
        return f"No he podido abrir {clave}."
    return f"Abriendo {clave}."


# ---------------------------------------------------------------
# CERRAR PROGRAMAS
#
# Se cierran EDUCADAMENTE (taskkill sin /F): la aplicación recibe la orden
# de cerrar y puede preguntar si guardas. Matar el proceso a lo bruto
# perdería el trabajo sin guardar, y eso no se puede deshacer.
# ---------------------------------------------------------------

# Procesos que no se tocan ni aunque el modelo lo pida: cerrarlos deja el
# sistema inutilizable o mata al propio asistente.
INTOCABLES = {
    "system", "system idle process", "registry", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "smss.exe", "svchost.exe",
    "dwm.exe", "fontdrvhost.exe", "ctfmon.exe", "sihost.exe",
    "python.exe", "pythonw.exe", "ollama.exe", "ollama app.exe",
}


# Cómo se llama en español lo que en el sistema tiene otro nombre. Sin esto,
# "cierra la calculadora" no encontraba nada: el proceso es CalculatorApp.exe.
NOMBRES_PROCESO = {
    "calculadora": ["calculatorapp.exe", "calculator.exe"],
    "navegador": ["chrome.exe", "msedge.exe", "firefox.exe"],
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "explorador": ["explorer.exe"],
    "bloc de notas": ["notepad.exe"],
    "notas": ["notepad.exe"],
    "terminal": ["cmd.exe", "windowsterminal.exe", "powershell.exe"],
    "consola": ["cmd.exe", "windowsterminal.exe"],
    "vs code": ["code.exe"],
    "el editor": ["code.exe"],
    "musica": ["spotify.exe"],
    "administrador de tareas": ["taskmgr.exe"],
    "paint": ["mspaint.exe"],
    "smite": ["smite.exe", "smite2.exe", "hirez.exe"],
    "juego": ["smite.exe", "smite2.exe"],
}


def procesos_abiertos():
    """Procesos en marcha. {nombre sin .exe: nombre real}.

    No se filtra por STATUS: las aplicaciones de la Tienda de Windows se
    quedan "Suspended" cuando no tienen el foco, y con el filtro puesto
    desaparecían de la lista aunque estuvieran abiertas.
    """
    if not ES_WINDOWS:
        return {}
    try:
        r = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="ignore", shell=False)
    except Exception as e:
        print(f"[sistema] listando procesos: {e}")
        return {}

    salida = {}
    for linea in r.stdout.splitlines():
        partes = [p.strip('"') for p in linea.split('","')]
        if not partes:
            continue
        exe = partes[0].strip('"').strip()
        if not exe or exe.lower() in INTOCABLES:
            continue
        salida.setdefault(_sin_tildes(exe.rsplit(".", 1)[0]), exe)
    return salida


def cerrar_programa(nombre):
    """Cierra un programa por su nombre. Educadamente, sin forzar."""
    if not ES_WINDOWS:
        return "Cerrar programas solo está hecho para Windows."

    n = _sin_tildes(nombre).strip()
    if not n:
        return "No he entendido qué cerrar."

    abiertos = procesos_abiertos()
    if not abiertos:
        return "No he podido ver qué hay abierto."

    # Primero el mapa de nombres en español: "calculadora" -> CalculatorApp.exe
    por_exe = {v.lower(): (k, v) for k, v in abiertos.items()}
    for clave, exes in NOMBRES_PROCESO.items():
        if clave == n or clave in n or n in clave:
            for exe in exes:
                if exe in por_exe:
                    elegido = clave
                    proceso = por_exe[exe][1]
                    if proceso.lower() in INTOCABLES:
                        return f"No voy a cerrar {proceso}: el sistema lo necesita."
                    return _cerrar(elegido, proceso)

    # Si no, se busca por palabras con contenido, como al abrir
    palabras = _utiles(n)
    exacto = abiertos.get(n)
    if exacto:
        elegido, proceso = n, exacto
    else:
        elegido, proceso, puntos = None, None, 0
        for corto, exe in abiertos.items():
            comunes = len(palabras & _utiles(corto)) if palabras else 0
            if corto in n or n in corto:
                comunes += 1
            if comunes > puntos:
                elegido, proceso, puntos = corto, exe, comunes
        if not proceso:
            return f"No veo {nombre} abierto ahora mismo."

    if proceso.lower() in INTOCABLES:
        return f"No voy a cerrar {proceso}: el sistema lo necesita."
    return _cerrar(elegido, proceso)


def _cerrar(elegido, proceso):
    """Manda cerrar un proceso concreto, sin forzar."""
    try:
        # Sin /F: se pide cerrar, no se mata. Así puede preguntar si guardas.
        r = subprocess.run(["taskkill", "/IM", proceso],
                           capture_output=True, text=True,
                           errors="ignore", shell=False)
    except Exception as e:
        print(f"[sistema] cerrando {proceso}: {e}")
        return f"No he podido cerrar {elegido}."

    if r.returncode != 0:
        # Las apps de la Tienda no siempre atienden a taskkill normal
        print(f"[sistema] taskkill {proceso}: {r.stdout.strip() or r.stderr.strip()}")
        return f"No he podido cerrar {elegido}."
    return f"Cerrando {elegido}."


# ---------------------------------------------------------------
# VOLUMEN
# Se simulan las teclas multimedia: funciona con cualquier tarjeta
# y no hace falta ninguna librería de audio.
# ---------------------------------------------------------------

_TECLAS = {"subir": 0xAF, "bajar": 0xAE, "silenciar": 0xAD}


def volumen(accion, pasos=5):
    """Sube, baja o silencia. `accion`: subir | bajar | silenciar."""
    accion = _sin_tildes(accion)
    for clave in _TECLAS:
        if clave in accion:
            accion = clave
            break
    else:
        return "No he entendido qué hacer con el volumen."

    if not ES_WINDOWS:
        return "El control de volumen solo está hecho para Windows."

    try:
        import ctypes
        tecla = _TECLAS[accion]
        veces = 1 if accion == "silenciar" else pasos
        for _ in range(veces):
            ctypes.windll.user32.keybd_event(tecla, 0, 0, 0)
            ctypes.windll.user32.keybd_event(tecla, 0, 2, 0)
    except Exception as e:
        print(f"[sistema] volumen: {e}")
        return "No he podido cambiar el volumen."

    return {"subir": "Subo el volumen.",
            "bajar": "Bajo el volumen.",
            "silenciar": "Silencio."}[accion]


# ---------------------------------------------------------------
# ENERGÍA
# Apagar y reiniciar van SIEMPRE con margen y siempre se puede cancelar.
# Sin ese margen, una frase mal transcrita te cierra el ordenador con
# trabajo sin guardar.
# ---------------------------------------------------------------

def bloquear():
    """Bloquea la sesión. Es seguro: no se pierde nada."""
    if not ES_WINDOWS:
        return "Bloquear la pantalla solo está hecho para Windows."
    try:
        subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"],
                       shell=False, check=False)
    except Exception as e:
        print(f"[sistema] bloquear: {e}")
        return "No he podido bloquear la pantalla."
    return "Bloqueando."


def apagar(reiniciar=False):
    """Programa el apagado con margen, para que se pueda cancelar."""
    if not ES_WINDOWS:
        return "Apagar solo está hecho para Windows."
    bandera = "/r" if reiniciar else "/s"
    try:
        subprocess.run(["shutdown", bandera, "/t", str(MARGEN_APAGADO)],
                       shell=False, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[sistema] apagado: {e}")
        return "No he podido programar el apagado."

    verbo = "Reinicio" if reiniciar else "Apago"
    return (f"{verbo} el ordenador en {MARGEN_APAGADO} segundos. "
            f"Dime cancela si no quieres.")


def cancelar_apagado():
    """Anula un apagado o reinicio programado."""
    if not ES_WINDOWS:
        return "Solo está hecho para Windows."
    try:
        r = subprocess.run(["shutdown", "/a"], shell=False, check=False,
                           capture_output=True)
    except Exception as e:
        print(f"[sistema] cancelar: {e}")
        return "No he podido cancelarlo."
    if r.returncode != 0:
        return "No había ningún apagado programado."
    return "Cancelado, no se apaga."


# ---------------------------------------------------------------
# ATAJOS DEL USUARIO
#
# Aquí es donde el asistente puede hacer cualquier cosa, sin dejar de ser
# seguro: los comandos los escribe el usuario en atajos.json, tecleándolos
# con calma. La voz solo elige CUÁL lanzar, nunca QUÉ ejecutar.
#
# Con una frase mal transcrita lo peor que pasa es que dispare otro atajo
# suyo. Nunca un comando que no haya escrito él.
# ---------------------------------------------------------------

import json
from pathlib import Path

ATAJOS = Path(__file__).parent / "atajos.json"

# Atajos marcados como peligrosos: hay que pedirlos dos veces seguidas
_pendiente_confirmar = {"nombre": None}


def cargar_atajos():
    """Lee atajos.json. Lista vacía si no existe o está mal."""
    if not ATAJOS.exists():
        return []
    try:
        d = json.loads(ATAJOS.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[sistema] atajos.json no se puede leer: {e}")
        return []

    salida = []
    for a in d.get("atajos", []):
        nombre = (a.get("nombre") or "").strip()
        orden = a.get("comando")
        # El comando tiene que ser una LISTA. Una cadena implicaría shell,
        # y con shell habría inyección: justo lo que se quiere evitar.
        if not nombre or not isinstance(orden, list) or not orden:
            print(f"[sistema] atajo mal definido, lo salto: {nombre or a}")
            continue
        if not all(isinstance(x, str) for x in orden):
            print(f"[sistema] el comando de {nombre!r} debe ser lista de textos")
            continue
        salida.append({
            "nombre": nombre,
            "alias": [str(x) for x in a.get("alias", [])],
            "comando": orden,
            "cwd": a.get("cwd"),
            "dice": a.get("dice") or f"Lanzando {nombre}.",
            "confirma": bool(a.get("confirma")),
        })
    return salida


def nombres_atajos():
    """Para enseñárselos al modelo en la descripción de la herramienta."""
    return [a["nombre"] for a in cargar_atajos()]


def _buscar_atajo(pedido):
    p = _sin_tildes(pedido).strip()
    if not p:
        return None
    for a in cargar_atajos():
        candidatos = [_sin_tildes(a["nombre"])] + [_sin_tildes(x) for x in a["alias"]]
        if any(c == p or c in p or p in c for c in candidatos):
            return a
    return None


def ejecutar_atajo(nombre):
    """Lanza un atajo del usuario. Devuelve la frase que dirá Jarvis."""
    atajo = _buscar_atajo(nombre)
    if not atajo:
        disponibles = nombres_atajos()
        if not disponibles:
            return ("No tienes ningún atajo configurado. Se definen en "
                    "atajos punto json.")
        return f"No conozco ese atajo. Tienes: {', '.join(disponibles[:5])}."

    # Los marcados como peligrosos se piden dos veces
    if atajo["confirma"] and _pendiente_confirmar["nombre"] != atajo["nombre"]:
        _pendiente_confirmar["nombre"] = atajo["nombre"]
        return (f"{atajo['nombre']} puede ser destructivo. "
                f"Vuelve a pedírmelo si estás seguro.")
    _pendiente_confirmar["nombre"] = None

    try:
        subprocess.Popen(atajo["comando"], shell=False, cwd=atajo["cwd"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return f"No encuentro el programa de {atajo['nombre']}."
    except Exception as e:
        print(f"[sistema] atajo {atajo['nombre']!r}: {e}")
        return f"No he podido lanzar {atajo['nombre']}."
    return atajo["dice"]


# ---------------------------------------------------------------
# LO QUE SE LE OFRECE AL MODELO
# ---------------------------------------------------------------

def captura_pantalla():
    """Guarda una captura en el Escritorio."""
    if not ES_WINDOWS:
        return "Las capturas solo están hechas para Windows."
    destino = Path(os.path.expanduser("~/Desktop")) / \
        f"captura_{__import__('datetime').datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        # PIL viene con pyttsx3/onnxruntime en la mayoría de instalaciones
        from PIL import ImageGrab
        ImageGrab.grab().save(destino)
    except ImportError:
        return "Me falta la librería Pillow para hacer capturas."
    except Exception as e:
        print(f"[sistema] captura: {e}")
        return "No he podido hacer la captura."
    return f"Captura guardada en el escritorio."


def suspender():
    """Suspende el equipo. Reversible: no se pierde nada."""
    if not ES_WINDOWS:
        return "Suspender solo está hecho para Windows."
    try:
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                       shell=False, check=False)
    except Exception as e:
        print(f"[sistema] suspender: {e}")
        return "No he podido suspender el equipo."
    return "Suspendiendo."


def minimizar_todo():
    """Enseña el escritorio."""
    if not ES_WINDOWS:
        return "Solo está hecho para Windows."
    try:
        import ctypes
        # Win+D
        ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x44, 0, 2, 0)
        ctypes.windll.user32.keybd_event(0x5B, 0, 2, 0)
    except Exception as e:
        print(f"[sistema] minimizar: {e}")
        return "No he podido minimizar."
    return "Ahí tienes el escritorio."


ACCIONES = {
    "bloquear": bloquear,
    "suspender": suspender,
    "apagar": lambda: apagar(False),
    "reiniciar": lambda: apagar(True),
    "cancelar": cancelar_apagado,
    "subir volumen": lambda: volumen("subir"),
    "bajar volumen": lambda: volumen("bajar"),
    "silenciar": lambda: volumen("silenciar"),
    "captura": captura_pantalla,
    "minimizar": minimizar_todo,
}


def ejecutar_accion(accion):
    """Ejecuta una acción del catálogo. Nada fuera de él es posible."""
    a = _sin_tildes(accion).strip()
    if a in ACCIONES:
        return ACCIONES[a]()
    # Coincidencia laxa, por si el modelo dice "apagar el ordenador"
    for clave, fn in ACCIONES.items():
        if clave in a or a in clave:
            return fn()
    return f"No sé hacer eso. Puedo: {', '.join(ACCIONES)}."


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    print("Programas que sé abrir:")
    for k, (_, alias) in PROGRAMAS.items():
        print(f"  {k:<16} (también: {', '.join(alias[:3])})")
    print("\nAcciones del sistema:")
    for k in ACCIONES:
        print(f"  {k}")
    print(f"\nMargen antes de apagar: {MARGEN_APAGADO} s (cancelable)")
