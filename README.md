<h1 align="center">🎙️ Jarvis</h1>

<p align="center">
  <strong>Asistente de voz en español que corre entero en tu GPU.</strong><br>
  Habla, entiende, decide y actúa — agenda, Gmail y control del PC — sin una sola clave de API.
</p>

<p align="center">
  <img alt="Latencia" src="https://img.shields.io/badge/voz→respuesta-~0.8s-2ea44f?style=for-the-badge">
  <img alt="APIs de pago" src="https://img.shields.io/badge/API_keys-0-2ea44f?style=for-the-badge">
  <img alt="Offline" src="https://img.shields.io/badge/offline--first-100%25-2ea44f?style=for-the-badge">
  <img alt="Router" src="https://img.shields.io/badge/router_eval-93%2F93-2ea44f?style=for-the-badge">
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-WebSocket-009688?logo=fastapi&logoColor=white">
  <img alt="Whisper" src="https://img.shields.io/badge/faster--whisper-CTranslate2-5A5A5A">
  <img alt="Ollama" src="https://img.shields.io/badge/Ollama-llama3.1--8B-000000?logo=ollama&logoColor=white">
  <img alt="Piper" src="https://img.shields.io/badge/Piper-neural_TTS-7C3AED">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-source_of_truth-003B57?logo=sqlite&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/tests-128_casos-2ea44f">
  <img alt="Herramientas" src="https://img.shields.io/badge/tool_calling-12_funciones-0ea5e9">
</p>

---

## 🎬 Demo

<p align="center">
  <img src="jarvis.png" alt="Interfaz de Jarvis" width="720">
</p>

> [!NOTE]
> **Sustituir por un GIF de 15–20 s**: pulsar espacio → hablar → el anillo
> reacciona al espectro → respuesta hablada → popup del correo.
> Es lo primero que mira quien entra al repositorio.

```
Tú     ▸ "Recuérdame sacar la basura mañana a las ocho y media"
Jarvis ▸ "Apuntado: sacar la basura, mañana a las 8 y media de la mañana."

Tú     ▸ "¿Qué tengo el fin de semana?"
Jarvis ▸ "El fin de semana tienes: cena con Ana el viernes a las 9."

Tú     ▸ "Manda un correo formal a Ana pidiéndole que venga el jueves"
Jarvis ▸ "Te lo he preparado. Míralo y dime si lo mando."   → popup editable
```

---

## ⚡ Arquitectura: 4 etapas independientes, no un modelo end-to-end

Cada pieza se mide, se sustituye y se depura por separado. Latencias medianas
medidas en caliente sobre una RTX 3060 Ti.

| Etapa | Tecnología | Latencia | Por qué esta y no otra |
|:---|:---|:---:|:---|
| **1. Transcripción** | `faster-whisper` · `tiny` + `small` | **0,29 s** | Dos modelos: `tiny` para el texto en vivo cada 900 ms, `small` con `beam_size=5` para la pasada buena. Un solo modelo obliga a elegir entre fluidez y precisión |
| **2. Enrutado** | `llama3.1:8b` vía Ollama · *tool calling* | **0,47 s** | Modelo **sin** razonamiento explícito: Qwen3 volcaba 3.256 caracteres de *chain-of-thought* en `content` y tardaba 32,5 s al primer token |
| **3. Generación** | `llama3.1:8b` · **prompt distinto, sin herramientas** | **0,25 s** | Streaming por frases: empieza a hablar en cuanto cierra la primera, no espera al párrafo |
| **4. Voz** | `Piper` · `es_ES-sharvard-medium` | ×19 RT | Voz neuronal local. `pyttsx3` solo sonaba en 1 de cada 5 frases; SAPI funcionaba pero suena a robot de hace veinte años |
| **Estado** | `SQLite` + espejo en Google Calendar | — | Lo que debe ser exacto no lo guarda un modelo. Calendar es una copia *best-effort*: si Google cae, apuntar la compra sigue funcionando |

```mermaid
flowchart LR
    MIC["🎤 Micrófono<br/>VAD Silero"] --> STT["Whisper<br/>tiny · small"]
    STT --> ROUTER{"Router<br/>🔧 CON herramientas"}
    ROUTER -->|"agenda · reloj · memoria"| HERR["Herramientas"]
    ROUTER -->|"abrir · cerrar · apagar"| SIS["Sistema<br/>lista blanca"]
    ROUTER -->|"correo"| MAIL["Gmail"]
    ROUTER -->|"algo actual"| WEB["Búsqueda web"]
    ROUTER -->|"es charla"| CHARLA["Conversador<br/>🚫 SIN herramientas"]
    WEB --> CHARLA
    MAIL --> CHARLA
    HERR <--> DB[("SQLite")]
    HERR --> TTS["🔊 Piper"]
    CHARLA -->|"streaming por frases"| TTS

    style ROUTER fill:#78350f,color:#fff
    style CHARLA fill:#14532d,color:#fff
    style DB fill:#003B57,color:#fff
```

---

## 🧠 Engineering Highlights

Los cinco problemas que costaron de verdad. Cada decisión viene de una
medición, no de una intuición.

- **🔀 Dual-Prompt Router** — Enchufar las herramientas al asistente principal
  **rompe la conversación**: contestaba *"no tengo una función específica para
  contar chistes"*. Tres intentos de arreglarlo por prompt fracasaron. Se
  resolvió por arquitectura: **dos llamadas al LLM con personalidades
  distintas**, y el conversador no ve una herramienta jamás. Resultado: **93/93
  frases enrutadas** y conversación intacta.

- **🛡️ Deterministic Guardrails — *el modelo propone, el código dispone*** —
  ~10 filtros deterministas entre lo que propone el LLM y lo que se ejecuta.
  Cazan cosas reales: *"el PC se calienta mucho"* proponía subir el volumen;
  *"va muy lento"*, reiniciar; un *"sí"* suelto llegó a cerrar un programa. El
  modelo elige **qué función llamar**; la fecha, la hora y el texto los calcula
  código determinista y se guardan literales.

- **💉 Anti-Prompt-Injection en el correo** — Un correo entrante lo escribe
  cualquiera, y Jarvis tiene herramientas para mandar correos y apagar el PC.
  La defensa no es pedirle al modelo que no obedezca: **el contenido de un
  correo nunca llega a la llamada que lleva las herramientas conectadas**. Va
  solo al conversador, que no tiene ninguna. Aunque el modelo se creyera la
  orden, no hay con qué ejecutarla.

- **⚙️ El bug que costaba 14×** — Las dos llamadas usaban `num_ctx` distinto.
  Ollama **recargaba el modelo entero en cada turno**: 17,3 s por pregunta
  contra 1,2 s. Un solo parámetro.

- **📊 Espectro FFT, no volumen** — El anillo reacciona a 32 bandas
  logarítmicas por FFT, 20 veces por segundo. En escala lineal era inservible
  (ruido de fondo 0,62 vs voz alta 0,65); en dB, 0,00 vs 0,29.

<details>
<summary><b>➕ Más decisiones documentadas</b> — intérprete de fechas propio, VRAM, streaming, CUDA</summary>

<br>

**Por qué un intérprete de fechas propio y no `dateparser`.** "Mañana a las
8.40" es ambiguo: ¿mañana el día, o mañana la franja horaria? ¿8:40 o 20:40?
`dateparser` acierta el formato, no la intención. El intérprete propio resuelve
con el reloj: si son las 20:15 y dices "a las 8.40", quieres decir dentro de 25
minutos, no dentro de 12 horas. **82 casos de test** solo sobre esto.

**Por qué el resultado de una herramienta no pasa por el modelo.** Lo que
devuelve SQLite ya viene redactado y se lee tal cual. Si el modelo lo
reformulara, una hora podría cambiar por el camino. Es la diferencia entre un
asistente y una anécdota.

**`cublas64_12.dll is not found`.** El modelo se construía en CUDA y reventaba
al transcribir. `pip` instala cuBLAS y cuDNN en `site-packages/nvidia/*/bin`,
pero CTranslate2 los carga con `LoadLibrary`, que solo mira el `PATH` del
proceso. Se registran antes de importar `faster_whisper`; `add_dll_directory`
por sí solo no basta.

**`cargar_whisper()` mentía.** El `try/except` solo cubría la construcción del
modelo, así que decía "GPU (cuda)" y fallaba después. Ahora hace una
transcripción de prueba real antes de afirmar nada.

**El troceador de frases partía las horas.** "A las 20.40" se dividía en dos
frases y Piper leía "a las veinte" y luego "cuarenta". El detector de fin de
frase exige espacio detrás y rechaza dígito-antes-de-punto y abreviaturas.

**La VRAM va justa.** Whisper y el LLM comparten 8 GB. `num_ctx=2048` se
quedaba corto y la conversación se olvidaba enseguida; 4096 es el techo sin
mover Whisper a CPU.

</details>

---

## 🚀 Qué sabe hacer

<table>
<tr>
<td width="33%" valign="top">

**📅 Agenda**
- Apunta tareas hablando
- Franjas: *"mañana por la tarde"*
- Tramos: *"entre las 2 y las 5"*
- *"el fin de semana"* = vie+sáb+dom
- Espejo en Google Calendar
- Te resume el día al entrar

</td>
<td width="33%" valign="top">

**✉️ Gmail**
- Te resume lo que ha llegado
- *"¿Me ha escrito Ana?"*
- **Tú dices el encargo, él redacta**
- Popup editable antes de enviar
- Permisos mínimos: no puede borrar
- Direcciones tecleadas, no dictadas

</td>
<td width="33%" valign="top">

**💻 Control del PC**
- Abre y cierra programas
- Detecta tus juegos de Steam
- Apagar/reiniciar **con confirmación**
- Volumen, bloqueo, capturas
- Tus propios atajos (`atajos.json`)
- Lista blanca, nunca comandos libres

</td>
</tr>
</table>

**Y además:** 🌐 busca en internet solo cuando la pregunta lo pide · 🧠 recuerda
tu nombre y tus datos entre sesiones · 🤫 corta la grabación sola al dejar de
hablar (VAD) · ✋ interrumpible a media frase · ⚡ streaming por frases.

<details>
<summary><b>🔧 Las 12 herramientas</b></summary>

<br>

| Agenda | Correo | Ordenador | Otras |
|:---|:---|:---|:---|
| `anadir_tarea` | `enviar_correo` | `abrir_programa` | `que_hora_es` |
| `listar_tareas` | `leer_correos` | `cerrar_programa` | `buscar_en_web` |
| `completar_tarea` | | `control_sistema` | `recordar_dato` |
| | | `ejecutar_atajo` | |

</details>

---

## 📦 Quickstart

**Requisitos:** Python 3.13 · [Ollama](https://ollama.com/download) · GPU NVIDIA
con 8 GB (funciona en CPU, más lento).

```bash
# 1. Modelo de lenguaje y dependencias
ollama pull llama3.1:8b
pip install -r requirements.txt

# 2. Voz neuronal (137 MB, una sola vez)
python -m piper.download_voices es_ES-sharvard-medium --data-dir voces

# 3. Arrancar
python servidor.py          # → http://localhost:8000
```

Los modelos de Whisper se descargan solos la primera vez. **Ya funciona**:
agenda, conversación, búsqueda web y control del PC, sin registrarte en ningún
sitio.

> `python servidor.py --red` lo abre a tu wifi para verlo desde el móvil.
> Avisa de lo que implica: no hay autenticación.

<details>
<summary><b>🔑 Google Calendar y Gmail (opcional)</b> — lo único que pide credenciales</summary>

<br>

Son **gratis** y **opcionales**: sin esto todo lo demás funciona igual. Ambos
comparten las mismas credenciales OAuth.

#### 1. Proyecto y APIs

En [Google Cloud Console](https://console.cloud.google.com) crea un proyecto y
habilita **cada API por separado** — tener credenciales no basta:

- [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
- [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com)

#### 2. Pantalla de consentimiento

**Externo** → añádete a ti mismo como **usuario de prueba**. Sin esto Google
devuelve `403 access_denied`.

#### 3. Credenciales

**Crear credenciales** → **ID de cliente de OAuth** → tipo **Aplicación de
escritorio**. Copia el ID y el secreto:

```bash
copy .env.example .env      # Windows   ·   cp en Linux/Mac
```

```ini
GOOGLE_CLIENT_ID=tu-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=tu-secreto
GOOGLE_PROJECT_ID=tu-proyecto
```

#### 4. Agenda de contactos

Las direcciones se **teclean**, nunca se dictan (ver *Anti-Prompt-Injection*):

```bash
copy contactos.EJEMPLO.json contactos.json
```

```json
{"nombre": "Ana", "email": "ana@ejemplo.com", "alias": ["mi hermana"]}
```

#### 5. Conceder permisos (una vez)

```bash
python calendario.py     # abre el navegador
python correo.py         # añade los permisos de Gmail al mismo token
python comprobar_google.py   # valida el .env sin mostrar los secretos
```

Verás *"Google no ha verificado esta aplicación"*: es normal en modo Testing.
**Configuración avanzada** → **Ir a (no seguro)**.

> [!TIP]
> `MODO_BORRADOR = True` en [`correo.py`](correo.py) hace que **nunca envíe**:
> deja todo en borradores de Gmail para que lo revises.

</details>

<details>
<summary><b>⚙️ Atajos propios (opcional)</b></summary>

<br>

```bash
copy atajos.EJEMPLO.json atajos.json
```

```json
{
  "nombre": "copia de seguridad",
  "alias": ["haz una copia", "backup"],
  "comando": ["robocopy", "C:\\Users\\TU_USUARIO\\Documents", "D:\\Backup"],
  "dice": "Haciendo la copia de seguridad."
}
```

El comando es una **lista de argumentos**, no una cadena: se ejecuta sin shell,
así que no hay inyección posible.

</details>

---

## 🧪 Testing

**128 casos** sobre la lógica que puede romperse en silencio —intérprete de
fechas, troceado para la voz, parseo MIME del correo, síntesis— más una **eval
suite del router con 93 frases reales al 100 %**, que es la pieza menos
determinista del sistema y la única forma de saber si una mejora lo es.

```bash
python eval_router.py     # 93 frases · acierto por categoría · % global
python test_memoria.py    # fechas y horas habladas          (37)
python test_ventanas.py   # franjas, tramos, fines de semana (25)
python test_horas.py      # ambigüedad de "a las 8.40"       (20)
python test_correo.py     # MIME, firmas, citas, fechas      (19)
```

<details>
<summary><b>Cómo están hechos</b></summary>

<br>

**Los tests de fecha fijan el "ahora"** en un lunes concreto en vez de usar el
reloj del sistema, así que comprueban de verdad qué pasa al día siguiente sin
esperar veinticuatro horas:

```python
AHORA = datetime(2026, 8, 31, 20, 15)      # lunes por la noche
caso(AHORA, "a las 8.40", "31/08 20:40")   # hoy por la noche, no mañana
```

**`test_voz.py` no puede oír**, así que comprueba lo medible: que suena varias
veces seguidas, que dura lo que debe, que se corta al interrumpir, y —leyendo
los fonemas que genera Piper— que los números se pronuncian como palabras
(`"5"` → `θˈinko`).

**Ninguno hace ruido.** Los tests que hablan sustituyen los altavoces por uno
mudo que consume el audio en tiempo real: las medidas siguen valiendo y no se
oye nada. Con `--sonido` suenan de verdad.

**Cada bug encontrado se convierte en un caso permanente.** El set del router
creció de 42 a 93 frases así: cada vez que enrutaba mal algo real, esa frase
entró en la evaluación.

</details>

---

## 🔒 Seguridad y privacidad

Jarvis acaba teniendo acceso a tu calendario, tu correo y tu ordenador.

| | |
|:---|:---|
| 🏠 **Solo tu equipo** | `127.0.0.1` por defecto. `--red` avisa de lo que implica |
| 🔑 **Permisos mínimos** | Gmail: leer y redactar. **No puede borrar.** Calendar: solo eventos |
| 📇 **Direcciones tecleadas** | Whisper oyó `"Pablojeroza2000arrobajemail.com"`. Un correo mal enviado no se recupera |
| 👀 **Nada irreversible sin verlo** | Apagar, reiniciar y enviar se confirman antes |
| 🧱 **Lista blanca** | Nunca ejecuta un comando salido de tu voz |
| 💉 **Barrera de inyección** | Web y correo van al modelo **sin herramientas** |
| 🙈 **Secretos fuera de git** | `.env`, `token.json`, `contactos.json`, `atajos.json` y la BD |

**Lo que no hay:** el servidor no tiene autenticación. Con `--red`, cualquiera
en esa wifi puede usarlo.

---

## 📁 Estructura

```
servidor.py     FastAPI · WebSocket · router · 12 herramientas · voz
memoria.py      SQLite + intérprete de fechas en español
sistema.py      Control del PC: lista blanca, Steam, atajos
correo.py       Gmail: leer, resumir, redactar, enviar
buscar.py       Búsqueda web y lectura de páginas
calendario.py   Espejo en Google Calendar
index.html      Interfaz + popup del correo (Three.js)
```

<details>
<summary><b>Árbol completo</b></summary>

<br>

```
├── servidor.py             # FastAPI, WebSocket, router, herramientas, voz
├── memoria.py              # SQLite + intérprete de fechas en español
├── sistema.py              # Control del PC: lista blanca y atajos
├── correo.py               # Gmail: leer la bandeja, redactar y enviar
├── buscar.py               # Búsqueda web y lectura de páginas
├── calendario.py           # Espejo en Google Calendar (opcional)
├── index.html              # Interfaz + popup del correo
├── static/nucleo.js        # Núcleo 3D con Three.js (servido en local)
├── ver_tareas.py           # Utilidad de consola para la agenda
├── comprobar_google.py     # Valida el .env sin mostrar los secretos
│
│   # PLANTILLAS: se suben porque no llevan datos reales
├── .env.example
├── contactos.EJEMPLO.json
├── atajos.EJEMPLO.json
│
│   # TESTS
├── eval_router.py          # Acierto del router             (93 frases)
├── test_memoria.py         # Fechas y horas habladas        (37 casos)
├── test_ventanas.py        # Franjas, tramos y findes       (25 casos)
├── test_horas.py           # Ambigüedad de "a las 8.40"     (20 casos)
├── test_correo.py          # MIME, firmas, citas, fechas    (19 casos)
├── test_datos.py           # Datos personales del usuario   (10 casos)
├── test_frases.py          # Troceado, negativas, limpieza  (10 casos)
├── test_voz.py             # Piper: suena, corta, pronuncia  (7 casos)
├── test_resumen.py         # Persistencia entre días         (4 casos)
├── test_cadena.py          # Las cuatro etapas de una vez
│
│   # TUYO: nada de esto se sube (.gitignore)
├── voces/  ·  .env  ·  token.json
└── contactos.json  ·  atajos.json  ·  asistente.db
```

</details>

---

## ⚙️ Configuración

Todo en la cabecera de [`servidor.py`](servidor.py):

```python
MODELO_LLM        = "llama3.1:8b"            # modelo de Ollama
MODELO_BUENO      = "small"                  # Whisper de la pasada final
VOZ_PIPER         = "es_ES-sharvard-medium"
TEMPERATURA       = 0.35                     # a 0.8 se inventaba datos
NUM_CTX           = 4096                     # ojo: igual en las dos llamadas
SILENCIO_CORTE_S  = 1.8                      # silencio que da por terminado
```

---

## 🗺️ Roadmap

| Hecho | Siguiente |
|:---|:---|
| ✅ Pipeline voz a voz con streaming | ⬜ Mensajes por Discord |
| ✅ Agenda persistente + tool calling | ⬜ Avisos a la hora, sin preguntar |
| ✅ Intérprete de fechas en español | ⬜ Captura de audio en el navegador |
| ✅ VAD con Silero (corte automático) | ⬜ `docker compose up` |
| ✅ Correo por Gmail (leer y enviar) | ⬜ Abstracción `LLMProvider` |
| ✅ Control del PC con lista blanca | |
| ✅ Eval suite del router | |

---

## ⚠️ Limitaciones conocidas

- **El micrófono es el del PC.** Lo captura Python, no el navegador. Moverlo
  al navegador exige HTTPS (`getUserMedia` no va sobre `http://`).
- **El control del sistema es de Windows** (`taskkill`, registro, rutas).
- **No avisa solo.** Hay que preguntarle o abrir la página.
- **Horas en letra.** "A las ocho cuarenta" no se interpreta; en cifras sí, y
  Whisper casi siempre transcribe los números como cifras.

---

## 📄 Licencia

> [!NOTE]
> Añade tu licencia. [MIT](https://choosealicense.com/licenses/mit/) es lo
> habitual para un proyecto así.

<p align="center">
  <sub>Construido con modelos que caben en una GPU de escritorio.</sub>
</p>
