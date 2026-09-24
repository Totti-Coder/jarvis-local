# Seguridad de Jarvis

Jarvis puede leer tu correo, escribir en tu calendario, abrir programas y
apagar el ordenador. Un agente así es un objetivo: quien lo controle, controla
todo eso. Este documento recoge **qué puede atacarlo, qué lo impide y qué queda
fuera**, con la evidencia de cada cosa.

---

## Caso real: el agujero del WebSocket (corregido)

### Cómo lo encontré

En 2026 el agente personal OpenClaw tuvo su peor fallo en el CVE-2026-25253
(CVSS 8.8): **visitar una página web bastaba para tomar el control del agente**.
El servidor corría en local, y su WebSocket no comprobaba desde qué web llegaba
la conexión.

Al estudiar el incidente comprobé si Jarvis tenía la misma clase de fallo.
**La tenía.**

### Por qué pasa

Los navegadores protegen las peticiones normales con la *política del mismo
origen*: una web no puede leer respuestas de otro sitio. **Los WebSockets son la
excepción.** Una página en `malo.com` puede abrir `ws://localhost:8000/ws` y
hablar con él sin restricción. El servidor recibe la cabecera `Origin`, y si
no la mira, no se entera de nada.

### Qué habría podido hacer una web maliciosa

| Acción | Cómo |
|---|---|
| Escucharte | Manda `alternar`: graba con el micro del PC y le llega tu transcripción |
| Leer tu correo | Envía audio con *"léeme los correos"* y le llega el resumen |
| Mandar correos desde tu Gmail | Lleva el flujo del borrador paso a paso. La confirmación no frena nada: la pregunta pendiente va por conexión, y **la conexión es suya** |

### Evidencia: antes y después

Script de ataque con el handshake escrito a mano (cabeceras exactas), contra el
commit anterior y contra el actual, con el servidor real en ambos casos:

```
=== ANTES (commit 3802a99) ===
  DENTRO  la propia interfaz                 101 Switching Protocols  <- ya recibe mensajes
  DENTRO  web maliciosa (https://malo.com)   101 Switching Protocols  <- ya recibe mensajes
  DENTRO  DNS rebinding (malo.com -> 127…)   101 Switching Protocols  <- ya recibe mensajes
  DENTRO  otra app local (localhost:3000)    101 Switching Protocols  <- ya recibe mensajes
  DENTRO  sin Origin                         101 Switching Protocols  <- ya recibe mensajes

=== AHORA ===
  DENTRO  la propia interfaz                 101 Switching Protocols  <- ya recibe mensajes
  FUERA   web maliciosa (https://malo.com)   403 Forbidden
  FUERA   DNS rebinding (malo.com -> 127…)   403 Forbidden
  FUERA   otra app local (localhost:3000)    403 Forbidden
  FUERA   sin Origin                         403 Forbidden
```

### El arreglo: tres defensas, una por ataque

Está todo en [`guardia.py`](../guardia.py), con 31 casos en
[`test_guardia.py`](../test_guardia.py) que corren en el CI.

**1. `Origin` debe ser la propia interfaz**, contra la web maliciosa. El
navegador pone esa cabecera y la página no puede falsificarla.

**2. `Host` en lista blanca**, contra el *DNS rebinding*. Es el truco para
saltarse la defensa 1: `malo.com` empieza apuntando a su servidor y, ya con la
página cargada, pasa a apuntar a `127.0.0.1`. Para el navegador la página y el
socket tienen el mismo origen, así que `Origin` coincide. Lo que no puede
cambiar es que el `Host` que llega sea `malo.com`, y ese no está en la lista. La
misma lista protege también las rutas HTTP.

**3. PIN en el modo `--red`**, contra quien esté en tu misma wifi. Antes
cualquiera en la red podía usar Jarvis entero. Ahora:
- el PIN se genera en cada arranque y solo se ve en la pantalla del PC;
- este mismo PC no lo necesita, y el móvil lo pide una vez y lo recuerda;
- **tras 5 fallos se bloquea hasta reiniciar**: seis cifras son un millón de
  combinaciones, que sin tope caen en minutos;
- se compara con `hmac.compare_digest`, así que el tiempo de respuesta no
  revela cuántas cifras van bien.

Verificado desde la IP de la red: sin PIN se rechaza con el código `4401`,
el PIN bueno entra, y al quinto fallo llega el `4403`. Después de eso, ni el PIN
bueno entra.

---

## Modelo de amenazas

| Amenaza | Defensa | Dónde |
|---|---|---|
| Una web abierta controla Jarvis | `Origin` + `Host` en lista blanca | `guardia.py` |
| Alguien en tu wifi | Solo `127.0.0.1` por defecto; `--red` exige PIN con bloqueo | `servidor.py`, `guardia.py` |
| **Inyección de prompts** (un correo o una web con *"ignora tus órdenes y…"*) | El contenido de fuera va a un modelo **sin herramientas**. El que tiene herramientas nunca lo lee | `servidor.py` (`_conversar`) |
| El modelo se inventa una acción | *"El modelo propone, el código dispone"*: guardas deterministas (p. ej. un destinatario que no dijiste se descarta) | `router.py` |
| Acción irreversible por un malentendido | Apagar, enviar y borrar se confirman antes, enseñando qué se va a hacer | `servidor.py` |
| El modelo ejecuta comandos | Nunca. Solo programas de una lista blanca o instalados en el menú Inicio, lanzados sin concatenar texto | `sistema.py` |
| Una rutina encadena algo peligroso | Sus pasos son de una lista cerrada (abrir, cerrar, atajo, cronómetro, horas), nunca un comando. Un atajo que pide confirmación no puede ir en una rutina | `rutinas.py` |
| Inyección de fórmulas en el CSV de horas | Una celda que empieza por `=` `+` `-` `@` se neutraliza antes de escribirla | `horas.py` |
| Robo de la cuenta de Google | Permisos mínimos: Gmail puede leer y redactar pero **no borrar**; Calendar solo eventos | `correo.py`, `calendario.py` |
| **Leer una URL que apunta a tu red (SSRF)** | Las URLs vienen del buscador, no del usuario. Se bloquean `127.0.0.1`, las IPs privadas, los puertos que no sean 80/443 y los esquemas raros, y se revisa cada redirección | `url_segura.py` |
| Perder la base de datos (tareas, horas facturables) | Copia diaria al arrancar, con la API de SQLite, rotando siete días | `respaldo.py` |
| Una dependencia comprometida o vulnerable | Las 11 con versión exacta en `requirements.txt`, y `pip-audit` en cada push del CI. Sin fijarlas, `pip install` trae la última que haya, que es el vector de cadena de suministro más común | `requirements.txt`, CI |
| **Whisper alucina y dispara una orden** | No devuelve silencio ante el silencio: con ruido escribió *"¡Suscríbete!"*. Si cae en "para" o "dale", el asistente actuaría solo. Se descarta por la probabilidad de no-voz del modelo, con el umbral medido | `filtro_voz.py` |
| El modelo se atasca y el turno queda colgado | Tiempo límite de 45 s por trozo: se avisa y la conversación sigue | `llm.py` |
| Secretos en GitHub | `.env`, `token.json`, contactos y BD en `.gitignore` | `.gitignore` |

Referencia: OWASP Top 10 para aplicaciones LLM (2025). LLM01 es la inyección de
prompts y LLM06 la agencia excesiva.

---

## Lo que queda fuera (a propósito)

- **Malware ya instalado en tu PC.** Puede falsificar cualquier cabecera y
  leer cualquier fichero. Contra eso no hay defensa desde dentro de la
  aplicación, y fingir lo contrario sería peor.
- **`token.json` en claro en el disco.** Es el siguiente paso: cifrarlo con
  DPAPI (el almacén de credenciales de Windows).
- **El certificado de `--red` es autofirmado.** El móvil avisa la primera
  vez. Sin un dominio público no hay otra forma para una IP privada.
- **El PIN viaja en la URL del WebSocket**, cifrado por TLS en `--red`. El
  servidor no guarda registro de accesos, así que no queda escrito en ningún
  sitio.
