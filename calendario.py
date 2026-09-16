"""Espejo de las tareas en Google Calendar.

SQLite manda; Calendar es una copia. El orden importa: si Google está caído
o no hay conexión, apuntar la compra tiene que seguir funcionando igual.
Por eso todo aquí falla en silencio y nunca corta el paso.

Sincroniza en UN sentido: de Jarvis a Calendar. Traer de vuelta lo que crees
en Calendar, y resolver qué gana cuando cambias algo en los dos sitios, es
bastante más trabajo y de momento no está.

PUESTA EN MARCHA
  1. Consola de Google -> habilitar Google Calendar API
  2. Pantalla de consentimiento OAuth -> Externo -> añádete como usuario de prueba
  3. Credenciales -> ID de cliente OAuth -> Aplicación de escritorio
  4. Copiar .env.example como .env y pegar el client_id y el client_secret
  5. python calendario.py    (abre el navegador una vez para dar permiso)

Ni .env ni token.json deben subirse a git: están en .gitignore.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

AQUI = Path(__file__).parent
ENV = AQUI / ".env"
CREDENCIALES = AQUI / "credentials.json"      # alternativa al .env
TOKEN = AQUI / "token.json"


def leer_env(ruta=ENV):
    """Lee un .env sencillo. Sin dependencias: son tres variables.

    Se prefiere el .env al credentials.json porque es lo estándar en un
    repositorio: un solo sitio para los secretos, fácil de excluir de git
    y de sustituir por variables de entorno en un servidor.
    """
    valores = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            valores[clave.strip()] = valor.strip().strip('"').strip("'")
    # Las variables del sistema mandan sobre el fichero
    for clave in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_PROJECT_ID"):
        if os.environ.get(clave):
            valores[clave] = os.environ[clave]
    return valores


def config_oauth():
    """Devuelve la configuración de OAuth, del .env o de credentials.json."""
    env = leer_env()
    cid = env.get("GOOGLE_CLIENT_ID", "")
    sec = env.get("GOOGLE_CLIENT_SECRET", "")
    if cid and sec and "tu-id" not in cid:
        return {"installed": {
            "client_id": cid,
            "client_secret": sec,
            "project_id": env.get("GOOGLE_PROJECT_ID", "jarvis"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }}
    # Compatibilidad: si alguien deja el JSON descargado tal cual, también vale
    if CREDENCIALES.exists():
        import json
        try:
            return json.loads(CREDENCIALES.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

# Solo se pide permiso para crear y ver eventos, no para borrar el calendario
PERMISOS = ["https://www.googleapis.com/auth/calendar.events"]

# Etiqueta oculta que se pone a todo evento creado desde aqui, para
# poder encontrarlos luego sin tocar los que has creado tu a mano.
MARCA = "jarvis"

ZONA = "Europe/Madrid"
DURACION_MIN = 60          # cuánto dura un evento si no se dice otra cosa

_servicio = None
_fallo = None


def disponible():
    """¿Se puede usar Calendar ahora mismo? (sin intentar conectarse)"""
    return config_oauth() is not None


def conectar(interactivo=False):
    """Devuelve el servicio de Calendar, o None si no se puede.

    Con interactivo=True abre el navegador para pedir permiso. Eso solo debe
    pasar cuando el usuario ejecuta este fichero a mano: si el servidor de
    voz abriera un navegador a mitad de una conversación sería desconcertante.
    """
    global _servicio, _fallo
    if _servicio:
        return _servicio
    config = config_oauth()
    if not config:
        _fallo = "faltan las credenciales de Google (mira .env.example)"
        return None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        _fallo = "faltan las librerías (pip install google-api-python-client google-auth-oauthlib)"
        return None

    # El token es compartido con el correo, así que aquí NO se puede pedir
    # solo PERMISOS: from_authorized_user_file no comprueba nada, sobrescribe
    # cred.scopes con lo que le pases, y al guardar se perdería el permiso de
    # Gmail. Pasaba de verdad: cada arranque de Jarvis dejaba el fichero con
    # el calendario solo y el correo decía "no tienes permiso" para siempre.
    # Se carga con lo que el fichero YA tiene, y se comprueba aparte que el
    # del calendario esté entre ellos.
    cred = None
    concedidos = []
    if TOKEN.exists():
        try:
            guardado = json.loads(TOKEN.read_text(encoding="utf-8"))
        except Exception:
            guardado = {}
        concedidos = list(guardado.get("scopes") or [])
        if set(PERMISOS).issubset(set(concedidos)):
            try:
                cred = Credentials.from_authorized_user_info(guardado, concedidos)
            except Exception:
                cred = None

    renovado = False
    if cred and not cred.valid and cred.expired and cred.refresh_token:
        try:
            cred.refresh(Request())
            renovado = True
        except Exception as e:
            # Google caduca los permisos a los SIETE DIAS mientras la app
            # este en modo "Testing". No es un fallo tuyo ni del codigo: es
            # la politica de Google para aplicaciones sin verificar. Se
            # vuelve a conceder y listo.
            caducado = "invalid_grant" in str(e) or "expired" in str(e).lower()
            print(f"[calendario] no se pudo renovar: {e}")
            if caducado:
                print(f"[calendario] el permiso ha caducado (Google los caduca a los")
                print(f"[calendario] 7 dias en modo Testing). Ejecuta: python calendario.py")
            cred = None

    if not cred or not cred.valid:
        if not interactivo:
            _fallo = "sin permiso todavía: ejecuta python calendario.py una vez"
            return None
        # Al reautorizar se piden también los permisos que ya hubiera, para
        # no quitarle al correo el suyo por el camino.
        flujo = InstalledAppFlow.from_client_config(
            config, sorted(set(concedidos) | set(PERMISOS)))
        cred = flujo.run_local_server(port=0)
        renovado = True

    if not cred:
        return None

    # Solo se escribe si algo ha cambiado. Reescribirlo en cada arranque no
    # aportaba nada y era justo lo que borraba el permiso de Gmail.
    if renovado:
        TOKEN.write_text(cred.to_json(), encoding="utf-8")
        try:
            os.chmod(TOKEN, 0o600)   # el token es una credencial: que no lo lea cualquiera
        except Exception:
            pass

    try:
        _servicio = build("calendar", "v3", credentials=cred,
                          cache_discovery=False)
        _fallo = None
    except Exception as e:
        _fallo = f"no se pudo abrir el calendario ({e})"
    return _servicio


def crear_evento(texto, cuando_iso, tiene_hora):
    """Sube una tarea a Calendar. Devuelve el id del evento, o None.

    Nunca lanza excepción: si algo falla, la tarea ya está guardada en
    SQLite y eso es lo que de verdad importa.
    """
    servicio = conectar()
    if not servicio:
        return None

    try:
        if not cuando_iso:
            # Sin fecha no hay evento posible: se deja solo en SQLite
            return None

        inicio = datetime.fromisoformat(cuando_iso)
        if tiene_hora:
            cuerpo = {
                "summary": texto,
                "start": {"dateTime": inicio.isoformat(), "timeZone": ZONA},
                "end": {"dateTime": (inicio + timedelta(minutes=DURACION_MIN)).isoformat(),
                        "timeZone": ZONA},
                "reminders": {"useDefault": False, "overrides": [
                    {"method": "popup", "minutes": 10}]},
            }
        else:
            # Sin hora se crea como evento de día completo, no a las 00:00
            cuerpo = {
                "summary": texto,
                "start": {"date": inicio.date().isoformat()},
                "end": {"date": (inicio.date() + timedelta(days=1)).isoformat()},
            }

        # Marca de la casa. Sin esto no hay forma de distinguir un evento
        # que creo Jarvis de uno tuyo, y eso hace imposible limpiar con
        # seguridad: borrar por nombre podria llevarse tu dentista de
        # verdad. Google la guarda oculta, no se ve en la interfaz.
        cuerpo["extendedProperties"] = {"private": {"origen": MARCA}}

        ev = servicio.events().insert(calendarId="primary", body=cuerpo).execute()
        return ev.get("id")
    except Exception as e:
        print(f"[calendar] no se pudo crear el evento: {e}")
        return None


def borrar_evento(id_evento):
    """Quita un evento cuando la tarea se marca como hecha."""
    servicio = conectar()
    if not servicio or not id_evento:
        return False
    try:
        servicio.events().delete(calendarId="primary",
                                 eventId=id_evento).execute()
        return True
    except Exception as e:
        print(f"[calendar] no se pudo borrar el evento: {e}")
        return False


def estado():
    """Frase corta para el arranque del servidor."""
    if not config_oauth():
        return "calendario: desactivado (sin credenciales en .env)"
    if not TOKEN.exists():
        return "calendario: falta dar permiso -> ejecuta python calendario.py"
    if conectar():
        return "calendario: conectado a Google Calendar"
    return f"calendario: no disponible ({_fallo})"


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    if not config_oauth():
        print("Faltan las credenciales de Google.")
        print("Copia .env.example como .env y rellena GOOGLE_CLIENT_ID y")
        print("GOOGLE_CLIENT_SECRET. Se consiguen en console.cloud.google.com:")
        print("Credenciales -> ID de cliente OAuth -> Aplicación de escritorio.")
        sys.exit(1)

    print("Abriendo el navegador para que des permiso...")
    print("(el permiso lo das tú en tu propia cuenta; aquí no se guarda ninguna")
    print(" contraseña, solo un token de acceso en token.json)")
    if not conectar(interactivo=True):
        print(f"\nNo ha funcionado: {_fallo}")
        sys.exit(1)

    print("\nPermiso concedido. Creando un evento de prueba...")
    manana = datetime.now() + timedelta(days=1)
    manana = manana.replace(hour=10, minute=0, second=0, microsecond=0)
    ident = crear_evento("Prueba de Jarvis", manana.isoformat(), True)
    if ident:
        print(f"Evento creado ({ident}). Míralo en tu Google Calendar de mañana.")
        if input("¿Lo borro? [s/N] ").strip().lower() == "s":
            print("Borrado." if borrar_evento(ident) else "No se pudo borrar.")
    else:
        print("El permiso está bien, pero no se pudo crear el evento.")


def listar_eventos(desde, hasta, maximo=250):
    """Los eventos del calendario entre dos fechas. Lista vacia si falla."""
    servicio = conectar()
    if not servicio:
        return []
    try:
        r = servicio.events().list(
            calendarId="primary",
            timeMin=desde.isoformat() + "Z",
            timeMax=hasta.isoformat() + "Z",
            singleEvents=True, orderBy="startTime",
            maxResults=max(1, min(int(maximo), 2500)),
        ).execute()
        return r.get("items", [])
    except Exception as e:
        print(f"[calendar] no se pudieron listar los eventos: {e}")
        return []


def es_de_jarvis(evento):
    """¿Lo creo Jarvis? Solo cuenta la marca, nunca el nombre.

    Emparejar por nombre borraria tu dentista de verdad junto con el de
    las pruebas. Si no hay marca, se deja en paz: los eventos anteriores
    a que existiera la marca hay que repasarlos a mano.
    """
    props = (evento.get("extendedProperties") or {}).get("private") or {}
    return props.get("origen") == MARCA


def _sin_tildes(s):
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


_VACIAS = {"el", "la", "los", "las", "un", "una", "de", "del", "a", "al",
           "mi", "mis", "lo", "y", "o", "para", "con", "que", "en", "por",
           "tarea", "tareas", "evento", "eventos", "cita", "calendario"}


def buscar_por_nombre(texto, desde=None, hasta=None):
    """Eventos del calendario que se parezcan a lo que ha dicho el usuario.

    Hace falta porque Jarvis solo conocia lo que estaba en SQLite. Si
    creas algo a mano en Calendar, o si la base de datos se vacio alguna
    vez, esos eventos eran invisibles: pedir "quita el dentista"
    contestaba "no encuentro esa tarea", que era cierto e inutil.

    Empareja por palabras COMPLETAS y descarta las vacias: sin eso,
    "administrador de tareas" encajaba con "Acerca de Java" porque
    compartian el "de".
    """
    from datetime import datetime, timedelta
    desde = desde or datetime.utcnow() - timedelta(days=90)
    hasta = hasta or datetime.utcnow() + timedelta(days=365)

    busca = {p for p in re_palabras(_sin_tildes(texto))} - _VACIAS
    if not busca:
        return []

    encontrados = []
    for ev in listar_eventos(desde, hasta):
        titulo = {p for p in re_palabras(_sin_tildes(ev.get("summary") or ""))}
        comunes = len(busca & (titulo - _VACIAS))
        if comunes:
            encontrados.append((comunes, ev))

    # Mejor coincidencia primero; a igualdad, el mas proximo en el tiempo
    encontrados.sort(key=lambda x: (-x[0], _inicio_de(x[1])))
    mejor = encontrados[0][0] if encontrados else 0
    return [ev for puntos, ev in encontrados if puntos == mejor]


def re_palabras(t):
    import re
    return re.findall(r"[a-z0-9]{2,}", t)


def _inicio_de(ev):
    i = ev.get("start", {})
    return i.get("dateTime") or i.get("date") or ""


def como_frase(ev):
    """El evento dicho en voz alta: "dentista, el 1 de septiembre a las 9"."""
    from datetime import datetime
    MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    titulo = ev.get("summary") or "(sin título)"
    crudo = _inicio_de(ev)
    if not crudo:
        return titulo
    try:
        d = datetime.fromisoformat(crudo.replace("Z", "+00:00"))
    except ValueError:
        return titulo
    cuando = f"el {d.day} de {MESES[d.month - 1]}"
    if "T" in crudo:
        # La hora la dice memoria, que ya sabe convertir 17:00 en "5 de la
        # tarde". Import perezoso: memoria tambien importa esto.
        try:
            import memoria
            cuando += " " + memoria.hora_hablada(d.hour, d.minute)
        except Exception:
            cuando += f" a las {d.hour}"
    return f"{titulo}, {cuando}"
