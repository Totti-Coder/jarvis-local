/**
 * Núcleo 3D de Jarvis.
 *
 * Dibuja un reactor holográfico que reacciona a lo que pasa en el servidor:
 * el espectro real del micrófono mientras escuchas, un barrido mientras
 * piensa, y ondas expansivas mientras habla.
 *
 * Lee el estado de window.JARVIS, que rellena el script principal:
 *   nivel  -> volumen 0..1
 *   bandas -> 32 bandas de frecuencia 0..1
 *   estado -> inactivo | escuchando | transcribiendo | pensando | hablando
 */

import * as THREE from './three.module.js';

const J = window.JARVIS;
const caja = document.getElementById('escena');

// ---------------------------------------------------------------
// PALETA
// Los mismos colores que usa el resto de la interfaz, para que el 3D
// no parezca pegado con cola.
// ---------------------------------------------------------------

const CIAN = new THREE.Color(0x56d9e8);
const AMBAR = new THREE.Color(0xe6a44b);
const colorActual = CIAN.clone();

// ---------------------------------------------------------------
// ESCENA
// ---------------------------------------------------------------

const escena = new THREE.Scene();
const camara = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
camara.position.set(0, 2.6, 11);
camara.lookAt(0, 0, 0);

const render = new THREE.WebGLRenderer({antialias: true, alpha: true});
render.setPixelRatio(Math.min(devicePixelRatio, 2));
caja.appendChild(render.domElement);

function medir() {
  const l = caja.clientWidth;
  if (!l) return;              // aún sin layout: ya volverá el observador
  render.setSize(l, l, false);
  camara.aspect = 1;
  camara.updateProjectionMatrix();
}

// ResizeObserver y no el evento 'resize' de la ventana: si la página carga
// con el contenedor todavía a cero (pestaña en segundo plano, o antes de
// que el navegador calcule el layout), el lienzo se quedaba con un tamaño
// fijo y nunca se corregía porque la ventana no cambiaba de tamaño.
new ResizeObserver(medir).observe(caja);
medir();

// Todo cuelga de aquí, así se inclina el conjunto entero de una vez
const nucleo = new THREE.Group();
escena.add(nucleo);

// ---------------------------------------------------------------
// ANILLOS
// Tres aros en ejes distintos, como el cardán de un giroscopio. Es lo
// que da sensación de volumen: en 2D no se puede fingir.
// ---------------------------------------------------------------

const anillos = [];
const GEOM_ANILLO = [
  {r: 3.4, grosor: 0.018, seg: 160, giro: [0.0, 0.0, 0.0], vel: 0.10},
  {r: 2.9, grosor: 0.012, seg: 140, giro: [Math.PI / 2.4, 0.3, 0.0], vel: -0.16},
  {r: 2.4, grosor: 0.010, seg: 120, giro: [-Math.PI / 3, -0.5, 0.2], vel: 0.22},
];

for (const cfg of GEOM_ANILLO) {
  const malla = new THREE.Mesh(
    new THREE.TorusGeometry(cfg.r, cfg.grosor, 8, cfg.seg),
    new THREE.MeshBasicMaterial({
      color: colorActual, transparent: true, opacity: 0.55,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
  malla.rotation.set(...cfg.giro);
  malla.userData.vel = cfg.vel;
  nucleo.add(malla);
  anillos.push(malla);
}

/* Arcos sueltos sobre el aro exterior: detalle de HUD, puro adorno */
const arcos = [];
for (let i = 0; i < 4; i++) {
  const arco = new THREE.Mesh(
    new THREE.TorusGeometry(3.75, 0.022, 8, 40, Math.PI / 7),
    new THREE.MeshBasicMaterial({
      color: colorActual, transparent: true, opacity: 0.8,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
  arco.rotation.z = (i / 4) * Math.PI * 2;
  nucleo.add(arco);
  arcos.push(arco);
}

// ---------------------------------------------------------------
// BARRAS DEL ESPECTRO
// 64 barras en círculo. Las 32 bandas que manda el servidor se reflejan
// en espejo: simétrico se lee mucho mejor que ruido asimétrico.
// ---------------------------------------------------------------

const BARRAS = 64;
const RADIO_BARRAS = 1.95;
const barras = [];

const geomBarra = new THREE.BoxGeometry(0.055, 1, 0.055);
geomBarra.translate(0, 0.5, 0);   // crece hacia fuera, no desde el centro

for (let i = 0; i < BARRAS; i++) {
  const a = (i / BARRAS) * Math.PI * 2;
  const b = new THREE.Mesh(geomBarra, new THREE.MeshBasicMaterial({
    color: colorActual, transparent: true, opacity: 0.9,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  b.position.set(Math.cos(a) * RADIO_BARRAS, 0, Math.sin(a) * RADIO_BARRAS);
  b.rotation.z = -a + Math.PI / 2;   // tumbadas, apuntando hacia fuera
  b.rotation.order = 'ZYX';
  b.lookAt(0, 0, 0);
  b.rotateX(-Math.PI / 2);
  b.userData.alto = 0;
  nucleo.add(b);
  barras.push(b);
}

// ---------------------------------------------------------------
// CORAZÓN
// ---------------------------------------------------------------

const corazon = new THREE.Mesh(
  new THREE.IcosahedronGeometry(0.85, 1),
  new THREE.MeshBasicMaterial({
    color: colorActual, wireframe: true,
    transparent: true, opacity: 0.85,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
nucleo.add(corazon);

const brillo = new THREE.Mesh(
  new THREE.SphereGeometry(0.42, 24, 24),
  new THREE.MeshBasicMaterial({
    color: 0xffffff, transparent: true, opacity: 0.9,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
nucleo.add(brillo);

/* Halo: una esfera grande y muy tenue que simula el resplandor.
   Un bloom de verdad necesitaría EffectComposer y más ficheros. */
const halo = new THREE.Mesh(
  new THREE.SphereGeometry(1.5, 24, 24),
  new THREE.MeshBasicMaterial({
    color: colorActual, transparent: true, opacity: 0.07,
    blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.BackSide,
  }));
nucleo.add(halo);

// ---------------------------------------------------------------
// PARTÍCULAS
// Una nube esférica que respira con el volumen. Da profundidad sin coste.
// ---------------------------------------------------------------

const NUM_PART = 700;
const posBase = new Float32Array(NUM_PART * 3);
for (let i = 0; i < NUM_PART; i++) {
  // distribución uniforme sobre la esfera (si no, se apelotonan en los polos)
  const u = Math.random() * 2 - 1;
  const th = Math.random() * Math.PI * 2;
  const r = 3.9 + Math.random() * 1.7;
  const s = Math.sqrt(1 - u * u);
  posBase[i * 3] = Math.cos(th) * s * r;
  posBase[i * 3 + 1] = u * r * 0.65;
  posBase[i * 3 + 2] = Math.sin(th) * s * r;
}
const geomPart = new THREE.BufferGeometry();
geomPart.setAttribute('position', new THREE.BufferAttribute(posBase.slice(), 3));
const particulas = new THREE.Points(geomPart, new THREE.PointsMaterial({
  color: colorActual, size: 0.038, transparent: true, opacity: 0.5,
  blending: THREE.AdditiveBlending, depthWrite: false,
}));
nucleo.add(particulas);

// ---------------------------------------------------------------
// ONDAS AL HABLAR
// Se reciclan tres aros en vez de crearlos y destruirlos: crear geometría
// en cada fotograma provoca tirones al pasar el recolector de basura.
// ---------------------------------------------------------------

const ondas = [];
for (let i = 0; i < 3; i++) {
  const o = new THREE.Mesh(
    new THREE.TorusGeometry(1, 0.02, 6, 90),
    new THREE.MeshBasicMaterial({
      color: colorActual, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
  o.rotation.x = Math.PI / 2;
  o.userData.avance = i / 3;
  nucleo.add(o);
  ondas.push(o);
}

// ---------------------------------------------------------------
// BARRIDO AL PENSAR
// ---------------------------------------------------------------

const barrido = new THREE.Mesh(
  new THREE.TorusGeometry(2.2, 0.03, 8, 60, Math.PI / 2.2),
  new THREE.MeshBasicMaterial({
    color: AMBAR, transparent: true, opacity: 0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
barrido.rotation.x = Math.PI / 2;
nucleo.add(barrido);

// ---------------------------------------------------------------
// CUENTA ATRÁS
// Con un temporizador en marcha, el aro de arcos decorativos se apaga y en
// su sitio aparece un reloj: 72 marcas que se van apagando desde arriba,
// en el sentido de las agujas. El adorno pasa a decir algo.
// ---------------------------------------------------------------

const MARCAS = 72;
const RADIO_MARCAS = 3.75;           // el mismo radio que los arcos a los que sustituye
const marcas = [];
const geomMarca = new THREE.BoxGeometry(0.035, 0.22, 0.035);

for (let i = 0; i < MARCAS; i++) {
  const a = Math.PI / 2 - (i / MARCAS) * Math.PI * 2;
  const m = new THREE.Mesh(geomMarca, new THREE.MeshBasicMaterial({
    color: CIAN, transparent: true, opacity: 0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  m.position.set(Math.cos(a) * RADIO_MARCAS, Math.sin(a) * RADIO_MARCAS, 0);
  m.rotation.z = a - Math.PI / 2;    // apuntando hacia fuera, como las de un reloj
  nucleo.add(m);
  marcas.push(m);
}
let hayCuenta = 0;                   // 0..1, para entrar y salir suave

// ---------------------------------------------------------------
// ANIMACIÓN
// ---------------------------------------------------------------

let t = 0;
let nivelSuave = 0;

/* Al hablar no llega audio de vuelta, así que la forma se genera aquí.
   Tres ritmos superpuestos (sílaba, palabra y frase) imitan el habla;
   un pulso regular se nota falso al instante. */
function envolventeVoz(x) {
  const silaba = 0.5 + 0.5 * Math.sin(x * 11);
  const palabra = 0.5 + 0.5 * Math.sin(x * 3.1 + 1.3);
  const frase = 0.55 + 0.45 * Math.sin(x * 0.8 + 0.4);
  return Math.max(0, silaba * 0.5 + palabra * 0.35 + 0.15) * frase;
}

function alturaBarra(i, hablando) {
  if (hablando) {
    const espejo = i < BARRAS / 2 ? i : BARRAS - i;
    const caida = 1 - Math.abs(espejo - BARRAS / 4) / (BARRAS / 2);
    return envolventeVoz(t + espejo * 0.09) * (0.35 + 0.65 * caida);
  }
  const b = J.bandas;
  if (b && b.length) {
    const espejo = i < BARRAS / 2 ? i : BARRAS - 1 - i;
    return b[Math.min(b.length - 1,
             Math.floor(espejo * b.length / (BARRAS / 2)))] || 0;
  }
  return 0;
}

const reloj = new THREE.Clock();

function animar() {
  requestAnimationFrame(animar);
  const dt = Math.min(reloj.getDelta(), 0.05);
  t += dt;

  const estado = J.estado;
  const hablando = estado === 'hablando';
  const pensando = estado === 'pensando';
  const escuchando = estado === 'escuchando' || estado === 'transcribiendo';

  // el color va con el estado, igual que en el resto de la interfaz
  colorActual.lerp(pensando ? AMBAR : CIAN, dt * 3);

  const objetivo = hablando ? envolventeVoz(t) : (J.nivel || 0);
  nivelSuave += (objetivo - nivelSuave) * (objetivo > nivelSuave ? 0.28 : 0.06);

  // --- anillos: giran más rápido cuanto más pasa ---
  const empuje = 1 + nivelSuave * 2.2 + (pensando ? 1.6 : 0);
  for (const a of anillos) {
    a.rotation.z += a.userData.vel * dt * empuje;
    a.rotation.y += a.userData.vel * dt * 0.4 * empuje;
    a.material.color.copy(colorActual);
    a.material.opacity = 0.35 + nivelSuave * 0.4;
  }
  // --- cuenta atrás: sustituye a los arcos mientras dura ---
  const cuenta = J.cuenta;
  hayCuenta += ((cuenta ? 1 : 0) - hayCuenta) * Math.min(1, dt * 4);
  const queda = cuenta
    ? Math.max(0, (cuenta.fin - performance.now()) / 1000) / cuenta.total : 0;
  const encendidas = Math.ceil(queda * MARCAS);
  for (let i = 0; i < MARCAS; i++) {
    let op = 0.12;                                   // la pista, apagada
    if (i < encendidas) {
      // la última encendida late: es el segundero
      op = i === encendidas - 1 ? 0.45 + 0.5 * Math.abs(Math.sin(t * 3)) : 0.95;
    }
    marcas[i].material.opacity = op * hayCuenta;
  }

  for (let i = 0; i < arcos.length; i++) {
    arcos[i].rotation.z -= dt * 0.35 * empuje;
    arcos[i].material.color.copy(colorActual);
    arcos[i].material.opacity = 0.8 * (1 - hayCuenta);
  }

  // --- barras del espectro ---
  for (let i = 0; i < BARRAS; i++) {
    const b = barras[i];
    const bruto = alturaBarra(i, hablando);
    // sube rápido y baja despacio: si no, parpadea
    b.userData.alto += (bruto - b.userData.alto) *
                       (bruto > b.userData.alto ? 0.45 : 0.10);
    b.scale.y = 0.12 + b.userData.alto * 2.6;
    b.material.color.copy(colorActual);
    b.material.opacity = 0.25 + b.userData.alto * 0.75;
  }

  // --- corazón ---
  corazon.rotation.y += dt * (0.25 + nivelSuave);
  corazon.rotation.x += dt * 0.12;
  const late = 1 + nivelSuave * 0.45 + Math.sin(t * 2.2) * 0.03;
  corazon.scale.setScalar(late);
  corazon.material.color.copy(colorActual);
  brillo.scale.setScalar(0.8 + nivelSuave * 0.8);
  brillo.material.opacity = 0.55 + nivelSuave * 0.45;
  halo.material.color.copy(colorActual);
  halo.material.opacity = 0.05 + nivelSuave * 0.13;

  // --- partículas ---
  particulas.rotation.y += dt * 0.06;
  particulas.material.color.copy(colorActual);
  particulas.material.opacity = 0.28 + nivelSuave * 0.5;
  const pos = geomPart.attributes.position.array;
  const expande = 1 + nivelSuave * 0.16;
  for (let i = 0; i < NUM_PART; i++) {
    const k = i * 3;
    // cada partícula respira con un desfase distinto
    const onda = 1 + Math.sin(t * 1.6 + i * 0.35) * 0.02;
    pos[k]     = posBase[k]     * expande * onda;
    pos[k + 1] = posBase[k + 1] * expande * onda;
    pos[k + 2] = posBase[k + 2] * expande * onda;
  }
  geomPart.attributes.position.needsUpdate = true;

  // --- ondas al hablar ---
  for (const o of ondas) {
    if (hablando) {
      o.userData.avance = (o.userData.avance + dt * 0.42) % 1;
      const r = 1.1 + o.userData.avance * 3.2;
      o.scale.setScalar(r);
      o.material.color.copy(colorActual);
      o.material.opacity = (1 - o.userData.avance) * 0.5 * (0.35 + nivelSuave);
    } else {
      o.material.opacity *= 0.88;      // se apagan suave, no de golpe
    }
  }

  // --- barrido al pensar ---
  barrido.rotation.z -= dt * 3.2;
  barrido.material.opacity += ((pensando ? 0.85 : 0) - barrido.material.opacity) * 0.12;

  // --- balanceo suave del conjunto: le quita rigidez ---
  nucleo.rotation.x = -0.22 + Math.sin(t * 0.35) * 0.05;
  nucleo.rotation.y = Math.sin(t * 0.22) * 0.09;

  render.render(escena, camara);
}

animar();
