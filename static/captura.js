/* Captura del microfono en el navegador, para poder hablarle desde el movil.
 *
 * POR QUE UN AudioWorklet Y NO MediaRecorder
 *
 * MediaRecorder da webm/opus comprimido: habria que decodificarlo en el
 * servidor antes de dárselo a Whisper, que quiere PCM crudo. Un worklet
 * entrega las muestras tal cual, sin pasar por ningun códec.
 *
 * Y corre en el hilo de audio, no en el principal: si la interfaz se
 * atasca pintando el anillo, la grabacion no pierde ni una muestra.
 *
 * POR QUE SE REMUESTREA AQUI
 *
 * Whisper trabaja a 16 kHz. Se le pide al AudioContext que abra ya a esa
 * frecuencia y casi siempre obedece, pero Safari en iOS la ignora y abre
 * a 48 kHz. Si eso pasa, se decima aqui promediando muestras: el
 * promedio hace de filtro y evita el aliasing que daria coger una de
 * cada tres a pelo.
 */

const DESTINO = 16000;
// Trozos de ~64 ms. Mas pequenos = mas mensajes por segundo y mas
// sobrecarga por wifi; mas grandes = el anillo reacciona a tirones.
const MUESTRAS_POR_ENVIO = 1024;

class Captura extends AudioWorkletProcessor {
  constructor() {
    super();
    this.acumulado = [];
    this.pendientes = 0;
    // sampleRate es global dentro del worklet: la real del contexto
    this.factor = sampleRate / DESTINO;
  }

  /* Baja de sampleRate a 16 kHz promediando cada grupo de muestras.
     Si ya viene a 16 kHz el factor es 1 y no toca nada. */
  aDestino(entrada) {
    if (Math.abs(this.factor - 1) < 0.01) return entrada;
    const salida = new Float32Array(Math.floor(entrada.length / this.factor));
    for (let i = 0; i < salida.length; i++) {
      const desde = Math.floor(i * this.factor);
      const hasta = Math.min(entrada.length, Math.floor((i + 1) * this.factor));
      let suma = 0;
      for (let j = desde; j < hasta; j++) suma += entrada[j];
      salida[i] = hasta > desde ? suma / (hasta - desde) : 0;
    }
    return salida;
  }

  process(entradas) {
    const canal = entradas[0] && entradas[0][0];
    if (!canal) return true;          // true = seguir vivo aunque no haya audio

    const trozo = this.aDestino(canal);
    this.acumulado.push(trozo);
    this.pendientes += trozo.length;

    if (this.pendientes < MUESTRAS_POR_ENVIO) return true;

    // Junta lo acumulado y lo pasa a int16: la mitad de bytes que float32,
    // y por wifi eso son 32 KB/s en vez de 64.
    const junto = new Float32Array(this.pendientes);
    let n = 0;
    for (const t of this.acumulado) { junto.set(t, n); n += t.length; }
    this.acumulado = [];
    this.pendientes = 0;

    const pcm = new Int16Array(junto.length);
    for (let i = 0; i < junto.length; i++) {
      const v = Math.max(-1, Math.min(1, junto[i]));
      pcm[i] = v * 32767;
    }
    // transferible: se pasa el buffer sin copiarlo
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}

registerProcessor('captura', Captura);
