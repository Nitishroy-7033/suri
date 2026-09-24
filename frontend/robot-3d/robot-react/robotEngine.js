// Suri robot engine — framework-agnostic three.js character.
// Used by <SuriRobot/> (React), but works on its own:
//   const bot = createRobot(document.getElementById('host'), { mood: 'happy' });
//   bot.play('wave'); bot.say('Hello!'); bot.setOptions({ antennaSpring: 0.9 }); bot.dispose();
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

const MOOD_NAMES = ['idle', 'listening', 'thinking', 'talking', 'happy', 'love', 'alert', 'confused', 'angry', 'dizzy', 'sleepy', 'sad'];
const GESTURE_NAMES = ['nod', 'shake', 'surprise', 'spin', 'scan', 'celebrate', 'sneeze', 'boot', 'wave'];
export { MOOD_NAMES as MOODS, GESTURE_NAMES as GESTURES };
/** [leftUp, rightUp, leftForward, rightForward] in degrees */
export const ARM_POSES = {
  rest: [0, 0, 0, 0], bothUp: [160, 160, 0, 0], point: [0, 15, 0, 90], pointUp: [0, 165, 0, 0],
  reachOut: [10, 10, 90, 90], tPose: [85, 85, 0, 0], victory: [130, 130, 20, 20], hi: [0, 140, 0, 10],
  shrug: [45, 45, 30, 30], hug: [-5, -5, 65, 65], chin: [0, 8, 0, 100], hips: [35, 35, -35, -35],
};
export const DEFAULTS = {
  mood: 'idle', look: 'wander', antennaMotion: 1, antennaSpring: 0.55, blinkRate: 14, speed: 1,
  headMotion: true, earPulse: true, tipLight: true, talkRate: 1, mouthSize: 1, voice: true,
  armMode: 'auto', armPose: [0, 0, 0, 0], background: null, orbit: true, shadow: true, boot: true,
  colors: { shell: '#f3f1ec', trim: '#c4c7cb', screen: '#0c0e12', glow: '#1e88ff' },
};
const KEY = { look: 'look', antennaMotion: 'antAmt', antennaSpring: 'spring', blinkRate: 'blinkRate', speed: 'speed',
  headMotion: 'headMo', earPulse: 'earMo', tipLight: 'tip', talkRate: 'talkRate', mouthSize: 'mouthAmt', voice: 'voice', armMode: 'armMode' };

export function createRobot(container, options = {}) {
  const opts = { ...DEFAULTS, ...options };

  // ---------- renderer / scene ----------
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  Object.assign(renderer.domElement.style, { display: 'block', width: '100%', height: '100%', outline: 'none' });
  container.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.01, 50);
  scene.add(new THREE.HemisphereLight(0xffffff, 0xb8bcc2, 1.5));
  const key = new THREE.DirectionalLight(0xffffff, 2.1);
  key.position.set(1.2, 2.2, 1.8); key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048); key.shadow.bias = -0.0005;
  Object.assign(key.shadow.camera, { left: -0.7, right: 0.7, top: 0.9, bottom: -0.5, near: 0.1, far: 8 });
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.7); fill.position.set(-1.6, 0.9, 1.2); scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 0.5); rim.position.set(0, 1.2, -2); scene.add(rim);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(6, 6).rotateX(-Math.PI / 2), new THREE.ShadowMaterial({ opacity: 0.16 }));
  ground.receiveShadow = true; scene.add(ground);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.enablePan = false;

  const ext = { speaking: false, bands: null, level: 0 };

  // ---------- model + animation (generated from Robot Head.html) ----------
  const g = new THREE.Group(); g.name = 'robot';

  const shell = new THREE.MeshStandardMaterial({ name: 'shell_white', color: 0xf3f1ec, roughness: 0.38, metalness: 0 });
  const trim = new THREE.MeshStandardMaterial({ name: 'trim_grey', color: 0xc4c7cb, roughness: 0.45, metalness: 0.1 });
  const glass = new THREE.MeshStandardMaterial({ name: 'screen_glass', color: 0x0c0e12, roughness: 0.12, metalness: 0.25 });
  const glow = new THREE.MeshStandardMaterial({ name: 'glow_blue', color: 0x1e88ff, emissive: 0x1e88ff, emissiveIntensity: 1.1, roughness: 0.3 });
  const earGlow = new THREE.MeshStandardMaterial({ name: 'ear_glow', color: 0x1e88ff, emissive: 0x1e88ff, emissiveIntensity: 1.1, roughness: 0.3 });

  const NECK_Y = 0.305;
  const head = new THREE.Group(); head.name = 'head'; head.position.y = NECK_Y; g.add(head);

  const add = (name, geo, mat, x = 0, y = 0, z = 0, parent = head) => {
    const m = new THREE.Mesh(geo, mat); m.name = name;
    m.position.set(x, y - (parent === head ? NECK_Y : 0), z);
    m.castShadow = m.receiveShadow = true; parent.add(m); return m;
  };
  const rrect = (w, h, r) => {
    const s = new THREE.Shape(), x = -w / 2, y = -h / 2;
    s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r);
    s.lineTo(x + w, y + h - r); s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    s.lineTo(x + r, y + h); s.quadraticCurveTo(x, y + h, x, y + h - r);
    s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y); return s;
  };

  // ---- base ----
  const baseProf = [new THREE.Vector2(0, 0)];
  for (let i = 0; i <= 12; i++) { const a = -Math.PI / 2 + Math.PI * i / 12; baseProf.push(new THREE.Vector2(0.196 + Math.cos(a) * 0.014, 0.014 + Math.sin(a) * 0.014)); }
  baseProf.push(new THREE.Vector2(0, 0.028));
  add('base', new THREE.LatheGeometry(baseProf, 72), shell, 0, 0, 0, g).scale.z = 0.78;

  // ---- body (egg, broad shoulders) ----
  const BODY = { cy: 0.155, hy: 0.145, sz: 0.9 };
  const bodyPt = a => { const sa = Math.sin(a), e = sa > 0 ? 0.3 : 0.6;
    return [(0.136 - 0.014 * sa) * Math.pow(Math.max(0, Math.cos(a)), e), BODY.cy + BODY.hy * sa]; };
  const bodyProf = [];
  for (let i = 0; i <= 48; i++) { const [r, y] = bodyPt(-Math.PI / 2 + Math.PI * i / 48); bodyProf.push(new THREE.Vector2(Math.max(r, 0), y)); }
  add('body', new THREE.LatheGeometry(bodyProf, 72), shell, 0, 0, 0, g).scale.z = BODY.sz;
  const bodyR = y => { const sa = Math.max(-1, Math.min(1, (y - BODY.cy) / BODY.hy)); return bodyPt(Math.asin(sa))[0]; };
  add('collar', new THREE.TorusGeometry(0.07, 0.006, 16, 64), trim, 0, 0.296, 0, g).rotation.x = Math.PI / 2;
  add('neck', new THREE.CylinderGeometry(0.04, 0.045, 0.03, 48), trim, 0, 0.305, 0, g);

  // chest tab hanging from the collar
  const tab = (w, l) => { const s = new THREE.Shape(), r = w / 2;
    s.moveTo(-r, 0); s.lineTo(-r, -l + r); s.absarc(0, -l + r, r, Math.PI, 0, false); s.lineTo(r, 0); s.lineTo(-r, 0); return s; };
  {
    const yT = 0.292, L = 0.085, yB = yT - L, zT = bodyR(yT) * BODY.sz, zB = bodyR(yB + 0.012) * BODY.sz, ang = Math.atan2(zB - zT, L);
    const mk = (name, w, l, d, mat, dz) => {
      const gg = new THREE.ExtrudeGeometry(tab(w, l), { depth: d, bevelEnabled: true, bevelThickness: 0.003, bevelSize: 0.003, bevelSegments: 6, curveSegments: 32 });
      const m = add(name, gg, mat, 0, yT, zT - 0.006 + dz, g); m.rotation.x = -ang; return m;
    };
    mk('chest_tab_groove', 0.058, L + 0.004, 0.003, trim, -0.001);
    mk('chest_tab', 0.05, L, 0.005, shell, 0.003);
  }

  // ---- arms (flippers) on shoulder pivots ----
  const armProf = [], AL = 0.175, AR = 0.042;
  for (let i = 0; i <= 40; i++) { const s = 1 - i / 40; armProf.push(new THREE.Vector2(AR * Math.pow(Math.sin(Math.PI * Math.pow(s, 0.6)), 0.7) + (i === 0 || i === 40 ? 0 : 0.0005), -AL * s)); }
  const armGeo = new THREE.LatheGeometry(armProf, 40);
  const arms = [-1, 1].map(s => {
    const side = s < 0 ? 'L' : 'R';
    const p = new THREE.Group(); p.name = 'arm_' + side; p.position.set(s * 0.152, 0.27, 0.004); g.add(p);
    const m = add('arm_' + side + '_shell', armGeo, shell, 0, 0.02, 0, p); m.scale.set(0.62, 1, 1);
    add('shoulder_' + side, new THREE.SphereGeometry(0.013, 24, 12), trim, -s * 0.012, 0, 0, p);
    return p;
  });

  // ---- head (rounded box with flat screen) ----
  const HY = 0.47, D = 0.16, B = 0.035;
  const headGeo = new THREE.ExtrudeGeometry(rrect(0.30, 0.24, 0.09), { depth: D, bevelEnabled: true, bevelThickness: B, bevelSize: B, bevelSegments: 10, curveSegments: 32 });
  headGeo.translate(0, 0, -D / 2);
  add('head_shell', headGeo, shell, 0, HY, 0);
  const FZ = D / 2 + B;
  add('screen_bezel', new THREE.ExtrudeGeometry(rrect(0.29, 0.21, 0.075), { depth: 0.003, bevelEnabled: false, curveSegments: 32 }), trim, 0, HY - 0.012, FZ + 0.001);
  add('screen', new THREE.ExtrudeGeometry(rrect(0.278, 0.198, 0.07), { depth: 0.002, bevelEnabled: true, bevelThickness: 0.002, bevelSize: 0.002, bevelSegments: 3, curveSegments: 32 }), glass, 0, HY - 0.012, FZ + 0.006);
  const SZ = FZ + 0.0105;

  const face = new THREE.Group(); face.name = 'face'; face.position.set(0, HY - NECK_Y, SZ); head.add(face);
  const eyeGeo = new THREE.SphereGeometry(1, 32, 16);
  const EYE = { x: 0.065, y: 0.01, sx: 0.023, sy: 0.032 };
  const eyes = [-1, 1].map(s => {
    const m = new THREE.Mesh(eyeGeo, glow); m.name = s < 0 ? 'eye_L' : 'eye_R';
    m.position.set(s * EYE.x, EYE.y, 0); m.scale.set(EYE.sx, EYE.sy, 0.004); face.add(m); return m;
  });
  const mouth = new THREE.Group(); mouth.name = 'mouth'; mouth.position.set(0, -0.048, 0); face.add(mouth);
  const smile = new THREE.Mesh(new THREE.TorusGeometry(0.026, 0.0055, 12, 40, Math.PI), glow);
  smile.name = 'smile'; smile.rotation.z = Math.PI; smile.scale.z = 0.5; mouth.add(smile);
  const tongueMat = new THREE.MeshStandardMaterial({ name: 'tongue', color: 0xff7a9c, emissive: 0xff7a9c, emissiveIntensity: 0.55, roughness: 0.4 });
  const discGeo = new THREE.CircleGeometry(1, 48);
  const mOuter = new THREE.Mesh(discGeo, glow); mOuter.name = 'mouth_open';
  const mInner = new THREE.Mesh(discGeo, glass); mInner.name = 'mouth_inner'; mInner.position.z = 0.0004;
  const tongue = new THREE.Mesh(new THREE.CircleGeometry(1, 32, 0, Math.PI), tongueMat); tongue.name = 'tongue'; tongue.position.z = 0.0008;
  for (const m of [mOuter, mInner, tongue]) { m.position.y = -0.012; m.scale.set(0.001, 0.001, 1); m.visible = false; mouth.add(m); }

  add('camera_ring', new THREE.CylinderGeometry(0.009, 0.009, 0.003, 32), trim, 0, HY + 0.108, FZ + 0.0015).rotation.x = Math.PI / 2;
  add('camera_lens', new THREE.CylinderGeometry(0.0055, 0.0055, 0.004, 32), glass, 0, HY + 0.108, FZ + 0.003).rotation.x = Math.PI / 2;

  const EX = 0.15 + B;
  for (const s of [-1, 1]) {
    const side = s < 0 ? 'L' : 'R';
    add('ear_' + side, new THREE.CylinderGeometry(0.068, 0.075, 0.035, 48), shell, s * (EX + 0.012), HY, 0).rotation.z = s * Math.PI / 2;
    add('ear_ring_' + side, new THREE.TorusGeometry(0.052, 0.006, 16, 48), trim, s * (EX + 0.03), HY, 0).rotation.y = Math.PI / 2;
    add('ear_light_' + side, new THREE.CylinderGeometry(0.028, 0.028, 0.004, 40), earGlow, s * (EX + 0.03), HY, 0).rotation.z = Math.PI / 2;
  }

  // antenna — stem + ball on a pivot at the collar
  const TOP = HY + 0.12 + B;
  add('antenna_collar', new THREE.CylinderGeometry(0.022, 0.028, 0.012, 32), trim, 0, TOP + 0.002, 0);
  const tipGlow = new THREE.MeshStandardMaterial({ name: 'antenna_glow', color: 0x1e88ff, emissive: 0x1e88ff, emissiveIntensity: 1.4, roughness: 0.25 });
  const ant = new THREE.Group(); ant.name = 'antenna'; ant.position.y = TOP - NECK_Y; head.add(ant);
  add('antenna_joint', new THREE.SphereGeometry(0.012, 32, 16), trim, 0, 0.004, 0, ant);
  add('antenna_stem_lower', new THREE.CylinderGeometry(0.0052, 0.0068, 0.042, 24), shell, 0, 0.024, 0, ant);
  add('antenna_mid_joint', new THREE.SphereGeometry(0.0078, 24, 12), trim, 0, 0.045, 0, ant);
  const ant2 = new THREE.Group(); ant2.name = 'antenna_upper'; ant2.position.y = 0.045; ant.add(ant2);
  add('antenna_stem_upper', new THREE.CylinderGeometry(0.0036, 0.0048, 0.034, 20), shell, 0, 0.017, 0, ant2);
  add('antenna_cap', new THREE.CylinderGeometry(0.0105, 0.006, 0.008, 32), trim, 0, 0.034, 0, ant2);
  add('antenna_cap_ring', new THREE.TorusGeometry(0.0105, 0.0022, 12, 40), shell, 0, 0.038, 0, ant2).rotation.x = Math.PI / 2;
  const ball = add('antenna_tip', new THREE.SphereGeometry(0.019, 40, 24), tipGlow, 0, 0.054, 0, ant2);
  add('antenna_tip_highlight', new THREE.SphereGeometry(0.006, 16, 8), shell, -0.007, 0.062, 0.012, ant2).scale.set(1, 0.7, 0.5);

  scene.add(g);

  // ---------- animation ----------
  let BLUE = 0x1e88ff;
  const MOODS = {
    idle:      { tilt: 0,     amp: 0.10, hz: 0.5, style: 'sway',    open: 1.0,  lookY: 0,    smile: [1, 1],      headTilt: 0,     ear: 1.0, earHz: 0.4 },
    listening: { tilt: -0.18, amp: 0.05, hz: 1.6, style: 'perk',    open: 1.12, lookY: 0,    smile: [0.9, 0.7],  headTilt: 0.09,  ear: 1.5, earHz: 1.4 },
    thinking:  { tilt: 0,     amp: 0.22, hz: 0.45,style: 'orbit',   open: 0.9,  lookY: 0.7,  smile: [0.6, 0.25], headTilt: -0.06, ear: 1.2, earHz: 0.8 },
    talking:   { tilt: 0,     amp: 0.08, hz: 2.4, style: 'bounce',  open: 1.0,  lookY: 0,    smile: [1, 1],      headTilt: 0,     ear: 1.3, earHz: 2.0 },
    happy:     { tilt: 0,     amp: 0.32, hz: 2.6, style: 'wiggle',  open: 0.45, lookY: 0.1,  smile: [1.3, 1.5],  headTilt: 0.05,  ear: 1.8, earHz: 1.8 },
    love:      { tilt: -0.1,  amp: 0.14, hz: 0.9, style: 'sway',    open: 0.5,  lookY: 0.15, smile: [1.1, 1.2],  headTilt: 0.14,  ear: 1.6, earHz: 1.1, color: 0xff4f9a },
    alert:     { tilt: 0,     amp: 0.025,hz: 9,   style: 'vibrate', open: 1.3,  lookY: 0,    smile: [0.7, 0.3],  headTilt: 0,     ear: 2.2, earHz: 3.2, color: 0xffb020 },
    confused:  { tilt: 0,     amp: 0.05, hz: 0.7, style: 'bent',    open: 1.0,  lookY: 0.2,  smile: [0.7, 0.2],  headTilt: 0.16,  ear: 1.0, earHz: 0.6, asym: 0.28 },
    angry:     { tilt: -0.3,  amp: 0.03, hz: 7,   style: 'vibrate', open: 0.55, lookY: -0.1, smile: [0.8, -0.6], headTilt: 0,     ear: 2.0, earHz: 2.6, color: 0xff3b30, eyeRot: 0.45 },
    dizzy:     { tilt: 0.2,   amp: 0.35, hz: 0.9, style: 'orbit',   open: 0.9,  lookY: 0,    smile: [0.7, 0.3],  headTilt: 0,     ear: 0.8, earHz: 0.9, dizzy: 1 },
    sleepy:    { tilt: 0.75,  amp: 0.06, hz: 0.25,style: 'sway',    open: 0.22, lookY: -0.5, smile: [0.6, 0.4],  headTilt: 0.12,  ear: 0.3, earHz: 0.2, glow: 0.55 },
    sad:       { tilt: 0.5,   amp: 0.04, hz: 0.35,style: 'sway',    open: 0.75, lookY: -0.7, smile: [0.85, -0.8],headTilt: -0.05, ear: 0.6, earHz: 0.3, color: 0x5b8fd6, eyeRot: -0.3 },
  };
  const GEST = { nod: 1.1, shake: 1.2, surprise: 1.4, spin: 1.6, scan: 2.4, celebrate: 2.2, boot: 3.0, sneeze: 1.6, wave: 2.4 };
  const S = { mood: 'idle', antAmt: 1, look: 'wander', blinkRate: 14, speed: 1, headMo: true, earMo: true, spring: 0.55, tip: true, talkRate: 1, mouthAmt: 1, voice: true };
  const ARMS = { idle: [0, 0, 0, 0], listening: [0.05, 0.05, 0.1, 0.1], thinking: [0, 0.15, 0, 1.55], talking: [0, 0, 0, 0],
    happy: [0.75, 0.75, 0, 0], love: [0, 0, 0.85, 0.85], alert: [0.4, 0.4, 0.2, 0.2], confused: [0.6, 0.6, 0.35, 0.35],
    angry: [0.12, 0.12, 0.05, 0.05], dizzy: [0.3, 0.3, 0, 0], sleepy: [-0.04, -0.04, 0.05, 0.05], sad: [-0.05, -0.05, 0.3, 0.3] };
  const armC = [0, 0, 0, 0];
  S.armMode = 'auto'; S.armPose = [0, 0, 0, 0]; S.armMirror = false;
  const CLOSED = { o: 0, w: 1, r: 0 }, mc = { o: 0, w: 1, r: 0 };
  const WORDS = 'hello there how are you doing today i am suri your robot friend let me think about that okay sounds good what would you like to know i can help with that sure thing wonderful great question here is what i found'.split(' ');
  const babble = () => { let s = '', n = 4 + (Math.random() * 6 | 0); for (let i = 0; i < n; i++) s += WORDS[Math.random() * WORDS.length | 0] + (i < n - 1 ? (Math.random() < 0.12 ? ', ' : ' ') : ''); return s + (Math.random() < 0.3 ? '? ' : '. '); };
  function vis(c) {
    if ('a'.includes(c)) return [{ o: 1, w: 1.05, r: 0 }, 0.09];
    if ('ei'.includes(c)) return [{ o: 0.45, w: 1.25, r: 0 }, 0.08];
    if ('ouwy'.includes(c)) return [{ o: 0.75, w: 0.72, r: 1 }, 0.1];
    if ('mbp'.includes(c)) return [CLOSED, 0.07];
    if ('fv'.includes(c)) return [{ o: 0.12, w: 1.05, r: 0 }, 0.07];
    if (c === ' ') return [{ o: 0.05, w: 1, r: 0 }, 0.08];
    if ('.!?'.includes(c)) return [CLOSED, 0.42];
    if (',;:'.includes(c)) return [CLOSED, 0.22];
    if (/[a-z]/.test(c)) return [{ o: 0.3, w: 0.95, r: 0.15 }, 0.06];
    return [CLOSED, 0.05];
  }
  const talk = { str: '', i: 0, left: 0, v: CLOSED, say: false, speech: false };
  function talkStep(dt) {
    talk.left -= dt * S.talkRate / S.speed;
    let guard = 0;
    while (talk.left <= 0 && guard++ < 50) {
      if (talk.i >= talk.str.length) {
        if (talk.say) {
          if (talk.speech) { talk.v = CLOSED; talk.left = 0.05; break; }
          talk.say = false; updSay();
          if (S.mood !== 'talking') { talk.v = CLOSED; talk.left = 0.1; break; }
        }
        talk.str = babble(); talk.i = 0;
      }
      const [v, d] = vis(talk.str[talk.i++].toLowerCase()); talk.v = v; talk.left += d * (0.85 + Math.random() * 0.3);
    }
    return talk.v;
  }
  function say(text) {
    text = (text || '').trim(); if (!text) return;
    talk.str = text + ' '; talk.i = 0; talk.left = 0; talk.say = true; talk.speech = false;
    if (S.voice && 'speechSynthesis' in window) {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text); u.rate = S.talkRate; u.pitch = 1.35;
      u.onstart = () => { talk.i = 0; talk.left = 0; };
      u.onboundary = e => { if (e.name === 'word') { talk.i = e.charIndex; talk.left = 0; } };
      u.onend = u.onerror = () => { talk.speech = false; talk.i = talk.str.length; };
      talk.speech = true; speechSynthesis.speak(u);
    }
    updSay();
  }
  function stopSay() { if ('speechSynthesis' in window) speechSynthesis.cancel(); talk.speech = false; talk.say = false; talk.str = ''; talk.i = 0; updSay(); }
  function updSay() { opts.onSpeakingChange && opts.onSpeakingChange(talk.say); }
  const cur = { tilt: 0, amp: 0.1, hz: 0.5, open: 1, lookX: 0, lookY: 0, smileW: 1, smileH: 1, headTilt: 0, ear: 1, earHz: 0.4, asym: 0, eyeRot: 0, dizzy: 0, glow: 1 };
  const col = new THREE.Color(BLUE), tmpC = new THREE.Color();
  const hb = { x: 0, y: 0 };
  const sp = { p: { x: 0, z: 0 }, v: { x: 0, z: 0 } };
  let phase = 0, t = 0, last = performance.now(), gest = null;
  let blink = { t: -1, eye: -1 }, nextBlink = 2, wander = { x: 0, y: 0, next: 1 };
  const mouse = { x: 0, y: 0 };
  const onMove = e => { mouse.x = e.clientX / innerWidth * 2 - 1; mouse.y = -(e.clientY / innerHeight * 2 - 1); };
  addEventListener('pointermove', onMove);

  const lerp = (a, b, k) => a + (b - a) * k;
  const ease = u => u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
  const bump = u => Math.sin(Math.min(1, Math.max(0, u)) * Math.PI);
  const blinkCurve = u => u < 0 || u > 1 ? 1 : Math.abs(Math.cos(u * Math.PI));
  const triggerBlink = (eye = -1) => { blink = { t: 0, eye }; };
  const play = name => { gest = { name, u: 0 }; if (name === 'surprise') triggerBlink(); };

  function gesture(dt) {
    const G = { arm: null, hx: 0, hy: 0, hz: 0, py: 0, antX: 0, antZ: 0, open: 1, look: null, mouth: null, glow: 1, ear: 1 };
    if (!gest) return G;
    gest.u += dt / GEST[gest.name]; const u = Math.min(1, gest.u), f = 1 - u;
    switch (gest.name) {
      case 'wave': { const env = Math.min(1, u * 5, (1 - u) * 5); G.arm = [0, 2.5 * env + Math.sin(u * Math.PI * 9) * 0.35 * env, 0, 0.25 * env]; G.hz = 0.06 * env; break; }
      case 'nod': G.hx = Math.sin(u * Math.PI * 4) * 0.13 * f; G.antX = -Math.sin(u * Math.PI * 4 - 0.6) * 0.25 * f; break;
      case 'shake': G.hy = Math.sin(u * Math.PI * 6) * 0.22 * f; G.antZ = Math.sin(u * Math.PI * 6 - 0.8) * 0.3 * f; G.look = [-Math.sin(u * Math.PI * 6) * 0.5 * f, 0]; break;
      case 'surprise': { const p = bump(u / 0.35) * (u < 0.35 ? 1 : 0) + (u >= 0.35 ? 1 - ease((u - 0.35) / 0.65) : 0);
        G.open = 1 + 0.55 * p; G.arm = [0.8 * p, 0.8 * p, 0.2 * p, 0.2 * p]; G.hx = -0.1 * p; G.py = 0.012 * p; G.antX = -0.25 * p + Math.sin(u * 40) * 0.08 * p; G.vis = { o: 0.9 * p, w: 0.65, r: 1 }; G.mouth = [1 - p, 1 - p]; G.ear = 1 + p; break; }
      case 'spin': G.arm = [0.5 * bump(u), 0.5 * bump(u), 0, 0]; G.hy = ease(u) * Math.PI * 2; G.antZ = Math.sin(u * Math.PI) * -0.5; G.py = bump(u) * 0.01; break;
      case 'scan': { const s = Math.sin(u * Math.PI * 3); G.look = [s * f * 1.1 + 0, 0]; G.hy = s * 0.18 * f; G.open = 0.75 + 0.25 * f; G.antX = -0.15 * bump(u); G.antZ = Math.sin(u * Math.PI * 12) * 0.05; G.ear = 1.5 + Math.sin(u * 60) * 0.5; break; }
      case 'celebrate': { const env = Math.min(1, u * 5, (1 - u) * 4); G.arm = [2.3 * env + Math.sin(u * 60) * 0.2 * env, 2.3 * env + Math.sin(u * 60 + 1) * 0.2 * env, 0, 0]; } G.py = Math.abs(Math.sin(u * Math.PI * 5)) * 0.02 * f; G.hz = Math.sin(u * Math.PI * 5) * 0.08 * f; G.antZ = Math.sin(u * Math.PI * 16) * 0.45 * f; G.open = 0.45 + 0.55 * u; G.mouth = [1.35, 1.6]; G.ear = 1 + Math.abs(Math.sin(u * 30)) * 1.2 * f; break;
      case 'boot': { const on = Math.min(1, Math.max(0, (u - 0.25) / 0.3)); const flick = u < 0.25 ? (Math.sin(u * 90) > 0.3 ? 0.4 : 0.02) : 1;
        G.glow = u < 0.25 ? flick * 0.4 : 0.2 + on * 0.8; G.open = u < 0.25 ? 0.06 : 0.06 + ease(on) * 0.94; G.ear = u < 0.25 ? flick : on;
        G.antX = u < 0.55 ? 0.7 * (1 - ease(Math.max(0, u - 0.3) / 0.25)) : Math.sin((u - 0.55) * 30) * 0.1 * (1 - u); G.hx = u < 0.4 ? 0.1 : 0.1 * (1 - ease((u - 0.4) / 0.3));
        G.mouth = on < 1 ? [on, on] : null; G.look = u > 0.6 && u < 0.9 ? [Math.sin((u - 0.6) / 0.3 * Math.PI * 2) * 0.7, 0] : null; break; }
      case 'sneeze': { const build = u < 0.55 ? ease(u / 0.55) : 0, burst = u >= 0.55 ? bump((u - 0.55) / 0.45) : 0;
        G.hx = -0.12 * build + 0.2 * burst; G.open = 1 - 0.6 * build - 0.9 * burst; G.mouth = [0.7 + 0.3 * build, 0.3 - 1.2 * build + 1.2 * burst]; G.arm = [0.1, 0.1, 0.6 * burst + 0.3 * build, 0.6 * burst + 0.3 * build]; G.vis = burst > 0.05 ? { o: burst, w: 0.8, r: 0.7 } : null; G.antX = -0.2 * build + 0.7 * burst; G.antZ = Math.sin(u * 50) * 0.15 * burst; break; }
    }
    if (gest.u >= 1) gest = null;
    return G;
  }

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000) * S.speed; last = now; t += dt;
    const M = MOODS[S.mood], k = 1 - Math.exp(-dt * 6);
    for (const key of ['tilt', 'amp', 'hz', 'open', 'headTilt', 'ear', 'earHz']) cur[key] = lerp(cur[key], M[key], k);
    cur.asym = lerp(cur.asym, M.asym ?? 0, k); cur.eyeRot = lerp(cur.eyeRot, M.eyeRot ?? 0, k);
    cur.dizzy = lerp(cur.dizzy, M.dizzy ?? 0, k); cur.glow = lerp(cur.glow, M.glow ?? 1, k);
    cur.smileW = lerp(cur.smileW, M.smile[0], k); cur.smileH = lerp(cur.smileH, M.smile[1], k);
    col.lerp(tmpC.set(M.color ?? BLUE), k);
    phase += dt * cur.hz * Math.PI * 2;
    const G = gesture(dt);

    // antenna
    const a = cur.amp * S.antAmt; let rx = cur.tilt, rz = 0;
    switch (M.style) {
      case 'orbit':   rx += Math.sin(phase) * a; rz = Math.cos(phase) * a; break;
      case 'bounce':  rx += Math.abs(Math.sin(phase)) * a; rz = Math.sin(phase * 0.5) * a * 0.5; break;
      case 'wiggle':  rz = Math.sin(phase) * a; rx += Math.sin(phase * 2) * a * 0.3; break;
      case 'vibrate': rz = Math.sin(phase) * a; rx += Math.cos(phase * 1.3) * a; break;
      case 'perk':    rz = Math.sin(phase) * a * (0.5 + 0.5 * Math.sin(phase * 0.23)); break;
      case 'bent':    rz = -0.4 * Math.min(1, S.antAmt) + Math.sin(phase) * a; rx += 0.1; break;
      default:        rz = Math.sin(phase) * a; rx += Math.sin(phase * 0.7) * a * 0.3;
    }
    ant.rotation.x = rx + G.antX * S.antAmt; ant.rotation.z = rz + G.antZ * S.antAmt;
    // upper segment: damped spring that lags behind the lower one (whip / follow-through)
    const kS = lerp(220, 22, S.spring), dmp = lerp(16, 4.5, S.spring);
    for (const ax of ['x', 'z']) {
      const target = (ant.rotation[ax] + head.rotation[ax]) * 1.35;
      sp.v[ax] += ((target - sp.p[ax]) * kS - sp.v[ax] * dmp) * dt;
      sp.p[ax] += sp.v[ax] * dt;
      ant2.rotation[ax] = Math.max(-1, Math.min(1, sp.p[ax] - ant.rotation[ax] - head.rotation[ax]));
    }
    ball.scale.setScalar(M.style === 'bounce' ? 1 + Math.abs(Math.sin(phase)) * 0.06 : 1);

    // gaze
    let tx = 0, ty = M.lookY;
    if (S.look === 'cursor') { tx = mouse.x; ty = mouse.y; }
    else if (S.look === 'wander') {
      wander.next -= dt;
      if (wander.next <= 0) {
        const busy = S.mood === 'alert' || S.mood === 'listening' || S.mood === 'angry';
        wander = { x: busy ? (Math.random() - 0.5) * 0.4 : (Math.random() * 2 - 1) * 0.9, y: (Math.random() * 2 - 1) * 0.5, next: 0.8 + Math.random() * 2.4 };
        if (Math.random() < 0.3) wander.x = wander.y = 0;
      }
      tx = wander.x; ty = M.lookY + wander.y * (S.mood === 'thinking' ? 0.3 : 1);
      if (S.mood === 'thinking') tx = 0.55 + wander.x * 0.2;
    }
    if (G.look) [tx, ty] = G.look;
    const gk = 1 - Math.exp(-dt * (S.look === 'cursor' ? 10 : 14));
    cur.lookX = lerp(cur.lookX, Math.max(-1, Math.min(1, tx)), gk);
    cur.lookY = lerp(cur.lookY, Math.max(-1, Math.min(1, ty)), gk);

    // blink
    const bpm = S.mood === 'sleepy' ? Math.max(S.blinkRate, 6) * 0.6 : S.blinkRate;
    if (bpm > 0 && blink.t < 0) { nextBlink -= dt; if (nextBlink <= 0) { triggerBlink(); nextBlink = (60 / bpm) * (0.5 + Math.random()); if (Math.random() < 0.15) nextBlink = 0.25; } }
    let bl = 1;
    if (blink.t >= 0) { const dur = S.mood === 'sleepy' ? 0.5 : 0.16; blink.t += dt / dur; bl = blinkCurve(blink.t); if (blink.t > 1) blink.t = -1; }

    eyes.forEach((e, i) => {
      const s = i ? 1 : -1, b = blink.eye === -1 || blink.eye === i ? bl : 1;
      const open = Math.max(0.06, cur.open * (1 - s * cur.asym) * b * G.open);
      e.scale.y = EYE.sy * open;
      e.scale.x = EYE.sx * (1 + (1 - Math.min(1, open)) * 0.15);
      e.rotation.z = -s * cur.eyeRot;
      const dz = cur.dizzy * 0.013, ang = t * 7 + i * Math.PI;
      e.position.x = s * EYE.x + cur.lookX * 0.018 + Math.cos(ang) * dz;
      e.position.y = EYE.y + cur.lookY * 0.014 + (S.mood === 'happy' || S.mood === 'love' ? 0.004 : 0) + Math.sin(ang) * dz;
    });

    // mouth
    let sh = cur.smileH, sw = cur.smileW;
    const talkOn = S.mood === 'talking' || talk.say || ext.speaking;
    if (talkOn) { sh = Math.min(sh, 0.55); }
    if (G.mouth) [sw, sh] = G.mouth;
    const tv = G.vis || (ext.speaking ? extVis() : talkOn ? talkStep(dt) : CLOSED);
    const km = 1 - Math.exp(-dt * 30);
    mc.o = lerp(mc.o, tv.o * S.mouthAmt, km); mc.w = lerp(mc.w, tv.w, km); mc.r = lerp(mc.r, tv.r, km);
    const useOpen = talkOn || !!G.vis || mc.o > 0.04;
    smile.visible = !useOpen;
    smile.scale.set(Math.max(0.01, sw), Math.abs(sh) < 0.01 ? 0.01 : sh, 0.5);
    smile.position.y = sh < 0 ? -0.022 * sh : 0;
    const ow = 0.024 * mc.w * (1 - 0.38 * mc.r), oh = 0.0045 + 0.021 * mc.o * (0.85 + 0.35 * mc.r);
    mOuter.visible = useOpen; mInner.visible = useOpen && oh > 0.0065; tongue.visible = useOpen && oh > 0.011;
    mOuter.scale.set(ow, oh, 1);
    mInner.scale.set(Math.max(0.0008, ow - 0.0042), Math.max(0.0005, oh - 0.0042), 1);
    tongue.scale.set(ow * 0.55, (oh - 0.0042) * 0.5, 1);
    tongue.position.y = -0.012 - (oh - 0.0042) * 0.98; tongue.rotation.z = 0;
    mouth.position.y = -0.048 + cur.lookY * 0.004;
    mouth.position.x = cur.lookX * 0.008 + (S.mood === 'confused' ? 0.012 : 0);
    mouth.rotation.z = (S.mood === 'confused' ? 0.25 : 0) * (cur.asym / 0.28);

    // head
    const on = S.headMo ? 1 : 0;
    hb.y = lerp(hb.y, on * cur.lookX * 0.12, k);
    hb.x = lerp(hb.x, on * (-cur.lookY * 0.06 + (S.mood === 'sleepy' ? 0.08 + Math.sin(t * 0.5) * 0.03 : 0)), k);
    const wob = cur.dizzy * on;
    head.rotation.x = hb.x + G.hx + Math.sin(t * 2.2) * 0.05 * wob;
    head.rotation.y = hb.y + G.hy;
    head.rotation.z = on * (cur.headTilt + Math.sin(t * 0.6) * 0.015 + (S.mood === 'happy' ? Math.sin(phase * 0.5) * 0.04 : 0)) + Math.cos(t * 2.2) * 0.07 * wob + G.hz;
    head.position.y = NECK_Y + G.py + on * mc.o * 0.0025;
    head.rotation.x += on * mc.o * 0.025;

    // arms
    const manual = S.armMode === 'manual';
    const tg = manual ? S.armPose.map(d => d * Math.PI / 180) : (ARMS[S.mood] || ARMS.idle).slice();
    const br = Math.sin(t * 1.3) * 0.025;
    tg[0] += br; tg[1] += br;
    if (!manual) {
    if (S.mood === 'happy') { tg[0] += Math.sin(t * 11) * 0.22; tg[1] += Math.sin(t * 11 + 0.6) * 0.22; }
    if (S.mood === 'dizzy') { tg[0] += Math.sin(t * 2.2) * 0.3; tg[1] += Math.sin(t * 2.2 + 1.5) * 0.3; }
    if (S.mood === 'angry') { tg[2] += Math.sin(t * 30) * 0.04; tg[3] += Math.cos(t * 30) * 0.04; }
    if (S.mood === 'thinking') tg[3] += Math.sin(t * 3) * 0.05;
    if (talkOn) { tg[3] += 0.35 + Math.sin(t * 2.3) * 0.3 + mc.o * 0.15; tg[2] += 0.2 + Math.sin(t * 1.7 + 2) * 0.2; tg[1] += 0.12 + mc.o * 0.12; tg[0] += 0.08; }
    }
    const fin = G.arm ? G.arm.map((v, i) => Math.max(v, tg[i])) : tg;
    const ka = 1 - Math.exp(-dt * (G.arm ? 18 : 7));
    for (let i = 0; i < 4; i++) armC[i] = lerp(armC[i], fin[i], ka);
    arms[0].rotation.z = -(0.16 + armC[0]); arms[1].rotation.z = 0.16 + armC[1];
    arms[0].rotation.x = -armC[2]; arms[1].rotation.x = -armC[3];

    // lights
    glow.color.copy(col); glow.emissive.copy(col); earGlow.color.copy(col); earGlow.emissive.copy(col);
    glow.emissiveIntensity = 1.1 * cur.glow * G.glow;
    let tipI = 1.4;
    switch (S.mood) {
      case 'talking': break;
      case 'thinking': tipI = 0.7 + 0.9 * (0.5 + 0.5 * Math.sin(t * 5)); break;
      case 'listening': tipI = 1.1 + 0.6 * Math.sin(t * 3); break;
      case 'alert': case 'angry': tipI = Math.sin(t * 16) > 0 ? 2.4 : 0.35; break;
      case 'happy': case 'love': tipI = 1.6 + 0.5 * Math.sin(t * 8); break;
      case 'sleepy': tipI = 0.35 + 0.15 * Math.sin(t * 1.2); break;
    }
    if (talkOn) tipI = 0.6 + mc.o * 2;
    if (S.mood === 'listening') tipI = 0.9 + ext.level * 2.5;
    if (S.tip) { tipGlow.color.copy(col); tipGlow.emissive.copy(col); tipGlow.emissiveIntensity = tipI * G.glow * G.ear; }
    else { tipGlow.color.set(0xf3f1ec); tipGlow.emissiveIntensity = 0; }
    earGlow.emissiveIntensity = (S.earMo ? cur.ear * (0.7 + 0.3 * Math.sin(t * cur.earHz * Math.PI * 2)) : 1.1) * G.ear * cur.glow;
    if (S.mood === 'listening') earGlow.emissiveIntensity *= 1 + ext.level * 1.5;
    controls.update();
    renderer.render(scene, camera);
    if (!disposed && !paused) raf = requestAnimationFrame(frame);
  }
  let disposed = false, paused = false, raf = requestAnimationFrame(frame);



  // ---------- external audio drive ----------
  function extVis() {
    const bd = ext.bands && ext.bands();
    if (!bd || !bd.length) return CLOSED;
    let lo = 0, hi = 0;
    for (let i = 0; i < bd.length; i++) (i < 3 ? (lo += bd[i]) : (hi += bd[i]));
    const amp = (lo + hi) / bd.length, cen = hi / (lo + hi + 1e-4);
    return { o: Math.max(0, Math.min(1, (amp - 0.06) * 2.6)), w: 0.8 + cen * 0.6, r: Math.max(0, 1 - cen * 2.2) };
  }

  // ---------- camera framing / resize ----------
  const box = new THREE.Box3().setFromObject(g), size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
  function fit() {
    const w = container.clientWidth || 1, h = container.clientHeight || 1;
    renderer.setSize(w, h, false); camera.aspect = w / h;
    const half = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const dist = Math.max(size.y / 2 / half, size.x / 2 / (half * camera.aspect)) * 1.25 + size.z / 2;
    const dir = camera.position.clone().sub(controls.target);
    if (dir.lengthSq() < 1e-6) dir.set(0.35, 0.18, 1);
    camera.position.copy(center).add(dir.normalize().multiplyScalar(dist));
    controls.target.copy(center); controls.minDistance = dist * 0.4; controls.maxDistance = dist * 3;
    camera.updateProjectionMatrix();
  }
  fit();
  const ro = new ResizeObserver(fit); ro.observe(container);

  // ---------- options ----------
  let flashTimer = 0, baseMood = S.mood;
  const mats = { shell, trim, screen: glass };
  function setOptions(p = {}) {
    for (const k in p) {
      const v = p[k];
      if (v === undefined) continue;
      if (k === 'mood') api.setMood(v);
      else if (k === 'armPose' && Array.isArray(v)) S.armPose = v.slice(0, 4).map(Number);
      else if (k === 'colors' && v) {
        for (const c in v) { if (mats[c]) mats[c].color.set(v[c]); }
        if (v.glow) BLUE = new THREE.Color(v.glow).getHex();
      } else if (k === 'background') scene.background = v ? new THREE.Color(v) : null;
      else if (k === 'orbit') controls.enabled = !!v;
      else if (k === 'shadow') ground.visible = key.castShadow = !!v;
      else if (KEY[k]) S[KEY[k]] = v;
    }
  }

  const api = {
    setOptions,
    setMood(m) { if (!MOOD_NAMES.includes(m)) return; baseMood = m; if (!flashTimer) { S.mood = m; wander.next = 0; } },
    /** Show a mood briefly, then return to the one set by setMood. */
    flash(m, ms = 1800) {
      if (!MOOD_NAMES.includes(m)) return;
      clearTimeout(flashTimer); S.mood = m; wander.next = 0;
      flashTimer = setTimeout(() => { flashTimer = 0; S.mood = baseMood; }, ms);
    },
    play(name) { if (GESTURE_NAMES.includes(name)) play(name); },
    blink: () => triggerBlink(),
    wink: () => triggerBlink(1),
    say: (text) => say(text),
    stopSay: () => stopSay(),
    /** Drive the mouth from real audio: bandsFn() returns an array of 0..1 band levels (e.g. 8 FFT bands). */
    setSpeaking(on, bandsFn = null) { ext.speaking = !!on; ext.bands = on ? bandsFn : null; },
    /** Mic input level 0..1 — makes the antenna/ears react while listening. */
    setLevel(l) { ext.level = Math.max(0, Math.min(1, l || 0)); },
    setArmPose(pose) { const p = typeof pose === 'string' ? ARM_POSES[pose] : pose; if (p) { S.armMode = 'manual'; S.armPose = p.slice(); } },
    get state() { return { ...S }; },
    /** Stop rendering while hidden (e.g. behind the workshop) and resume later. */
    setPaused(on) {
      if (disposed || paused === !!on) return;
      paused = !!on;
      if (paused) cancelAnimationFrame(raf);
      else { last = performance.now(); raf = requestAnimationFrame(frame); }
    },
    object: g, scene, camera, renderer,
    dispose() {
      disposed = true; cancelAnimationFrame(raf); ro.disconnect(); clearTimeout(flashTimer);
      removeEventListener('pointermove', onMove); stopSay(); controls.dispose();
      scene.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) [].concat(o.material).forEach(m => m.dispose()); });
      renderer.dispose(); renderer.domElement.remove();
    },
  };
  setOptions(opts);
  if (opts.boot) play('boot');
  return api;
}
