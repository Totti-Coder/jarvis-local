# Jarvis como producto

Jarvis es un proyecto personal, no una empresa. Pero cada funcionalidad se ha
decidido como si tuviera usuarios: **para quién es, qué problema resuelve y qué
no se construye a propósito.** Este documento recoge ese razonamiento.

---

## El problema

Los asistentes de voz generalistas (Siri, Copilot, Gemini, el modo voz de
ChatGPT) son gratis y cada vez mejores. **Otro asistente genérico no aporta
nada.** El hueco está en lo que ellos no pueden ofrecer por diseño:

- **Datos que no pueden salir del equipo.** Un fisioterapeuta, un psicólogo o
  un abogado no puede pasar sus sesiones por Otter o Fathom. Los datos de
  salud son categoría especial en el RGPD, y esas herramientas procesan en la
  nube.
- **Actuar sin miedo.** Un agente que lee tu correo y controla tu PC es
  útil solo si te fías de él. Los agentes personales de 2026 han demostrado lo
  contrario (ver abajo).
- **Español de verdad.** *"Lo del 1 de septiembre"*, *"mañana por la tarde"* o
  *"a las 8.40"* dicho a las nueve de la noche. Esto no se arregla traduciendo.

## Para quién

**Profesionales que manejan información confidencial y viven del tiempo:**
consultores, abogados, sanitarios o freelancers técnicos. Su día a día es
agenda, correo, seguimientos y horas facturables, y ninguna herramienta en la
nube les vale para los datos de sus clientes.

## Por qué ahora

| Señal | Qué implica |
|---|---|
| La recepcionista telefónica con IA ya cuesta **desde 29 €/mes** en clínicas españolas [¹] | La voz en la nube es un commodity. No compensa competir ahí |
| Meta lanzó en junio de 2026 **su propio agente para WhatsApp Business** [²] | Los agentes de atención al cliente pasan a ser una función de la plataforma |
| OpenClaw, agente personal local: **CVE de control remoto (CVSS 8.8)**, un 12 % de su tienda de extensiones era malware, y borró en masa el correo de una usuaria ignorando sus órdenes [³] | Hay demanda de agentes locales, **y un vacío de confianza** |
| Ley de IA de la UE, artículo 50: desde el 2 de agosto de 2026, **un sistema que habla con personas debe decir que es una IA** [⁴] | La transparencia ya no es opcional en Europa |

## Posicionamiento

> **El asistente que trabaja en tu PC, en español, y nunca actúa sin preguntarte.**

| | Asistentes generalistas | Agentes locales tipo OpenClaw | **Jarvis** |
|---|:---:|:---:|:---:|
| Tus datos se quedan en tu equipo | ✗ | ✓ | **✓** |
| Controla tu PC | Limitado | ✓ (cualquier comando) | **✓ (lista blanca)** |
| Confirma lo irreversible | Variable | ✗ | **✓** |
| Resiste la inyección de prompts por diseño | Variable | ✗ | **✓** |
| Español nativo (fechas, horas habladas) | ✓ | Depende del modelo | **✓ (intérprete propio)** |
| Coste | Suscripción | Gratis + API | **0 € (sin APIs de pago)** |

---

## Decisiones de producto ya tomadas

Cada una salió de un fallo real o de una medición, no de una intuición.

| Decisión | Por qué |
|---|---|
| **Las direcciones de correo se teclean, no se dictan** | Whisper oyó `"Pablojeroza2000arrobajemail.com"`. Un correo mal enviado no se recupera |
| **Lo irreversible se confirma enseñando qué se va a hacer** | *"El 1 de septiembre hay 116 cosas: comprar pan, 58 veces… ¿Las quito todas?"* Un "sí" a ciegas no vale |
| **El cronómetro no pasa por el modelo** | *"Para"* tiene que parar ya. Un 8B tarda un segundo en decidir lo que una lista de verbos resuelve sin fallar |
| **Busca en internet solo cuando hace falta** | Una búsqueda cuesta segundos. Para *"¿quién pintó Las Meninas?"* sobra |
| **Te avisa sin preguntar, pero nunca te interrumpe** | Un recordatorio que pisa lo que estás diciendo es peor que no tenerlo |
| **El router se mide, no se intuye** | 97 frases reales al 100 %. Cada error encontrado entra como caso permanente |

## Lo que NO se construye (y por qué)

Esta lista pesa tanto como la de funcionalidades.

| Tentación | Por qué no |
|---|---|
| **Facturación propia** | Desde el 1 de julio de 2027 los autónomos necesitan software certificado Verifactu, con multas de hasta 50.000 € [⁵]. Jarvis prepara los datos (horas por cliente) y los exporta a un programa certificado |
| **Automatizar el WhatsApp personal** | Lo prohíben las condiciones de WhatsApp y las cuentas se bloquean. La vía legal es la API de Business, pensada para empresas |
| **Un agente que pulsa por cualquier parte de la pantalla** | Es frágil y es exactamente la *agencia excesiva* del OWASP Top 10 para LLM (LLM06). Jarvis actúa solo con herramientas acotadas |
| **Una tienda abierta de extensiones** | Es lo que convirtió la de OpenClaw en un canal de malware. Si algún día hay extensiones, serán revisadas y por MCP, el estándar del sector |
| **Recepcionista telefónica** | Mercado saturado y barato, y exige telefonía en la nube: lo contrario de lo que diferencia a Jarvis |

## Métricas

| Qué | Hoy |
|---|---|
| Acierto del router (elegir la acción correcta) | **97/97** frases reales |
| Tests | **649 casos**, 514 en el CI |
| Latencia de voz a respuesta | **~0,8 s** |
| APIs de pago | **0** |
| Conexiones ajenas aceptadas | **0** (antes, todas: [seguridad](seguridad.md)) |

## Hecho a partir de este análisis

- **Horas por cliente.** *"Empiezo con Acme"*, *"¿cuántas horas llevo este
  mes?"*, *"exporta las horas"*. **Hipótesis:** quien factura por horas pierde
  dinero por las que no apunta. Jarvis no factura (Verifactu): deja un CSV
  listo para el programa de facturación, en formato español y protegido
  contra la inyección de fórmulas de Excel. Whisper no escribe igual un
  nombre propio dos veces, así que los clientes se comparan **por cómo
  suenan en español** ("Akme" es Acme) y uno nuevo se confirma antes de
  crearlo. Olvidarse de parar es el error más caro, así que la sesión se ve
  siempre en la barra, se recuerda cada 3 horas y, al abrir Jarvis al día
  siguiente, se avisa. Se cierra a una hora pasada: *"terminé a las 7"*. Y
  lo olvidado se corrige hablando: *"ayer trabajé 2 horas para García"*,
  *"quítale media hora a Acme"*, *"borra la última sesión"* (con pregunta).
- **Rutinas por voz.** *"Modo trabajo"* abre tus programas, cierra
  distracciones, pone el cronómetro y empieza a contar horas. **Hipótesis:**
  es lo que más se nota en el día a día. El riesgo es bajo porque una rutina
  solo encadena pasos de una lista cerrada, y ninguno es un comando.
- **Temporizador.** *"Ponme un pomodoro"* o *"avísame en 10 minutos"*, y un
  paso de rutina (`{"temporizador": "25"}`) para que el "modo trabajo" sea
  de verdad un bloque de foco. Una frase con contenido (*"avísame en 10
  minutos de sacar la pizza"*) no es un temporizador: es una tarea con hora
  y va a la agenda.

## Siguiente: hipótesis por validar

1. **Actas locales de reuniones**: transcripción, resumen, tareas al
   calendario y correo de seguimiento, sin que el audio salga del PC.
   **Hipótesis:** es la razón por la que un profesional con datos sensibles lo
   usaría en vez de Otter.
2. **Validarlo** con cinco usuarios reales antes de construirlo: es lo más
   caro de todo.

**Limitación conocida:** hace falta una GPU con 6-8 GB para el modelo y
Whisper, y muchos portátiles de oficina no la tienen. Las salidas posibles
son un mini-PC preconfigurado o un modo híbrido: la voz y los datos siempre en
local, y el razonamiento en una API con acuerdo RGPD, solo si el usuario lo
elige.

---

<sub>

[¹] [Zerolag — AI receptionist for clinics (Spain 2026)](https://zerolagia.com/en/blog/ai-receptionist-clinics-is-it-worth-it) ·
[²] [Ecosistema Startup — WhatsApp Business lanza agente IA](https://ecosistemastartup.com/whatsapp-business-lanza-agente-ia-global-en-2026/) ·
[³] [Sliq — OpenClaw security incidents](https://getsliq.com/blog/openclaw-security-incidents-timeline), [Hive Security](https://hivesecurity.gitlab.io/blog/openclaw-ai-agent-security-crisis-2026/) ·
[⁴] [Cooley — AI Act transparency obligations](https://www.cooley.com/news/insight/2026/2026-08-03-eu-ai-act-transparency-obligations-take-effect-2-august-2026) ·
[⁵] [Infoautónomos — Verifactu](https://www.infoautonomos.com/blog/verifactu-cuando-es-obligatorio-a-quien-afecta/)

</sub>
