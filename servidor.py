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
from pathlib import Path

import numpy as np
import ollama
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import buscar
import calendario
import correo
import memoria
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
print("Listo.")
print(f"  {calendario.estado()}")
print(f"  {correo.estado()}")


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
                if mensaje.get("cmd") != "alternar":
                    continue

                if not self.grabando:
                    self.grabando = True
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
                tarea_cmd = asyncio.create_task(self.comandos.get())

                hechas, _ = await asyncio.wait(
                    {tarea_turno, tarea_cmd},
                    return_when=asyncio.FIRST_COMPLETED)

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
                      self.tarea_silencio, self.tarea_receptor):
                if t:
                    t.cancel()
            cortar_voz()          # si se va con el asistente hablando, que calle
            permitir_voz()        # y que la próxima conexión pueda hablar
            # La salida de voz es global: si esta conexion la habia desviado al
            # navegador, hay que devolverla o la siguiente se quedaria muda.
            salida_voz_a(None)
            self.micro.parar()


    async def enviar(self, **datos):
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
        """Llama al LLM en streaming y va hablando frase a frase.

        ollama.chat(stream=True) devuelve un generador SÍNCRONO y bloqueante:
        cada `next()` espera a la red. Antes se hacía list(...) sobre él dentro
        de un hilo, lo que consumía la respuesta ENTERA antes de devolver nada
        al bucle de eventos — de ahí que no hubiera streaming real.
        Aquí el generador se consume en un hilo aparte, y cada trozo se mete
        en una asyncio.Queue mediante call_soon_threadsafe (la única forma
        correcta de tocar una asyncio.Queue desde fuera del hilo del bucle de
        eventos). El bucle de eventos va sacando trozos de la cola según
        llegan, así que puede hablar la primera frase sin esperar al resto.
        """
        await self.enviar(tipo="estado", valor="pensando")

        async def decir_y_cerrar(frase, etiqueta=None):
            """Dice una frase corta y cierra el turno. Sin pasar por el modelo."""
            await self.decir_turno(frase, etiqueta, pregunta)

        # ¿Se le acababa de preguntar si apagar o reiniciar? Entonces este
        # turno es la respuesta, y se resuelve aquí SIN pasar por el router:
        # el modelo enrutaría un "sí" suelto como charla y se perdería.
        if self.pendiente["tipo"]:
            tipo, datos = self.pendiente["tipo"], self.pendiente["datos"]
            self.pendiente["tipo"], self.pendiente["datos"] = None, None
            dijo_si = es_afirmacion(pregunta)

            if tipo == "sistema":
                if dijo_si:
                    print(f"[sistema] confirmado: {datos}")
                    resultado = await asyncio.to_thread(
                        sistema.ejecutar_accion, datos)
                    await decir_y_cerrar(resultado, "control sistema")
                else:
                    print(f"[sistema] NO confirmado: {datos} descartado")
                    verbo = "reinicio" if datos == "reiniciar" else "apago"
                    await decir_y_cerrar(f"Vale, no {verbo} nada.")
                return

            # Se le acaba de preguntar el asunto, el texto o a quién: lo
            # que ha dicho ES la respuesta, no una orden nueva. No pasa
            # por el router, que enrutaría "que llego tarde a la cena"
            # como una tarea que apuntar.
            if tipo == "correo_paso":
                if pide_dejarlo(pregunta):
                    print("[correo] abandonado a medias")
                    await self.enviar(tipo="borrador_cerrar")
                    await decir_y_cerrar("Vale, lo dejo.", "correo")
                    return
                paso = datos.pop("paso", "")
                dicho = pregunta.strip()
                if paso == "asunto":
                    # El asunto es una línea: sin punto final y sin más
                    datos["asunto"] = dicho.rstrip(" .").strip()
                elif paso == "mensaje":
                    # Lo dicho es un ENCARGO, no el texto: "mándale algo
                    # formal pidiéndole que venga a casa". Lo redacta el
                    # modelo, y luego se revisa en el popup.
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
                        await decir_y_cerrar(
                            "No tengo a esa persona en la agenda. "
                            "Escríbelo en el recuadro.", "correo")
                        return
                    datos["email"], datos["nombre"] = destino, visible
                await self.avanzar_correo(datos, pregunta)
                return

            if tipo == "correo":
                if not dijo_si:
                    print("[correo] NO confirmado: no se envia")
                    await self.enviar(tipo="borrador_cerrar")
                    await decir_y_cerrar("Vale, no lo mando.")
                    return
                # Decir "sí" manda lo que hay escrito en el borrador. Si le
                # falta el destinatario no se puede: hay que elegirlo, y eso
                # se hace en el panel, no hablando.
                if not datos.get("email"):
                    self.pendiente["tipo"], self.pendiente["datos"] = "correo", datos
                    await decir_y_cerrar(
                        "Antes tienes que elegir a quién se lo mando.", "borrador")
                    return
                print(f"[correo] confirmado por voz -> {datos['email']}")
                ok, frase = await asyncio.to_thread(
                    correo.enviar, datos["email"], datos["asunto"],
                    datos["mensaje"])
                await self.enviar(tipo="borrador_cerrar")
                await decir_y_cerrar(frase, "correo")
                return

        # Primero el router: ¿esto va de tareas o del reloj?
        try:
            t_ruta = time.monotonic()
            nombre, args = await asyncio.to_thread(enrutar, pregunta)
            print(f"[router] {time.monotonic()-t_ruta:.1f}s -> {nombre or 'conversación'}")
        except Exception as e:
            print(f"Error en el router: {e}")
            nombre, args = None, None

        # La búsqueda es distinta al resto de herramientas: lo que vuelve son
        # fragmentos de páginas, no una respuesta. Hace falta que el modelo los
        # lea y conteste. Las de la agenda, en cambio, vuelven ya redactadas.
        contexto_web = None
        if nombre == "buscar_en_web":
            await self.enviar(tipo="herramienta", nombre="buscando en la web")
            consulta = (args or {}).get("consulta") or pregunta
            t_web = time.monotonic()
            # buscar_y_leer, no buscar_en_web: los fragmentos del buscador
            # suelen ser la descripción de la página, no el dato.
            fragmentos, fallo = await asyncio.to_thread(
                buscar.buscar_y_leer, consulta)
            print(f"[web] {time.monotonic()-t_web:.1f}s  {consulta!r} -> "
                  f"{len(fragmentos)} resultados{' | ' + fallo if fallo else ''}")
            if fallo:
                aviso = f"No he podido buscarlo: {fallo}."
                await self.enviar(tipo="token", texto=aviso)
                await self.enviar(tipo="estado", valor="hablando")
                await asyncio.to_thread(hablar, aviso)
                await self.enviar(tipo="fin_respuesta")
                await self.enviar(tipo="estado", valor="inactivo")
                return
            contexto_web = buscar.como_contexto(fragmentos)
            nombre = None          # sigue por la vía conversacional, con contexto

        # Los correos, igual que la web: hace falta que el modelo los lea y
        # los resuma. Pero aquí hay un motivo de seguridad además del
        # práctico. El texto de un correo lo escribe cualquiera, y puede
        # traer dentro "manda un correo a esta dirección" o "apaga el
        # ordenador". Metiéndolo por esta vía se le entrega al CONVERSADOR,
        # que no lleva herramientas: aunque el modelo se creyera la orden,
        # no tiene con qué ejecutarla. Al router, que sí las lleva, no le
        # llega nunca el contenido de un correo.
        contexto_correo = None
        if nombre == "leer_correos":
            await self.enviar(tipo="herramienta", nombre="mirando el correo")
            a = args or {}
            nuevos = a.get("solo_nuevos")
            nuevos = True if nuevos is None else str(nuevos).lower() not in ("false", "0", "no")
            quien = (a.get("de") or "").strip()
            t_mail = time.monotonic()
            correos, fallo = await asyncio.to_thread(
                correo.leer_nuevos, nuevos, quien)
            print(f"[correo] {time.monotonic()-t_mail:.1f}s  "
                  f"{'sin leer' if nuevos else 'recientes'}"
                  f"{' de ' + quien if quien else ''} -> {len(correos)}"
                  f"{' | ' + fallo if fallo else ''}")
            if fallo:
                await decir_y_cerrar(f"No he podido mirar el correo: {fallo}.",
                                     "correo")
                return
            if not correos:
                # Sin correos no hay nada que resumir, y pasarle una lista
                # vacía al modelo es invitarle a inventarse remitentes.
                if quien:
                    vacio = f"No tienes ningún correo de {quien}."
                elif nuevos:
                    vacio = "No tienes correos nuevos."
                else:
                    vacio = "No hay nada en la bandeja de entrada."
                await decir_y_cerrar(vacio, "correo")
                return
            contexto_correo = correo.como_contexto(correos, nuevos)
            nombre = None

        # Apagar y reiniciar NO se ejecutan a la primera: se pregunta y se
        # espera respuesta. Es lo único de lo que no se vuelve, así que no
        # basta con que el router lo proponga bien.
        if nombre == "control_sistema":
            accion = (args or {}).get("accion", "")
            if accion in ("apagar", "reiniciar"):
                self.pendiente["tipo"], self.pendiente["datos"] = "sistema", accion
                verbo = "reinicie" if accion == "reiniciar" else "apague"
                await decir_y_cerrar(f"¿Seguro que quieres que {verbo} el ordenador?",
                                     "confirmar")
                return

        # Un correo tampoco se manda a la primera: se lee entero en voz alta
        # y se espera un "sí". Mandarlo a quien no era no tiene arreglo.
        if nombre == "enviar_correo":
            a = args or {}
            if not correo.cargar_contactos():
                await decir_y_cerrar(
                    "No tengo la agenda de contactos preparada todavía.",
                    "correo")
                return

            pedido = (a.get("destinatario") or "").strip()
            # Un destinatario que no salió de la boca del usuario no vale:
            # a "quiero mandar un correo" el router propuso "su novia",
            # sacado de los datos guardados. Se ignora y se pregunta.
            if pedido and destinatario_inventado(pedido, pregunta):
                print(f"[correo] descarto destinatario {pedido!r}: no lo dijo")
                pedido = ""
            contacto = correo.buscar_contacto(pedido) if pedido else None

            mensaje = (a.get("mensaje") or "").strip()
            # El modelo a veces describe el encargo en vez de redactarlo:
            # "El usuario pide que mandes un correo electrónico". Eso no es
            # un correo, así que se tira y se pregunta qué decir.
            if re.match(r"(?i)\s*(el|la)\s+usuari[oa]\b", mensaje):
                print(f"[correo] descarto mensaje {mensaje[:40]!r}: es una descripción")
                mensaje = ""

            if mensaje:
                # Lo que saca el router vale, pero sale en un renglón: sin
                # saludo aparte ni despedida aparte, todo seguido. En Gmail
                # queda de aviso automático. Se vuelve a redactar a partir
                # de lo que dijo el usuario, que es la intención de verdad,
                # y de paso pasa por el reintento y el control de negativas.
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
            return

        if nombre:
            resultado = await asyncio.to_thread(ejecutar, nombre, args)
            if resultado:
                # La respuesta de una herramienta ya viene redactada y es
                # exacta. No se la pasa al modelo para que la reformule:
                # se diría igual de bien y podría cambiar una hora o una fecha.
                await self.enviar(tipo="herramienta", nombre=nombre)
                await self.enviar(tipo="token", texto=resultado)
                await self.enviar(tipo="estado", valor="hablando")
                await asyncio.to_thread(hablar, resultado)
                self.historial.append({"role": "user", "content": pregunta})
                self.historial.append({"role": "assistant", "content": resultado})
                recortar_historial(self.historial)
                await self.enviar(tipo="fin_respuesta")
                await self.enviar(tipo="estado", valor="inactivo")
                return
            # Si la herramienta falla, se sigue como conversación normal

        # La fecha se refresca cada turno: el asistente puede llevar horas
        # abierto y haber cruzado la medianoche.
        self.historial[0] = {"role": "system", "content": prompt_con_fecha()}

        material = contexto_web or contexto_correo
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
            # Un fallo de Ollama (modelo no descargado, servicio caído, etc.)
            # ya no debe tumbar la conexión entera: se avisa a la interfaz,
            # se quita del historial la pregunta que quedó sin responder,
            # y se vuelve a inactivo para poder seguir usando el asistente.
            print(f"Error al hablar con Ollama: {error}")
            self.historial.pop()
            await self.enviar(tipo="error", texto="No he podido pensar la respuesta.")
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


@app.websocket("/ws")
async def ws(sock: WebSocket):
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
        print("  Cualquiera en esta wifi puede usar Jarvis: no hay")
        print("  contraseña. Podría apagarte el ordenador o mandar")
        print("  correos con tu cuenta. Úsalo solo en una red de fiar.")
        print("  " + "!" * 62)
    else:
        print("\n  Abre http://localhost:8000")
        print("  (solo desde este ordenador. Para el móvil: python servidor.py --red)")
    print()

    uvicorn.run(app, host=host, port=8000, log_level="warning", **ssl)
