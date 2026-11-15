// xScanner space layer: star field + scroll-timeline-driven 3D moon (three.js r170, vendored, MIT).
// Moon colour + elevation maps: NASA Scientific Visualization Studio "CGI Moon Kit" (LRO LROC WAC colour mosaic
// and LOLA elevation), https://svs.gsfc.nasa.gov/4720 — NASA imagery, public domain. Resized/re-encoded only.
// Behaviour: moon drifts / scales / rotates and its lighting moves from gibbous to thin crescent as you scroll.
// Static image fallback for prefers-reduced-motion, Motion toggle off, no WebGL or load failure.
const space = document.getElementById('space');
const docEl = document.documentElement;
const mqReduce = matchMedia('(prefers-reduced-motion: reduce)');
const mqSmall = matchMedia('(max-width: 640px)');
const nav = navigator;
const lowPower = mqSmall.matches || (nav.hardwareConcurrency || 8) <= 4 || (nav.deviceMemory || 8) <= 4 ||
  !!(nav.connection && nav.connection.saveData) || matchMedia('(hover: none)').matches;

// ---------- star field (2D canvas tile, drawn once) ----------
(function stars() {
  const el = space && space.querySelector('.stars'); if (!el) return;
  const S = lowPower ? 512 : 900, c = document.createElement('canvas'); c.width = c.height = S;
  const g = c.getContext('2d'); let seed = 1337; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const n = lowPower ? 260 : 700;
  for (let i = 0; i < n; i++) {
    const x = rnd() * S, y = rnd() * S, big = rnd() > .975, r = big ? .8 + rnd() * .7 : .3 + rnd() * .5;
    const a = big ? .6 + rnd() * .3 : .12 + rnd() * .45, t = rnd();
    const col = t < .12 ? `rgba(170,230,255,${a})` : t < .2 ? `rgba(225,200,255,${a})` : t < .26 ? `rgba(255,228,200,${a})` : `rgba(255,255,255,${a})`;
    if (big) { const gr = g.createRadialGradient(x, y, 0, x, y, r * 3); gr.addColorStop(0, col); gr.addColorStop(1, 'rgba(255,255,255,0)'); g.fillStyle = gr; g.beginPath(); g.arc(x, y, r * 3, 0, 7); g.fill(); }
    g.fillStyle = col; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
  }
  el.style.backgroundImage = `url(${c.toDataURL('image/png')})`;
  el.style.backgroundSize = `${S}px ${S}px`;
})();

// ---------- scroll progress (0..1) shared with CSS (--sp) ----------
let target = 0;
function readScroll() {
  const max = Math.max(1, docEl.scrollHeight - innerHeight);
  target = Math.min(1, Math.max(0, scrollY / max));
}
readScroll();
addEventListener('scroll', readScroll, { passive: true });
addEventListener('resize', readScroll, { passive: true });

const motionOff = () => mqReduce.matches || docEl.classList.contains('fx-off');
function setStatic(on) { space && space.classList.toggle('static', on); }
// static mode still lets the background glow follow scroll (no animation loop)
function staticScroll() { if (space.classList.contains('static')) space.style.setProperty('--sp', target.toFixed(4)); }
addEventListener('scroll', staticScroll, { passive: true });

function hasWebGL() {
  try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); } catch (e) { return false; }
}

let started = false, api = null;
async function boot() {
  if (!space) return;
  if (motionOff() || !hasWebGL()) { setStatic(true); staticScroll(); return; }
  if (started) { api && api.resume(); setStatic(false); return; }
  started = true;
  try { api = await startMoon(); setStatic(false); }
  catch (e) { console.warn('moon: falling back to static', e && e.message); setStatic(true); staticScroll(); }
}
new MutationObserver(() => { if (motionOff()) { api && api.pause(true); setStatic(true); staticScroll(); } else boot(); })
  .observe(docEl, { attributes: true, attributeFilter: ['class'] });
mqReduce.addEventListener && mqReduce.addEventListener('change', () => { if (motionOff()) { api && api.pause(true); setStatic(true); } else boot(); });

// ---------- the moon ----------
// Keyframes along the page scroll: x,y in [-1,1] of the visible half-width/height, r = radius as a fraction of
// min(halfW, halfH), th = light angle around the moon (0 = full, -90 = half, ±180 = new), el = light elevation.
const KF_DESK = [
  { p: 0.00, x: 0.62, y: 0.16, r: 0.56, th: -28, el: 0.25 },
  { p: 0.33, x: -0.62, y: -0.06, r: 0.40, th: -78, el: 0.35 },
  { p: 0.66, x: 0.64, y: -0.18, r: 0.34, th: -122, el: 0.15 },
  { p: 1.00, x: 0.02, y: -0.66, r: 0.62, th: -150, el: 0.08 },
];
const KF_MOB = [
  { p: 0.00, x: 0.62, y: 0.30, r: 0.62, th: -28, el: 0.25 },
  { p: 0.33, x: -0.55, y: 0.12, r: 0.48, th: -78, el: 0.35 },
  { p: 0.66, x: 0.55, y: -0.20, r: 0.46, th: -122, el: 0.15 },
  { p: 1.00, x: 0.00, y: -0.60, r: 0.70, th: -150, el: 0.08 },
];
const ease = t => t * t * (3 - 2 * t);
function sample(kf, p) {
  let i = 0; while (i < kf.length - 2 && p > kf[i + 1].p) i++;
  const a = kf[i], b = kf[i + 1], t = ease(Math.min(1, Math.max(0, (p - a.p) / (b.p - a.p))));
  const o = {}; for (const k of ['x', 'y', 'r', 'th', 'el']) o[k] = a[k] + (b[k] - a[k]) * t; return o;
}

async function startMoon() {
  const THREE = await import('/static/vendor/three-0.170.0.module.min.js');
  const canvas = document.createElement('canvas'); canvas.id = 'moon3d'; canvas.setAttribute('aria-hidden', 'true');
  space.insertBefore(canvas, space.querySelector('.grain'));
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !lowPower, alpha: true, powerPreference: lowPower ? 'low-power' : 'high-performance' });
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
  const DPR_CAP = lowPower ? 1.5 : 2;

  const scene = new THREE.Scene();
  const FOV = 30, DIST = 12;
  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 100); camera.position.set(0, 0, DIST);

  const loader = new THREE.TextureLoader();
  const load = url => new Promise((res, rej) => loader.load(url, res, undefined, () => rej(new Error('texture ' + url))));
  const [map, bump] = await Promise.all([
    load(lowPower ? '/static/img/moon-1k.jpg' : '/static/img/moon-2k.jpg'),
    load('/static/img/moon-bump-1k.jpg'),
  ]);
  map.colorSpace = THREE.SRGBColorSpace;
  const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy()); map.anisotropy = aniso; bump.anisotropy = aniso;

  const seg = lowPower ? [72, 48] : [160, 112];
  const moon = new THREE.Mesh(
    new THREE.SphereGeometry(1, seg[0], seg[1]),
    new THREE.MeshStandardMaterial({ map, bumpMap: bump, bumpScale: lowPower ? 2.2 : 3.0, roughness: 1, metalness: 0 })
  );
  const pivot = new THREE.Group(); pivot.add(moon); scene.add(pivot);
  pivot.rotation.z = THREE.MathUtils.degToRad(6.7); // axial tilt

  // soft iridescent halo (fresnel rim, brighter on the lit side) — ties the moon into the pastel background glow
  const halo = new THREE.Mesh(new THREE.SphereGeometry(1.22, 64, 48), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.BackSide, blending: THREE.AdditiveBlending,
    uniforms: { uLight: { value: new THREE.Vector3(0, 0, 1) }, uK: { value: 1 } },
    vertexShader: `varying vec3 vN; varying vec3 vV; void main(){ vec4 wp = modelMatrix*vec4(position,1.); vN = normalize(mat3(modelMatrix)*normal); vV = normalize(cameraPosition-wp.xyz); gl_Position = projectionMatrix*viewMatrix*wp; }`,
    fragmentShader: `uniform vec3 uLight; uniform float uK; varying vec3 vN; varying vec3 vV;
      void main(){ float d = abs(dot(vN, vV)); float f = pow(clamp(d / 0.57, 0.0, 1.0), 3.0); // 0 at halo edge, 1 at the moon's limb
        float lit = 0.35 + 0.65 * max(dot(-vN, uLight), 0.0);
        vec3 c = mix(vec3(0.61,0.96,1.0), vec3(0.83,0.70,0.93), 0.5 + 0.5 * vN.y);
        gl_FragColor = vec4(c * f * lit * 0.55 * uK, f * lit * uK); }`,
  }));
  pivot.add(halo);

  const sun = new THREE.DirectionalLight(0xfff6ea, 3.4); scene.add(sun); scene.add(sun.target);
  const earthshine = new THREE.HemisphereLight(0x9fb6ff, 0x1a1420, 0.14); scene.add(earthshine);

  let W = 0, H = 0, halfH = 1, halfW = 1;
  function resize() {
    W = innerWidth; H = innerHeight;
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, DPR_CAP));
    renderer.setSize(W, H, false);
    camera.aspect = W / H; camera.updateProjectionMatrix();
    halfH = Math.tan(THREE.MathUtils.degToRad(FOV / 2)) * DIST; halfW = halfH * camera.aspect;
  }
  resize(); addEventListener('resize', resize, { passive: true });

  // gentle pointer parallax on desktop
  let mx = 0, my = 0, pmx = 0, pmy = 0;
  if (!lowPower) addEventListener('pointermove', e => { mx = e.clientX / W - .5; my = e.clientY / H - .5; }, { passive: true });

  let s = target, idle = 0, last = performance.now(), raf = 0, paused = false, acc = 0;
  const FRAME = lowPower ? 1 / 30 : 0;
  function frame(now) {
    raf = requestAnimationFrame(frame);
    let dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (FRAME) { acc += dt; if (acc < FRAME) return; dt = acc; acc = 0; }
    s += (target - s) * (1 - Math.exp(-dt * 4.5));           // eased scroll timeline
    idle += dt * 0.045;                                        // slow idle spin
    pmx += (mx - pmx) * (1 - Math.exp(-dt * 3)); pmy += (my - pmy) * (1 - Math.exp(-dt * 3));
    const k = sample(mqSmall.matches ? KF_MOB : KF_DESK, s);
    const m = Math.min(halfW, halfH);
    pivot.position.set(k.x * halfW + pmx * 0.25, k.y * halfH - pmy * 0.18, 0);
    pivot.scale.setScalar(k.r * m);
    moon.rotation.y = idle + s * 2.4 + pmx * 0.15;
    moon.rotation.x = -0.12 + s * 0.35 + pmy * 0.08;
    const th = THREE.MathUtils.degToRad(k.th);
    sun.position.set(pivot.position.x + Math.sin(th) * 10, pivot.position.y + k.el * 10, Math.cos(th) * 10);
    sun.target.position.copy(pivot.position);
    halo.material.uniforms.uLight.value.copy(sun.position).sub(pivot.position).normalize();
    space.style.setProperty('--sp', s.toFixed(4));
    renderer.render(scene, camera);
  }
  function resume() { if (!paused && raf) return; paused = false; last = performance.now(); cancelAnimationFrame(raf); raf = requestAnimationFrame(frame); }
  function pause() { paused = true; cancelAnimationFrame(raf); raf = 0; }
  document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); else if (!motionOff()) resume(); });
  canvas.addEventListener('webglcontextlost', e => { e.preventDefault(); pause(); setStatic(true); });

  resume();
  requestAnimationFrame(() => canvas.classList.add('on'));
  window.__moon = { get progress() { return s; } };
  return { pause, resume };
}

boot();
