"""Servidor del asistente de voz.

Python captura el micro, transcribe, piensa y habla. El navegador es solo
la pantalla: se comunican por WebSocket.

Aqui vive UNA cosa: la conversacion. Todo lo demas esta en su modulo —
ajustes, audio, voz, escucha, router, redactor— y este fichero solo los
coordina.

Arrancar:  python servidor.py
Abrir:     http://localhost:8000
"""

import asyncio
import hashlib
import json
import re
import struct
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import ollama
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import buscar
import calendario
import correo
import cronometro
import guardia
import horas
import memoria
import rutinas
import sistema
from ajustes import (CORTE_POR_SILENCIO, FRECUENCIA, MAX_GRABACION_S,
                     MAX_TURNOS, MIN_HABLA_S, MODELO_LLM, MS_PARCIAL,
                     NUM_CTX, PROMPT_SISTEMA, SILENCIO_CORTE_S,
                     TEMPERATURA, VAD_VENTANA_S, VENTANA_PARCIAL_S)
from audio import Microfono, MicrofonoRemoto
from escucha import (OPCIONES_VAD, get_speech_timestamps, stt_bueno,
                     stt_rapido, transcribir)
from redactor import redactar_correo
from router import (CMDS_BORRADOR, destinatario_inventado, ejecutar, enrutar,
                    es_afirmacion, momento_en, pide_dejarlo, prompt_con_fecha)
from voz import (cortar_voz, frase_terminada, hablar, permitir_voz,
                 salida_voz_a)

AQUI = Path(__file__).parent

memoria.preparar()
horas.preparar()
# La agenda tambien lee Google Calendar. Se engancha aqui, en el servidor,
# y no dentro de memoria: asi los tests de memoria siguen sacando la lista
# solo de SQLite y dan lo mismo tengas lo que tengas en tu calendario.
if calendario.disponible():
    memoria.fuente_externa = calendario.eventos_para_agenda
    memoria.crear_externo = calendario.crear_evento
    memoria.borrar_externo = calendario.borrar_evento
print("Listo.")
print(f"  {calendario.estado()}")
print(f"  {correo.estado()}")


# Cada cuanto se mira si toca avisar de algo. Mas a menudo no aporta: los
# avisos van con diez minutos de antelacion.
AVISO_CADA_S = 20


def recortar_historial(historial):
    """Quita los turnos viejos, dejando siempre el prompt de sistema."""
    if len(historial) > MAX_TURNOS + 1:
        del historial[1:3]


# ---------------------------------------------------------------
# SERVIDOR
# ---------------------------------------------------------------

app = FastAPI()
AQUI = Path(__file__).parent


# Three.js se sirve desde aquí, no desde un CDN: el asistente tiene que
# funcionar sin conexión, y una etiqueta <script src="https://..."> lo
# convertiría en mentira.
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")


def version_interfaz():
    """Huella de los ficheros que se envían al navegador.

    Se calcula en cada conexión, no al arrancar: así vale también si se
    toca el HTML con el servidor ya en marcha.
    """
    huella = hashlib.sha1()
    for f in (AQUI / "index.html", AQUI / "static" / "nucleo.js"):
        try:
            huella.update(f.read_bytes())
        except OSError:
            pass
    return huella.hexdigest()[:12]


@app.middleware("http")
async def solo_hosts_conocidos(peticion, siguiente):
    # La misma lista que el WebSocket (ver guardia.py). Contra el DNS
    # rebinding: una web que se hace pasar por localhost llega con su
    # propio nombre en Host, y aquí se queda.
    if guardia._nombre_de(peticion.headers.get("host")) not in guardia.HOSTS:
        return PlainTextResponse("Host no permitido", status_code=400)
    return await siguiente(peticion)


@app.get("/")
async def raiz():
    # Sin no-cache, tras editar el HTML el navegador seguía sirviendo el
    # de su caché aunque se recargara, y parecía que el cambio no existía.
    return FileResponse(AQUI / "index.html",
                        headers={"Cache-Control": "no-store"})


class Conversacion:
    """Una conexion del navegador: un microfono, un historial, un turno.

    Antes esto era una funcion de 795 lineas con trece funciones anidadas
    dentro. Funcionaba, pero para saber que tocaba el "pendiente" habia
    que leerla entera. Como clase, cada pieza tiene nombre y el estado que
    comparten esta declarado arriba en vez de escondido en un closure.

    Va POR CONEXION a proposito: dos pestanas abiertas son dos
    conversaciones independientes. Cuando era global se pisaban los turnos.
    """

    def __init__(self, sock):
        self.sock = sock
        self.micro = Microfono()
        # Una memoria por conexión. Antes era global, así que dos pestañas
        # abiertas compartían conversación y se pisaban los turnos.
        self.historial = [{"role": "system", "content": PROMPT_SISTEMA}]
        # Lo irreversible no se hace a la primera: se pregunta y se espera un
        # "sí". Aquí se recuerda qué quedó pendiente. Va por conexión, como el
        # historial: cada pestaña lleva su propia pregunta en el aire.
        #   tipo "sistema" -> apagar o reiniciar
        #   tipo "correo"  -> un email escrito y sin enviar
        self.pendiente = {"tipo": None, "datos": None}
        self.comandos = asyncio.Queue()
        self.tarea_receptor = None
        self.grabando = False
        self.tarea_medidor = None
        self.tarea_parciales = None
        self.tarea_limite = None
        self.tarea_silencio = None

        self.tarea_avisos = None
        self.estado = "inactivo"
        # El reloj se inyecta, como en memoria: "lo del 1 de septiembre" es
        # de este año o del siguiente segun el dia en que se diga, y un test
        # que dependa del calendario de verdad cambia solo de resultado.
        self.ahora = datetime.now
        # Uno por pestaña, como el historial: el panel lo pinta esta página
        self.crono = cronometro.Cronometro()

        # Se fija en correr(), que es cuando hay bucle de asyncio
        self.bucle_principal = None

    async def correr(self):
        """Atiende la conexion hasta que el navegador se va."""
        await self.sock.accept()
        self.bucle_principal = asyncio.get_running_loop()
        try:
            # La huella de la interfaz va lo primero. Al reiniciar el servidor,
            # la página NO se recarga: el WebSocket se cae, reconecta solo, y la
            # pestaña sigue con el JavaScript de antes. Todo parece funcionar
            # —el servidor manda bien sus mensajes— pero los nuevos los ignora
            # sin dar ningún error. Pasó con el popup del correo: el servidor
            # decía "¿a quién se lo mando?" y el popup no salía por ningún lado.
            await self.enviar(tipo="version", valor=version_interfaz())
            # Tras reiniciar el servidor la pestaña sigue enseñando el panel
            # de antes, contando un tiempo que ya no existe: se le corrige
            await self.enviar(tipo="crono", **self.crono.estado())

            await self.enviar(tipo="estado", valor="inactivo")

            # Nada más entrar, cuenta lo que hay para hoy. Es la diferencia entre
            # una libreta (hay que ir a mirarla) y algo que de verdad te recuerda
            # las cosas. Si no hay nada apuntado, no dice nada.
            resumen = await asyncio.to_thread(memoria.resumen_del_dia)
            if resumen:
                await self.enviar(tipo="saludo", texto=resumen)
                await self.enviar(tipo="estado", valor="hablando")
                await asyncio.to_thread(hablar, resumen)
                await self.enviar(tipo="fin_respuesta")
                await self.enviar(tipo="estado", valor="inactivo")

            # Una tarea aparte no deja NUNCA de leer el socket. Antes el bucle
            # principal hacía el receive él mismo, así que mientras el asistente
            # pensaba o hablaba nadie leía: las pulsaciones se acumulaban y se
            # ejecutaban al terminar, provocando grabaciones fantasma.
            FIN = object()

            async def receptor():
                try:
                    while True:
                        mensaje = await self.sock.receive()
                        if mensaje.get("type") == "websocket.disconnect":
                            break
                        crudo = mensaje.get("bytes")
                        if crudo is not None:
                            # El audio NO pasa por la cola de comandos: llegan
                            # decenas de trozos por segundo y taparían las
                            # pulsaciones, que es justo lo que hay que atender
                            # rápido para poder interrumpirle. Va directo.
                            if isinstance(self.micro, MicrofonoRemoto):
                                self.micro.alimentar(crudo)
                            continue
                        texto = mensaje.get("text")
                        if texto is not None:
                            await self.comandos.put(json.loads(texto))
                except Exception:
                    pass
                await self.comandos.put(FIN)

            self.tarea_receptor = asyncio.create_task(receptor())
            self.tarea_avisos = asyncio.create_task(self.vigilar_avisos())

            while True:
                mensaje = await self.comandos.get()
                if mensaje is FIN:
                    break
                # El navegador avisa de que tiene micrófono propio (móvil, o
                # cualquier pestaña servida por https). A partir de aquí el
                # audio entra y sale por él, no por la tarjeta de sonido.
                if mensaje.get("cmd") == "micro_navegador":
                    if not isinstance(self.micro, MicrofonoRemoto):
                        self.micro.parar()
                        self.micro = MicrofonoRemoto()
                    salida_voz_a(self.poner_voz_en_cola)
                    # Se imprime lo que el navegador ha negociado de verdad. Sin
                    # esto, "desde el móvil se entiende mal" solo se puede
                    # investigar adivinando.
                    print(f"[audio] micrófono y voz por el navegador "
                          f"| contexto {mensaje.get('contexto_hz')} Hz "
                          f"| pista {mensaje.get('ajustes')}")
                    print(f"[audio] {mensaje.get('agente', '')}")
                    await self.enviar(tipo="audio_navegador", valor=True)
                    continue

                if mensaje.get("cmd") in CMDS_BORRADOR:
                    await self.resolver_borrador(mensaje)
                    continue
                if mensaje.get("cmd") == "crono":
                    await self.boton_cronometro(mensaje)
                    continue
                if mensaje.get("cmd") != "alternar":
                    continue

                if not self.grabando:
                    self.grabando = True
                    # Con los avisos, Jarvis puede estar hablando SOLO cuando
                    # pulsas. Si no se le calla, el micro graba su voz y se
                    # transcribe encima de la tuya. La pausa deja al hilo de
                    # voz ver el corte antes de volver a permitirle hablar.
                    if self.estado == "hablando":
                        cortar_voz()
                        await self.enviar(tipo="voz_corta")
                        await asyncio.sleep(0.1)
                    permitir_voz()
                    self.micro.empezar()
                    await self.enviar(tipo="estado", valor="escuchando")
                    self.tarea_medidor = asyncio.create_task(self.medidor())
                    self.tarea_parciales = asyncio.create_task(self.parciales())
                    self.tarea_limite = asyncio.create_task(self.vigilar_limite())
                    self.tarea_silencio = asyncio.create_task(self.vigilar_silencio())
                    continue

                # Se procesa el turno como tarea, vigilando a la vez si llega
                # otra pulsación: eso es lo que permite interrumpirle.
                self.grabando = False
                tarea_turno = asyncio.create_task(self.parar_grabacion())
                while True:
                    tarea_cmd = asyncio.create_task(self.comandos.get())
                    hechas, _ = await asyncio.wait(
                        {tarea_turno, tarea_cmd},
                        return_when=asyncio.FIRST_COMPLETED)
                    # Los botones del cronómetro van en el acto, sin cortarle
                    # ni esperar a que acabe: una pausa que llega tres
                    # segundos tarde ya no mide lo que tenía que medir.
                    llegado = tarea_cmd.result() if tarea_cmd in hechas else None
                    if isinstance(llegado, dict) and llegado.get("cmd") == "crono":
                        await self.boton_cronometro(llegado)
                        if tarea_turno.done():
                            hechas = set()      # atendido: no es interrupción
                            break
                        continue
                    break

                if not hechas:
                    continue
                if tarea_cmd in hechas:
                    llegado = tarea_cmd.result()
                    # Los botones del borrador no son una interrupción: si se
                    # pulsan mientras aún está diciendo "te lo he preparado",
                    # se le deja acabar y se atienden después. Tratarlos como
                    # una pulsación de micro habría descartado el correo.
                    if (llegado is not FIN and isinstance(llegado, dict)
                            and llegado.get("cmd") in CMDS_BORRADOR):
                        await tarea_turno
                        await self.resolver_borrador(llegado)
                        continue
                    # Pulsó mientras pensaba o hablaba: se le calla en el acto
                    cortar_voz()
                    # Y el navegador tira lo que le quede en el buffer: si no,
                    # seguiria oyendose la frase por el movil despues de callarle
                    await self.enviar(tipo="voz_corta")
                    tarea_turno.cancel()
                    try:
                        await tarea_turno
                    except asyncio.CancelledError:
                        pass
                    await self.enviar(tipo="fin_respuesta")
                    await self.enviar(tipo="estado", valor="inactivo")
                    print("[voz] interrumpido por el usuario")
                    # Callarle ya es la acción: esa pulsación no graba nada más.
                    if tarea_cmd.result() is FIN:
                        break
                else:
                    tarea_cmd.cancel()
                    try:
                        await tarea_cmd
                    except asyncio.CancelledError:
                        pass

        except WebSocketDisconnect:
            pass
        finally:
            for t in (self.tarea_medidor, self.tarea_parciales, self.tarea_limite,
                      self.tarea_silencio, self.tarea_receptor, self.tarea_avisos):
                if t:
                    t.cancel()
            cortar_voz()          # si se va con el asistente hablando, que calle
            permitir_voz()        # y que la próxima conexión pueda hablar
            # La salida de voz es global: si esta conexion la habia desviado al
            # navegador, hay que devolverla o la siguiente se quedaria muda.
            salida_voz_a(None)
            self.micro.parar()


    async def enviar(self, **datos):
        # El estado se apunta al pasar: es la unica fuente fiable de si
        # Jarvis esta libre. Todo cambio de estado sale por aqui, asi que
        # lo que ve la interfaz y lo que cree el servidor no se separan.
        if datos.get("tipo") == "estado":
            self.estado = datos.get("valor")
        try:
            await self.sock.send_text(json.dumps(datos))
        except Exception:
            pass

    def poner_voz_en_cola(self, muestras, frecuencia):
        """Manda un trozo de voz al navegador. Lo llama el hilo de voz.

        El hilo de voz no es asíncrono y no puede tocar el socket, así que
        el envío se programa en el bucle principal. Va en int16 con una
        cabecera de 4 bytes con la frecuencia: Piper sintetiza a 22050 y
        el navegador tiene que saberlo para no reproducirlo agudo.
        """
        pcm = (np.clip(muestras, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        paquete = struct.pack("<I", int(frecuencia)) + pcm
        try:
            asyncio.run_coroutine_threadsafe(self.sock.send_bytes(paquete),
                                             self.bucle_principal)
        except RuntimeError:
            pass          # el bucle ya no existe: la conexión se cerró

    async def decir_suelto(self, frase, etiqueta=None):
        """Dice algo que no contesta a nada hablado: los botones del panel."""
        await self.enviar(tipo="dicho", texto=frase, etiqueta=etiqueta)
        await self.enviar(tipo="estado", valor="hablando")
        await asyncio.to_thread(hablar, frase)
        # Si mientras hablaba se pulso el micro, el estado ya es otro.
        # Devolverlo a "inactivo" dejaria la interfaz en reposo con el
        # microfono grabando, y el movil —que solo manda audio mientras
        # el estado es "escuchando"— dejaria de enviar a media frase.
        if not self.grabando and self.estado == "hablando":
            await self.enviar(tipo="estado", valor="inactivo")

    async def decir_turno(self, frase, etiqueta=None, pregunta=None):
        """Dice una frase corta y cierra el turno, sin pasar por el modelo.

        Con `pregunta` cierra un turno de voz (y lo apunta en el historial).
        Sin ella la frase va suelta: es lo que hace falta cuando quien ha
        hablado es un botón del popup y no el micrófono.
        """
        if pregunta is None:
            await self.decir_suelto(frase, etiqueta)
            return
        if etiqueta:
            await self.enviar(tipo="herramienta", nombre=etiqueta)
        await self.enviar(tipo="token", texto=frase)
        await self.enviar(tipo="estado", valor="hablando")
        await asyncio.to_thread(hablar, frase)
        self.historial.append({"role": "user", "content": pregunta})
        self.historial.append({"role": "assistant", "content": frase})
        recortar_historial(self.historial)
        await self.enviar(tipo="fin_respuesta")
        await self.enviar(tipo="estado", valor="inactivo")

    async def avanzar_correo(self, borrador, pregunta=None):
        """Pide lo que le falte al correo, y abre el popup cuando esté.

        El orden no es capricho. Primero A QUIÉN, y eso se teclea en el
        popup: una dirección dictada que Whisper oye mal no se puede
        arreglar repitiéndola, porque la vuelve a oír mal. El asunto y el
        texto van por voz, que para eso sí acierta.

        Lo que ya venga dicho no se vuelve a preguntar: "mándale un correo
        a Ana diciéndole que llego tarde" no pasa por ningún paso.
        """
        agenda = [{"nombre": c["nombre"], "email": c["email"]}
                  for c in correo.cargar_contactos()]

        if not borrador.get("email"):
            self.pendiente["tipo"] = "correo_paso"
            self.pendiente["datos"] = dict(borrador, paso="destinatario")
            await self.enviar(tipo="borrador", modo="destinatario", contactos=agenda,
                         destinatario="", asunto=borrador.get("asunto", ""),
                         mensaje=borrador.get("mensaje", ""))
            await self.decir_turno("¿A quién se lo mando?", "correo", pregunta)
            return

        if not borrador.get("asunto"):
            self.pendiente["tipo"] = "correo_paso"
            self.pendiente["datos"] = dict(borrador, paso="asunto")
            await self.decir_turno(f"Vale, para {borrador['nombre']}. "
                              "¿De qué se trata?", "correo", pregunta)
            return

        if not borrador.get("mensaje"):
            self.pendiente["tipo"] = "correo_paso"
            self.pendiente["datos"] = dict(borrador, paso="mensaje")
            await self.decir_turno("¿Y qué le digo?", "correo", pregunta)
            return

        # Ya está entero: se abre para revisarlo con el teclado antes de
        # mandarlo. Un correo enviado a quien no era no se recupera.
        self.pendiente["tipo"] = "correo"
        self.pendiente["datos"] = dict(borrador)
        await self.enviar(tipo="borrador", modo="completo", contactos=agenda,
                     destinatario=borrador["nombre"], email=borrador["email"],
                     asunto=borrador["asunto"], mensaje=borrador["mensaje"])
        await self.decir_turno("Te lo he preparado. Míralo y dime si lo mando.",
                          "borrador", pregunta)

    def resolver_destino(self, escrito):
        """Convierte lo tecleado en (dirección, nombre para decir).

        El destinatario NO se coge tal cual de lo que manda el navegador:
        o es alguien de la agenda, o es algo con forma de dirección.
        Devuelve (None, None) si no es ni una cosa ni la otra.
        """
        escrito = (escrito or "").strip()
        contacto = correo.buscar_contacto(escrito)
        if contacto:
            return contacto["email"], contacto["nombre"]
        if correo.es_direccion(escrito):
            return escrito, escrito
        return None, None

    async def resolver_borrador(self, mensaje):
        """Botones del popup: elegir destinatario, enviar, o descartar.

        Solo hace algo si ESTA conexión tiene un correo a medias, y eso
        es lo que impide que alguien de la red se monte uno desde cero:
        el servidor escucha en 0.0.0.0, pero `pendiente` va por conexión,
        así que otra pestaña o otro equipo tiene el suyo vacío y estos
        comandos le caen en saco roto.
        """
        if self.pendiente["tipo"] not in ("correo", "correo_paso"):
            return
        tipo, datos = self.pendiente["tipo"], self.pendiente["datos"]
        self.pendiente["tipo"], self.pendiente["datos"] = None, None
        cmd = mensaje.get("cmd")

        def dejar_abierto():
            """Devuelve el borrador a su sitio: lo escrito no se pierde."""
            self.pendiente["tipo"], self.pendiente["datos"] = tipo, datos

        if cmd == "borrador_cancelar":
            print("[correo] descartado desde el popup")
            await self.enviar(tipo="borrador_cerrar")
            await self.decir_suelto("Vale, lo dejo.", "correo")
            return

        # Primer paso: solo el destinatario. Se cierra el popup y se sigue
        # preguntando por voz, que es lo cómodo para el asunto y el texto.
        if cmd == "borrador_destinatario":
            destino, visible = self.resolver_destino(mensaje.get("destinatario"))
            if not destino:
                dejar_abierto()
                await self.enviar(tipo="borrador_error",
                             texto="Elige a alguien de la agenda, o escribe "
                                   "una dirección entera.")
                return
            datos.pop("paso", None)
            datos["email"], datos["nombre"] = destino, visible
            await self.enviar(tipo="borrador_cerrar")
            await self.avanzar_correo(datos)
            return

        # Último paso: mandar lo que hay escrito en el popup
        asunto = (mensaje.get("asunto") or "").strip() or datos.get("asunto") or "Sin asunto"
        cuerpo = (mensaje.get("mensaje") or "").strip()
        if not cuerpo:
            dejar_abierto()
            await self.enviar(tipo="borrador_error", texto="El correo está vacío.")
            return

        destino, visible = self.resolver_destino(mensaje.get("destinatario"))
        if not destino:
            dejar_abierto()
            await self.enviar(tipo="borrador_error",
                         texto="Elige a quién va, o escribe una dirección entera.")
            return

        print(f"[correo] enviado desde el popup -> {destino}")
        ok, frase = await asyncio.to_thread(correo.enviar, destino, asunto, cuerpo)
        if not ok:
            dejar_abierto()
            await self.enviar(tipo="borrador_error", texto=frase)
            return
        await self.enviar(tipo="borrador_cerrar")
        await self.decir_suelto(frase, f"correo a {visible}")

    async def medidor(self):
        """Manda nivel y espectro para que el anillo reaccione al micro."""
        while True:
            await self.enviar(tipo="nivel",
                         valor=round(self.micro.nivel, 3),
                         bandas=self.micro.espectro())
            await asyncio.sleep(0.05)

    async def parciales(self):
        """Re-transcribe solo los últimos VENTANA_PARCIAL_S segundos.

        Si se re-transcribiera el audio entero cada vez, cada vuelta costaría
        más que la anterior (crece con la duración de la grabación al cuadrado
        contando el total de trabajo). Con la ventana fija el coste por vuelta
        es constante.
        """
        while True:
            await asyncio.sleep(MS_PARCIAL / 1000)
            audio = self.micro.audio_reciente(VENTANA_PARCIAL_S)
            if audio is None or len(audio) < FRECUENCIA * 0.4:
                continue
            try:
                texto = await asyncio.to_thread(transcribir, audio, stt_rapido, "parcial")
            except Exception as e:
                # Sin esto, una excepción aquí mataba la tarea en silencio
                # (asyncio se traga el error de una tarea que nadie espera)
                # y el texto en vivo dejaba de aparecer sin explicación.
                print(f"Error en transcripción parcial: {e}")
                return
            if texto:
                await self.enviar(tipo="parcial", texto=texto)

    async def responder(self, pregunta):
        """Un turno entero. Esto solo decide el camino; cada uno va aparte.

        El ORDEN importa y es el de siempre:

          1. Si se le acababa de preguntar algo, esto es la respuesta y no
             pasa por el router: el modelo enrutaria un "si" suelto como
             charla y se perderia.
          2. La web y el correo no devuelven una respuesta, sino material
             que hay que leer. Van al conversador, no vuelven de aqui.
          3. Lo irreversible (apagar, enviar) se confirma antes de hacerse.
          4. El resto de herramientas contestan ellas, con texto exacto.
          5. Y si no hubo herramienta, se conversa.
        """
        await self.enviar(tipo="estado", valor="pensando")

        if self.pendiente["tipo"]:
            await self._resolver_pendiente(pregunta)
            return

        # El cronómetro no pasa por el modelo: "para" tiene que parar ya
        accion = cronometro.orden(pregunta, self.crono.visible)
        if accion:
            await self._usar_cronometro(accion, pregunta)
            return
        # Las horas y las rutinas, igual: frases fijas, sin el modelo
        abierta = await asyncio.to_thread(horas.abierta)
        conocidos = await asyncio.to_thread(horas.clientes)
        pedido = horas.orden(pregunta, abierta is not None, conocidos)
        if pedido:
            await self._usar_horas(*pedido, pregunta, conocidos)
            return
        rutina = await asyncio.to_thread(rutinas.buscar, pregunta)
        if rutina:
            await self._correr_rutina(rutina, pregunta)
            return

        nombre, args = await self._enrutar(pregunta)

        material = None
        if nombre == "buscar_en_web":
            material = await self._material_de_la_web(args, pregunta)
            if material is None:
                return                      # ya se le ha contestado
            nombre = None                   # sigue por la via conversacional
        elif nombre == "leer_correos":
            material = await self._material_del_correo(args, pregunta)
            if material is None:
                return
            nombre = None

        if nombre == "control_sistema":
            if await self._confirmar_accion(args, pregunta):
                return
        elif nombre == "enviar_correo":
            await self._preparar_correo(args, pregunta)
            return
        elif nombre == "completar_tarea":
            # La fecha va antes que los grupos: "borra todo lo que tenia el
            # 31 de agosto" contiene "todo", y sin este orden se entendia
            # como "borra todo" y proponia vaciar la agenda entera.
            if await self._confirmar_borrado_por_fecha(pregunta):
                return
            if await self._confirmar_borrado(args, pregunta):
                return
            if await self._buscar_en_el_calendario(args, pregunta):
                return

        if nombre and await self._contestar_con_herramienta(nombre, args, pregunta):
            return

        await self._conversar(pregunta, material)

    async def _usar_cronometro(self, accion, pregunta):
        """Una orden de voz al cronómetro: se hace, se pinta y se cuenta."""
        frase = self.crono.aplicar(accion)
        await self.enviar(tipo="crono", **self.crono.estado())
        await self.decir_turno(frase, "cronómetro", pregunta)

    async def _usar_horas(self, accion, dato, pregunta, conocidos=None):
        if accion == "empezar":
            # "Akme" es Acme: Whisper no escribe igual un nombre dos veces.
            # Y uno que no se parece a ninguno se confirma antes de crearlo:
            # "empiezo con el informe" no debe abrir un cliente "Informe".
            conocido = horas.cliente_parecido(dato, conocidos or {})
            if conocido is None:
                nombre = horas.nombre_bonito(dato)
                self.pendiente["tipo"], self.pendiente["datos"] = "cliente_nuevo", nombre
                await self.decir_turno(
                    f"No tengo ningún cliente llamado {nombre}. "
                    f"¿Lo creo y empiezo a contar?", "horas", pregunta)
                return
            dato = conocido
        frase = await asyncio.to_thread(horas.responder, accion, dato,
                                        pregunta, self.ahora())
        print(f"[horas] {accion} {dato or ''}".rstrip())
        await self.decir_turno(frase, "horas", pregunta)

    async def _crear_cliente(self, nombre, pregunta):
        if not es_afirmacion(pregunta):
            print(f"[horas] cliente nuevo descartado: {nombre}")
            await self.decir_turno("Vale, no cuento nada.", None, pregunta)
            return
        frase = await asyncio.to_thread(horas.empezar, nombre, self.ahora())
        print(f"[horas] cliente nuevo: {nombre}")
        await self.decir_turno(frase, "horas", pregunta)

    async def _correr_rutina(self, rutina, pregunta):
        """Los pasos en orden. Si uno falla, los demás siguen y se dice
        cuál falló: que no se abra Spotify no es motivo para no empezar
        a contar horas."""
        fallos = []
        for tipo, valor in rutina["pasos"]:
            try:
                if tipo == "abrir":
                    r = await asyncio.to_thread(sistema.abrir_programa, valor)
                    if not r.startswith("Abriendo"):
                        fallos.append(f"abrir {valor}")
                elif tipo == "cerrar":
                    # Si ya estaba cerrado, mejor: no es un fallo
                    await asyncio.to_thread(sistema.cerrar_programa, valor)
                elif tipo == "atajo":
                    r = await asyncio.to_thread(sistema.ejecutar_atajo, valor)
                    if r.startswith(("No conozco", "No encuentro", "No he podido",
                                     "No tienes")):
                        fallos.append(f"lanzar {valor}")
                elif tipo == "cronometro":
                    if valor not in cronometro.ACCIONES:
                        fallos.append(f"el cronómetro ({valor})")
                        continue
                    self.crono.aplicar(valor)
                    await self.enviar(tipo="crono", **self.crono.estado())
                elif tipo == "horas":
                    if valor.lower() in ("parar", "terminar"):
                        await asyncio.to_thread(horas.parar, self.ahora())
                    else:
                        await asyncio.to_thread(horas.empezar, valor, self.ahora())
            except Exception as e:
                print(f"[rutina] {rutina['nombre']}: {tipo} {valor!r} -> {e}")
                fallos.append(f"{tipo} {valor}")

        frase = rutina["dice"]
        if fallos:
            frase += f" No he podido: {', '.join(fallos)}."
        print(f"[rutina] {rutina['nombre']}: {len(rutina['pasos'])} pasos, "
              f"{len(fallos)} fallidos")
        await self.decir_turno(frase, "rutina", pregunta)

    async def boton_cronometro(self, mensaje):
        """Un botón del panel. Sin voz: ya lo estás viendo."""
        accion = mensaje.get("accion")
        if accion in cronometro.ACCIONES:
            self.crono.aplicar(accion)
        await self.enviar(tipo="crono", **self.crono.estado())

    async def _enrutar(self, pregunta):
        """¿Hace falta una herramienta? (None, None) si es charla."""
        try:
            t0 = time.monotonic()
            nombre, args = await asyncio.to_thread(enrutar, pregunta)
            print(f"[router] {time.monotonic()-t0:.1f}s -> "
                  f"{nombre or 'conversación'}")
            return nombre, args
        except Exception as e:
            print(f"Error en el router: {e}")
            return None, None

    # ---------------------------------------------------------------
    # 1. LO QUE SE LE ACABABA DE PREGUNTAR
    # ---------------------------------------------------------------

    async def _resolver_pendiente(self, pregunta):
        """Este turno contesta a una pregunta de Jarvis, no es una orden nueva."""
        tipo, datos = self.pendiente["tipo"], self.pendiente["datos"]
        self.pendiente["tipo"], self.pendiente["datos"] = None, None

        if tipo == "sistema":
            await self._confirmar_apagado(datos, pregunta)
        elif tipo == "borrado":
            await self._quitar_grupo(datos, pregunta)
        elif tipo == "borrado_calendario":
            await self._quitar_del_calendario(datos, pregunta)
        elif tipo == "borrado_dia":
            await self._quitar_del_dia(datos, pregunta)
        elif tipo == "correo_paso":
            await self._seguir_correo(datos, pregunta)
        elif tipo == "correo":
            await self._confirmar_envio(datos, pregunta)
        elif tipo == "cliente_nuevo":
            await self._crear_cliente(datos, pregunta)

    async def _confirmar_apagado(self, accion, pregunta):
        if es_afirmacion(pregunta):
            print(f"[sistema] confirmado: {accion}")
            resultado = await asyncio.to_thread(sistema.ejecutar_accion, accion)
            await self.decir_turno(resultado, "control sistema", pregunta)
            return
        print(f"[sistema] NO confirmado: {accion} descartado")
        verbo = "reinicio" if accion == "reiniciar" else "apago"
        await self.decir_turno(f"Vale, no {verbo} nada.", None, pregunta)

    async def _quitar_grupo(self, grupo, pregunta):
        """Ya ha dicho si quiere que se quiten las tareas del grupo."""
        if not es_afirmacion(pregunta):
            print(f"[agenda] NO confirmado: no se quita nada ({grupo})")
            await self.decir_turno("Vale, las dejo.", "agenda", pregunta)
            return
        # Se vuelven a buscar AHORA, no se guardaron antes: entre la
        # pregunta y el "si" el usuario ha podido apuntar otra cosa.
        filas = await asyncio.to_thread(memoria.tareas_del_grupo, grupo)
        print(f"[agenda] confirmado: quito {len(filas)} ({grupo})")
        resultado = await asyncio.to_thread(memoria.completar_varias, filas)
        await self.decir_turno(resultado, "completar tarea", pregunta)

    async def _seguir_correo(self, datos, pregunta):
        """Rellena el hueco que se estaba preguntando y sigue con el siguiente.

        Lo dicho ES la respuesta, no una orden: no pasa por el router, que
        enrutaria "que llego tarde a la cena" como una tarea que apuntar.
        """
        if pide_dejarlo(pregunta):
            print("[correo] abandonado a medias")
            await self.enviar(tipo="borrador_cerrar")
            await self.decir_turno("Vale, lo dejo.", "correo", pregunta)
            return

        paso = datos.pop("paso", "")
        dicho = pregunta.strip()

        if paso == "asunto":
            # El asunto es una línea: sin punto final y sin más
            datos["asunto"] = dicho.rstrip(" .").strip()

        elif paso == "mensaje":
            # Lo dicho es un ENCARGO, no el texto: "mándale algo formal
            # pidiéndole que venga a casa". Lo redacta el modelo, y luego
            # se revisa en el popup.
            await self.enviar(tipo="estado", valor="pensando")
            t0 = time.monotonic()
            datos["mensaje"] = await asyncio.to_thread(
                redactar_correo, dicho, datos.get("nombre", ""),
                datos.get("asunto", ""))
            print(f"[correo] redactado en {time.monotonic()-t0:.1f}s")

        elif paso == "destinatario":
            # Contestó hablando en vez de por el popup
            destino, visible = self.resolver_destino(dicho)
            if not destino:
                self.pendiente["tipo"] = "correo_paso"
                self.pendiente["datos"] = dict(datos, paso="destinatario")
                await self.decir_turno(
                    "No tengo a esa persona en la agenda. "
                    "Escríbelo en el recuadro.", "correo", pregunta)
                return
            datos["email"], datos["nombre"] = destino, visible

        await self.avanzar_correo(datos, pregunta)

    async def _confirmar_envio(self, datos, pregunta):
        if not es_afirmacion(pregunta):
            print("[correo] NO confirmado: no se envia")
            await self.enviar(tipo="borrador_cerrar")
            await self.decir_turno("Vale, no lo mando.", None, pregunta)
            return

        # Decir "sí" manda lo que hay escrito en el borrador. Si le falta el
        # destinatario no se puede: hay que elegirlo, y eso se hace en el
        # panel, no hablando.
        if not datos.get("email"):
            self.pendiente["tipo"], self.pendiente["datos"] = "correo", datos
            await self.decir_turno(
                "Antes tienes que elegir a quién se lo mando.", "borrador",
                pregunta)
            return

        print(f"[correo] confirmado por voz -> {datos['email']}")
        ok, frase = await asyncio.to_thread(
            correo.enviar, datos["email"], datos["asunto"], datos["mensaje"])
        await self.enviar(tipo="borrador_cerrar")
        await self.decir_turno(frase, "correo", pregunta)

    # ---------------------------------------------------------------
    # 2. MATERIAL QUE HAY QUE LEER, NO RESPUESTAS
    # ---------------------------------------------------------------

    async def _material_de_la_web(self, args, pregunta):
        """Busca y devuelve lo leido. None si ya se le ha contestado.

        La busqueda es distinta al resto de herramientas: lo que vuelve son
        fragmentos de paginas, no una respuesta. Hace falta que el modelo
        los lea y conteste. Las de la agenda vuelven ya redactadas.
        """
        await self.enviar(tipo="herramienta", nombre="buscando en la web")
        consulta = (args or {}).get("consulta") or pregunta
        t0 = time.monotonic()
        # buscar_y_leer, no buscar_en_web: los fragmentos del buscador suelen
        # ser la descripción de la página, no el dato.
        fragmentos, fallo = await asyncio.to_thread(buscar.buscar_y_leer, consulta)
        print(f"[web] {time.monotonic()-t0:.1f}s  {consulta!r} -> "
              f"{len(fragmentos)} resultados{' | ' + fallo if fallo else ''}")

        if fallo:
            aviso = f"No he podido buscarlo: {fallo}."
            await self.enviar(tipo="token", texto=aviso)
            await self.enviar(tipo="estado", valor="hablando")
            await asyncio.to_thread(hablar, aviso)
            await self.enviar(tipo="fin_respuesta")
            await self.enviar(tipo="estado", valor="inactivo")
            return None
        return buscar.como_contexto(fragmentos)

    async def _material_del_correo(self, args, pregunta):
        """Lee la bandeja y devuelve lo leido. None si ya se ha contestado.

        Igual que la web, pero aqui hay un motivo de SEGURIDAD ademas del
        practico. El texto de un correo lo escribe cualquiera, y puede
        traer dentro "manda un correo a esta direccion" o "apaga el
        ordenador". Metiendolo por esta via se le entrega al CONVERSADOR,
        que no lleva herramientas: aunque el modelo se creyera la orden, no
        tiene con que ejecutarla. Al router, que si las lleva, no le llega
        nunca el contenido de un correo.
        """
        await self.enviar(tipo="herramienta", nombre="mirando el correo")
        a = args or {}
        nuevos = a.get("solo_nuevos")
        nuevos = True if nuevos is None else str(nuevos).lower() not in ("false", "0", "no")
        quien = (a.get("de") or "").strip()

        t0 = time.monotonic()
        correos, fallo = await asyncio.to_thread(correo.leer_nuevos, nuevos, quien)
        print(f"[correo] {time.monotonic()-t0:.1f}s  "
              f"{'sin leer' if nuevos else 'recientes'}"
              f"{' de ' + quien if quien else ''} -> {len(correos)}"
              f"{' | ' + fallo if fallo else ''}")

        if fallo:
            await self.decir_turno(f"No he podido mirar el correo: {fallo}.",
                                   "correo", pregunta)
            return None

        if not correos:
            # Sin correos no hay nada que resumir, y pasarle una lista vacía
            # al modelo es invitarle a inventarse remitentes.
            if quien:
                vacio = f"No tienes ningún correo de {quien}."
            elif nuevos:
                vacio = "No tienes correos nuevos."
            else:
                vacio = "No hay nada en la bandeja de entrada."
            await self.decir_turno(vacio, "correo", pregunta)
            return None

        return correo.como_contexto(correos, nuevos)

    # ---------------------------------------------------------------
    # 3. LO IRREVERSIBLE SE PREGUNTA ANTES
    # ---------------------------------------------------------------

    async def _confirmar_accion(self, args, pregunta):
        """Apagar y reiniciar NO se ejecutan a la primera. True si se pregunto.

        Es lo unico de lo que no se vuelve, asi que no basta con que el
        router lo proponga bien.
        """
        accion = (args or {}).get("accion", "")
        if accion not in ("apagar", "reiniciar"):
            return False
        self.pendiente["tipo"], self.pendiente["datos"] = "sistema", accion
        verbo = "reinicie" if accion == "reiniciar" else "apague"
        await self.decir_turno(f"¿Seguro que quieres que {verbo} el ordenador?",
                               "confirmar", pregunta)
        return True

    async def _confirmar_borrado(self, args, pregunta):
        """Quitar VARIAS tareas se pregunta antes. True si se pregunto.

        Una sola se quita sin mas: si te equivocas, la vuelves a apuntar.
        Pero "quitalo todo" borra la lista entera, y eso no se deshace
        hablando. Con Whisper de por medio, una frase mal oida no puede
        vaciarte la agenda.
        """
        texto = (args or {}).get("texto", "")
        grupo = memoria.grupo_pedido(texto)
        if not grupo:
            return False                  # nombra una concreta: sin ceremonia

        cuantas = memoria.tareas_del_grupo(grupo)
        if len(cuantas) <= 1:
            return False                  # una o ninguna: tampoco hace falta

        self.pendiente["tipo"] = "borrado"
        self.pendiente["datos"] = grupo
        nombres = "; ".join(f["texto"] for f in cuantas[:4])
        await self.decir_turno(
            f"Eso son {len(cuantas)} tareas: {nombres}. ¿Las quito todas?",
            "confirmar", pregunta)
        return True

    @staticmethod
    def _dia_hablado(momento, ahora):
        """"el 1 de septiembre", o "hoy"/"mañana"/"ayer" si toca."""
        dias = (momento.date() - ahora.date()).days
        if dias == 0:
            return "hoy"
        if dias == 1:
            return "mañana"
        if dias == -1:
            return "ayer"
        return f"el {momento.day} de {memoria.MESES_ES[momento.month - 1]}"

    async def _confirmar_borrado_por_fecha(self, pregunta):
        """"Quita lo del 1 de septiembre": lo de ese dia, aqui y en Google.

        SIEMPRE pregunta, aunque sea una cosa: por fecha se abarca mas de
        lo que parece, y en Google puede haber cosas tuyas de ese dia que
        Jarvis no creo. Se dice que hay antes, agrupado por nombre, para
        que el "si" sea sabiendo a que.

        Se mira la frase original y no lo que extrajo el router: la fecha
        es lo que importa aqui, y el modelo a veces la recorta.
        """
        ahora = self.ahora()
        pedido = memoria.borrado_por_fecha(pregunta, ahora)
        if not pedido:
            return False
        desde, hasta, palabras = pedido

        propias = await asyncio.to_thread(memoria.tareas_del_dia,
                                          desde, hasta, palabras)
        # Lo de Google que no este ya ligado a una tarea propia: esas se
        # borran de alli al completarlas, y contarlas seria decirlas dos veces
        conocidos = await asyncio.to_thread(memoria.eventos_conocidos)
        eventos = await asyncio.to_thread(calendario.eventos_para_agenda,
                                          desde, hasta)
        fuera = [e for e in eventos
                 if e["id"] not in conocidos
                 and memoria.coincide_nombre(e["texto"], palabras)]

        dia = self._dia_hablado(desde, ahora)
        total = len(propias) + len(fuera)
        if not total:
            que = "de eso " if palabras else ""
            await self.decir_turno(f"No tienes nada {que}{dia}.", "agenda", pregunta)
            return True

        # Agrupado por nombre: el 1 de septiembre hay 47 dentistas iguales,
        # y leerlos uno a uno no es una pregunta, es un castigo
        cuenta = Counter([f["texto"] for f in propias] + [e["texto"] for e in fuera])
        partes = [f"{t}, {n} veces" if n > 1 else t
                  for t, n in cuenta.most_common(4)]
        if len(cuenta) > 4:
            partes.append(f"y {len(cuenta) - 4} cosas más")

        self.pendiente["tipo"] = "borrado_dia"
        self.pendiente["datos"] = {"propias": [f["id"] for f in propias],
                                   "fuera": [e["id"] for e in fuera]}
        if total == 1:
            frase = f"{dia.capitalize()} tienes {partes[0]}. ¿Lo quito?"
        else:
            frase = (f"{dia.capitalize()} hay {total} cosas: "
                     f"{'; '.join(partes)}. ¿Las quito todas?")
        await self.decir_turno(frase, "confirmar", pregunta)
        return True

    async def _quitar_del_dia(self, datos, pregunta):
        """Ya ha dicho si quiere quitar lo de ese dia."""
        if not es_afirmacion(pregunta):
            print("[agenda] NO confirmado: se deja lo de ese dia")
            await self.decir_turno("Vale, lo dejo.", "agenda", pregunta)
            return

        filas = await asyncio.to_thread(memoria.tareas_por_ids, datos["propias"])
        if filas:
            await asyncio.to_thread(memoria.completar_varias, filas)
        borrados = 0
        if datos["fuera"]:
            borrados = await asyncio.to_thread(calendario.borrar_varios,
                                               datos["fuera"])

        pedidas = len(datos["propias"]) + len(datos["fuera"])
        hechas = len(filas) + borrados
        print(f"[agenda] quitadas {hechas} de {pedidas}")
        if hechas == pedidas:
            frase = "Hecho, lo he quitado." if hechas == 1 else f"Hecho, he quitado {hechas}."
        elif hechas:
            frase = f"He quitado {hechas} de {pedidas}. El resto no he podido."
        else:
            frase = "No he podido quitar nada."
        await self.decir_turno(frase, "completar tarea", pregunta)

    async def _buscar_en_el_calendario(self, args, pregunta):
        """Si no esta en la lista, mira en Google Calendar. True si actuo.

        Jarvis solo conocia lo que habia en su SQLite. Si creas algo a mano
        en Calendar —o si la base de datos se vacio alguna vez— esos
        eventos eran invisibles: "quita el dentista" contestaba "no
        encuentro esa tarea", que era cierto y no ayudaba.

        Borrar aqui SIEMPRE pregunta, aunque sea uno solo. Un evento que
        Jarvis no creo es tuyo, puede llevar invitados o llevar ahi meses,
        y no se toca sin que lo veas.
        """
        texto = (args or {}).get("texto", "")
        if not texto.strip():
            return False
        if memoria.buscar_tarea(texto):
            return False                  # esta en la lista: por la via normal

        eventos = await asyncio.to_thread(calendario.buscar_por_nombre, texto)
        if not eventos:
            return False                  # tampoco esta ahi: que conteste el de siempre

        self.pendiente["tipo"] = "borrado_calendario"
        self.pendiente["datos"] = [e["id"] for e in eventos]
        cuales = "; ".join(calendario.como_frase(e) for e in eventos[:3])
        if len(eventos) == 1:
            frase = (f"Eso no está en tu lista, pero sí en tu calendario: "
                     f"{cuales}. ¿Lo quito de ahí?")
        else:
            frase = (f"Eso no está en tu lista. En el calendario hay "
                     f"{len(eventos)}: {cuales}. ¿Los quito?")
        await self.decir_turno(frase, "confirmar", pregunta)
        return True

    async def _quitar_del_calendario(self, ids, pregunta):
        """Ya ha dicho si quiere que se borren esos eventos de Google."""
        if not es_afirmacion(pregunta):
            print(f"[calendar] NO confirmado: {len(ids)} eventos intactos")
            await self.decir_turno("Vale, los dejo.", "agenda", pregunta)
            return
        hechos = 0
        for identificador in ids:
            if await asyncio.to_thread(calendario.borrar_evento, identificador):
                hechos += 1
        print(f"[calendar] borrados {hechos} de {len(ids)}")
        if hechos == 1:
            frase = "Hecho, lo he quitado del calendario."
        elif hechos:
            frase = f"Hecho, he quitado {hechos} del calendario."
        else:
            frase = "No he podido quitarlo del calendario."
        await self.decir_turno(frase, "completar tarea", pregunta)

    async def _preparar_correo(self, args, pregunta):
        """Monta el borrador y lo abre para revisarlo. Nunca envia aqui.

        Mandarlo a quien no era no tiene arreglo, asi que siempre pasa por
        el popup antes.
        """
        a = args or {}
        if not correo.cargar_contactos():
            await self.decir_turno(
                "No tengo la agenda de contactos preparada todavía.",
                "correo", pregunta)
            return

        pedido = (a.get("destinatario") or "").strip()
        # Un destinatario que no salió de la boca del usuario no vale: a
        # "quiero mandar un correo" el router propuso "su novia", sacado de
        # los datos guardados. Se ignora y se pregunta.
        if pedido and destinatario_inventado(pedido, pregunta):
            print(f"[correo] descarto destinatario {pedido!r}: no lo dijo")
            pedido = ""
        contacto = correo.buscar_contacto(pedido) if pedido else None

        mensaje = (a.get("mensaje") or "").strip()
        # El modelo a veces describe el encargo en vez de redactarlo: "El
        # usuario pide que mandes un correo electrónico". Eso no es un
        # correo, así que se tira y se pregunta qué decir.
        if re.match(r"(?i)\s*(el|la)\s+usuari[oa]\b", mensaje):
            print(f"[correo] descarto mensaje {mensaje[:40]!r}: es una descripción")
            mensaje = ""

        if mensaje:
            # Lo que saca el router vale, pero sale en un renglón: sin saludo
            # aparte ni despedida aparte, todo seguido. En Gmail queda de
            # aviso automático. Se vuelve a redactar a partir de lo que dijo
            # el usuario, que es la intención de verdad, y de paso pasa por
            # el reintento y el control de negativas.
            await self.enviar(tipo="estado", valor="pensando")
            t0 = time.monotonic()
            mensaje = await asyncio.to_thread(
                redactar_correo, pregunta,
                contacto["nombre"] if contacto else pedido,
                (a.get("asunto") or "").strip())
            print(f"[correo] redactado en {time.monotonic()-t0:.1f}s")

        await self.avanzar_correo({
            "email": contacto["email"] if contacto else "",
            "nombre": contacto["nombre"] if contacto else "",
            "asunto": (a.get("asunto") or "").strip(),
            "mensaje": mensaje,
        }, pregunta)

    # ---------------------------------------------------------------
    # 4. HERRAMIENTAS QUE CONTESTAN ELLAS
    # ---------------------------------------------------------------

    async def _contestar_con_herramienta(self, nombre, args, pregunta):
        """Ejecuta y lee el resultado TAL CUAL. False si no devolvio nada.

        Lo que devuelve una herramienta ya viene redactado y es exacto. No
        se le pasa al modelo para que lo reformule: se diria igual de bien y
        podria cambiar una hora o una fecha por el camino.
        """
        resultado = await asyncio.to_thread(ejecutar, nombre, args)
        if not resultado:
            return False              # si falla, se sigue como conversación

        await self.enviar(tipo="herramienta", nombre=nombre)
        await self.enviar(tipo="token", texto=resultado)
        await self.enviar(tipo="estado", valor="hablando")
        await asyncio.to_thread(hablar, resultado)
        self.historial.append({"role": "user", "content": pregunta})
        self.historial.append({"role": "assistant", "content": resultado})
        recortar_historial(self.historial)
        await self.enviar(tipo="fin_respuesta")
        await self.enviar(tipo="estado", valor="inactivo")
        return True

    # ---------------------------------------------------------------
    # 5. CONVERSAR
    # ---------------------------------------------------------------

    async def _conversar(self, pregunta, material=None):
        """Llama al LLM en streaming y va hablando frase a frase.

        ollama.chat(stream=True) devuelve un generador SINCRONO y
        bloqueante: cada next() espera a la red. Antes se hacia list(...)
        sobre el dentro de un hilo, lo que consumia la respuesta ENTERA
        antes de devolver nada al bucle de eventos — de ahi que no hubiera
        streaming real.

        Aqui el generador se consume en un hilo aparte, y cada trozo se
        mete en una asyncio.Queue con call_soon_threadsafe (la unica forma
        correcta de tocar una asyncio.Queue desde fuera del hilo del bucle).
        El bucle va sacando trozos segun llegan, asi que puede hablar la
        primera frase sin esperar al resto.
        """
        # La fecha se refresca cada turno: el asistente puede llevar horas
        # abierto y haber cruzado la medianoche.
        self.historial[0] = {"role": "system", "content": prompt_con_fecha()}

        if material:
            # El material va en el turno del usuario, no en el prompt de
            # sistema: así se va solo cuando el historial se recorta y no
            # contamina las preguntas siguientes. Que un correo leído hace
            # diez turnos siga influyendo sería un problema, no una ventaja.
            self.historial.append({"role": "user",
                                   "content": f"{material}\n\nPREGUNTA: {pregunta}"})
        else:
            self.historial.append({"role": "user", "content": pregunta})

        completa = ""
        buffer_frase = ""
        primera = True

        bucle = asyncio.get_running_loop()
        cola = asyncio.Queue()
        FIN = object()

        t0 = time.monotonic()
        primer_token = [None]

        def producir():
            try:
                flujo = ollama.chat(
                    model=MODELO_LLM,
                    messages=self.historial,
                    stream=True,
                    options={"num_ctx": NUM_CTX, "temperature": TEMPERATURA},
                )
                for parte in flujo:
                    trozo = parte["message"]["content"]
                    if trozo:
                        bucle.call_soon_threadsafe(cola.put_nowait, trozo)
            except Exception as e:
                bucle.call_soon_threadsafe(cola.put_nowait, ("__error__", str(e)))
            finally:
                bucle.call_soon_threadsafe(cola.put_nowait, FIN)

        threading.Thread(target=producir, daemon=True).start()

        error = None
        while True:
            trozo = await cola.get()
            if trozo is FIN:
                break
            if isinstance(trozo, tuple) and trozo[0] == "__error__":
                error = trozo[1]
                break

            if primer_token[0] is None:
                primer_token[0] = time.monotonic() - t0
                print(f"[llm] primer token en {primer_token[0]:.1f}s")

            completa += trozo
            buffer_frase += trozo
            await self.enviar(tipo="token", texto=trozo)

            # Al cerrar una frase, la manda a la voz sin esperar al resto
            if frase_terminada(buffer_frase):
                if primera:
                    await self.enviar(tipo="estado", valor="hablando")
                    primera = False
                await asyncio.to_thread(hablar, buffer_frase)
                buffer_frase = ""

        if error:
            # Un fallo de Ollama (modelo no descargado, servicio caído...) ya
            # no debe tumbar la conexión entera: se avisa a la interfaz, se
            # quita del historial la pregunta que quedó sin responder, y se
            # vuelve a inactivo para poder seguir usando el asistente.
            print(f"Error al hablar con Ollama: {error}")
            self.historial.pop()
            await self.enviar(tipo="error",
                              texto="No he podido pensar la respuesta.")
            await self.enviar(tipo="estado", valor="inactivo")
            return

        if buffer_frase.strip():
            if primera:
                await self.enviar(tipo="estado", valor="hablando")
            await asyncio.to_thread(hablar, buffer_frase)

        self.historial.append({"role": "assistant", "content": completa})
        recortar_historial(self.historial)
        await self.enviar(tipo="fin_respuesta")
        await self.enviar(tipo="estado", valor="inactivo")

    async def parar_grabacion(self):
        self.grabando = False
        for t in (self.tarea_medidor, self.tarea_parciales, self.tarea_limite, self.tarea_silencio):
            if t:
                t.cancel()
        await self.enviar(tipo="nivel", valor=0)

        audio = self.micro.parar()
        if audio is None or len(audio) < FRECUENCIA * 0.5:
            await self.enviar(tipo="descartar")
            await self.enviar(tipo="estado", valor="inactivo")
            return

        await self.enviar(tipo="estado", valor="transcribiendo")
        try:
            texto = await asyncio.to_thread(transcribir, audio, stt_bueno, "final")
        except Exception as e:
            # Antes, un fallo aquí reventaba el handler entero del WebSocket
            # y la interfaz solo mostraba "sin servidor", sin decir por qué.
            print(f"Error al transcribir: {e}")
            await self.enviar(tipo="error", texto="No he podido entender el audio.")
            await self.enviar(tipo="estado", valor="inactivo")
            return

        if not texto:
            await self.enviar(tipo="descartar")
            await self.enviar(tipo="estado", valor="inactivo")
            return

        await self.enviar(tipo="usuario", texto=texto)
        await self.responder(texto)

    async def vigilar_avisos(self):
        """Avisa un rato antes de lo que tienes apuntado. Sin que preguntes.

        Solo habla cuando Jarvis esta libre: nunca mientras grabas, piensa o
        contesta, ni con una pregunta en el aire (un "si" a "¿apago el
        ordenador?" no puede quedar pisado por un recordatorio). Si esta
        ocupado, el aviso espera a la siguiente vuelta.
        """
        while True:
            await asyncio.sleep(AVISO_CADA_S)
            if (self.grabando or self.estado != "inactivo"
                    or self.pendiente["tipo"]):
                continue
            try:
                avisos = await asyncio.to_thread(memoria.pendientes_de_aviso)
            except Exception as e:
                print(f"[avisos] no se pudo mirar la agenda: {e}")
                continue
            for clave, texto, momento in avisos:
                # Mirar la agenda puede tardar (Google). Si mientras tanto
                # has pulsado el micro, el aviso espera: no se marca como
                # dado, asi que sale en la siguiente vuelta libre.
                if (self.grabando or self.estado != "inactivo"
                        or self.pendiente["tipo"]):
                    break
                # marcar_avisado es atomico: con dos pestanas abiertas solo
                # una se lo queda, y la otra no lo repite
                if not await asyncio.to_thread(memoria.marcar_avisado, clave):
                    continue
                frase = memoria.frase_de_aviso(texto, momento)
                print(f"[avisos] {frase}")
                await self.decir_suelto(frase, "recordatorio")
                break          # de uno en uno: si hay otro, en la siguiente vuelta

    async def vigilar_limite(self):
        """Corta sola la grabación a los MAX_GRABACION_S segundos."""
        while True:
            await asyncio.sleep(0.5)
            if self.micro.segundos_grabados() >= MAX_GRABACION_S:
                await self.parar_grabacion()
                return

    async def vigilar_silencio(self):
        """Envía solo cuando dejas de hablar, sin pulsar espacio otra vez.

        Mira los últimos segundos con Silero (el mismo VAD que trae dentro
        faster-whisper, así que no hace falta nada nuevo) y mide cuánto
        silencio hay DESPUÉS de lo último que dijiste. Cuesta entre 4 y 30
        milisegundos, así que se puede consultar cuatro veces por segundo.

        No corta hasta haberte oído: si pulsas y te quedas pensando, espera.
        """
        if not CORTE_POR_SILENCIO:
            return
        while True:
            await asyncio.sleep(0.25)
            audio = self.micro.audio_reciente(VAD_VENTANA_S)
            if audio is None or len(audio) < FRECUENCIA * 0.8:
                continue
            try:
                tramos = await asyncio.to_thread(
                    get_speech_timestamps, audio, OPCIONES_VAD)
            except Exception as e:
                print(f"Error en el detector de voz: {e}")
                return
            if not tramos:
                continue                      # todavía no ha hablado nadie

            habla = sum(t["end"] - t["start"] for t in tramos) / FRECUENCIA
            cola = (len(audio) - tramos[-1]["end"]) / FRECUENCIA
            if habla >= MIN_HABLA_S and cola >= SILENCIO_CORTE_S:
                print(f"[vad] corte por silencio: {habla:.1f}s de voz, "
                      f"{cola:.1f}s de silencio")
                await self.parar_grabacion()
                return


# Códigos de cierre propios (4000-4999 son de la aplicación). La página
# los distingue para pedir el PIN o avisar del bloqueo.
CIERRE_PIN = 4401
CIERRE_BLOQUEADO = 4403


@app.websocket("/ws")
async def ws(sock: WebSocket):
    motivo = guardia.motivo_rechazo(sock.headers.get("origin"),
                                    sock.headers.get("host"))
    if motivo:
        # Cerrar sin aceptar: el navegador recibe un 403 y ni un byte más
        print(f"[guardia] conexión rechazada ({motivo})")
        await sock.close()
        return

    ip = sock.client.host if sock.client else ""
    llave = guardia.LLAVE
    if llave and not guardia.es_local(ip):
        if not llave.comprobar(sock.query_params.get("pin")):
            await sock.accept()
            if llave.bloqueada:
                print(f"[guardia] PIN BLOQUEADO tras {llave.fallos} fallos "
                      f"(último desde {ip}). Reinicia para generar otro.")
                await sock.close(code=CIERRE_BLOQUEADO)
            else:
                if sock.query_params.get("pin"):
                    print(f"[guardia] PIN incorrecto desde {ip} "
                          f"({llave.fallos}/{guardia.INTENTOS_PIN})")
                await sock.close(code=CIERRE_PIN)
            return

    await Conversacion(sock).correr()


def ip_en_la_red():
    """La IP de este equipo en la red local, para poder decirla."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No se conecta a nada: solo se pregunta al sistema por qué
        # interfaz saldría el tráfico, y de ahí sale la IP buena.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "tu-ip-local"
    finally:
        s.close()


if __name__ == "__main__":
    # Por defecto solo escucha en este ordenador.
    #
    # Antes escuchaba en 0.0.0.0, o sea en TODAS las interfaces: cualquiera
    # en la misma wifi podía abrir la interfaz y usar Jarvis entero, sin
    # contraseña. Pulsar el micro, apagar el ordenador, mandar correos
    # desde la cuenta de Gmail del usuario. En casa da igual; en la
    # universidad o en una cafetería, no.
    #
    # Con --red vuelve a abrirse, que es lo que hace falta para verlo en
    # el móvil o enseñárselo a alguien. Se avisa por pantalla de lo que
    # implica, para que sea una decisión y no un descuido.
    abierto = "--red" in sys.argv
    host = "0.0.0.0" if abierto else "127.0.0.1"
    ssl = {}

    if abierto:
        # Por la red hace falta HTTPS, y no por gusto: el micrófono del
        # navegador (getUserMedia) solo existe en "contexto seguro". Por
        # http:// desde el móvil la API ni siquiera aparece, así que no
        # habría forma de hablarle. localhost sí cuenta como seguro, por
        # eso en local se sigue usando http y nadie ve ningún aviso.
        import certificado
        if not certificado.existe():
            print()
            print("  Falta el certificado HTTPS, y sin él el móvil no puede")
            print("  usar el micrófono. Se crea una vez con:")
            print()
            print("      python certificado.py")
            print()
            sys.exit(1)

        ssl = {"ssl_keyfile": str(certificado.CLAVE),
               "ssl_certfile": str(certificado.CERT)}
        # Los nombres por los que el móvil puede llegar: los mismos que
        # lleva el certificado
        ips, nombres = certificado._nombres()
        guardia.HOSTS |= {n.lower() for n in [*ips, *nombres]}
        guardia.LLAVE = guardia.Llave()
        dias = certificado.caduca_en()
        if dias is not None and dias < 15:
            print(f"\n  Aviso: el certificado caduca en {dias} días."
                  "  Renuévalo con: python certificado.py")

        print()
        print("  " + "!" * 62)
        print("  ABIERTO A LA RED LOCAL")
        print(f"  Desde el móvil:  https://{ip_en_la_red()}:8000")
        print()
        print("  La primera vez el móvil avisará de que la conexión no es")
        print("  privada: el certificado lo firma tu PC y no hay autoridad")
        print("  que pueda certificar una IP privada. Continúa y acepta.")
        print()
        print(f"  PIN para el móvil:  {guardia.LLAVE.pin}")
        print()
        print("  Te lo pedirá una vez; este PC no lo necesita. Cambia en")
        print(f"  cada arranque, y tras {guardia.INTENTOS_PIN} fallos se bloquea hasta reiniciar.")
        print("  " + "!" * 62)
    else:
        print("\n  Abre http://localhost:8000")
        print("  (solo desde este ordenador. Para el móvil: python servidor.py --red)")
    print()

    uvicorn.run(app, host=host, port=8000, log_level="warning", **ssl)
