import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { createLook, hologramize } from "./hologram.js";

// The hologram stage: a projector disc on a grid floor, a cone of light,
// slow rings and drifting dust, and the model floating above it all -- then
// bloom, which is what really sells the effect.
//
// The model hangs from a pivot you turn directly (rotateBy / rollBy /
// scaleBy / moveBy) rather than an orbiting camera, so a mouse drag and a
// pinch-and-drag go through exactly the same calls. Same shape as
// robotEngine.js: a mount element, its own frame loop, a ResizeObserver,
// dispose().

const LIFT = 0.95;  // how high the model floats above the floor

export function createStage(container, opts = {}) {
  const look = createLook({ color: opts.color });
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.setClearColor(0x02070d, 1);
  container.append(renderer.domElement);
  renderer.domElement.className = "ws-canvas";

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x02070d, 0.09);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 60);
  const HOME = new THREE.Vector3(0, 1.25, 3.6);
  camera.position.copy(HOME);
  const aim = new THREE.Vector3(0, LIFT * 0.92, 0);
  camera.lookAt(aim);

  const deco = [];  // everything tinted with the hologram colour
  const tint = (m) => { deco.push(m); return m; };

  // ---- floor: grid, projector disc and its light cone ----
  const grid = new THREE.GridHelper(24, 60);
  grid.material = tint(new THREE.LineBasicMaterial({ transparent: true, opacity: 0.08, depthWrite: false }));
  scene.add(grid);
  const polar = new THREE.PolarGridHelper(1.4, 24, 6, 96);
  polar.material = tint(new THREE.LineBasicMaterial({ transparent: true, opacity: 0.28, depthWrite: false, blending: THREE.AdditiveBlending }));
  polar.position.y = 0.002;
  scene.add(polar);

  const disc = new THREE.Mesh(new THREE.RingGeometry(0.62, 0.7, 96),
    tint(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide })));
  disc.rotation.x = -Math.PI / 2; disc.position.y = 0.004;
  scene.add(disc);

  const coneMat = new THREE.ShaderMaterial({
    uniforms: { uColor: look.uColor, uTime: look.uTime },
    vertexShader: `varying float vH; void main(){ vH = uv.y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }`,
    fragmentShader: `uniform vec3 uColor; uniform float uTime; varying float vH;
      void main(){ float a = pow(1. - vH, 2.6) * 0.05 * (0.85 + 0.15 * sin(uTime * 3.)); gl_FragColor = vec4(uColor, a); }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
  });
  const cone = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 0.66, LIFT * 1.9, 64, 1, true), coneMat);
  cone.position.y = LIFT * 0.95;
  scene.add(cone);

  // ---- rings with tick marks, turning slowly around the model ----
  const rings = new THREE.Group();
  rings.position.y = LIFT;
  scene.add(rings);
  function ring(r, ticks, tilt, speed) {
    const pts = [];
    for (let i = 0; i < ticks; i++) {
      const a0 = (i / ticks) * Math.PI * 2, a1 = a0 + (Math.PI * 2 / ticks) * (i % 4 === 0 ? 0.75 : 0.35);
      pts.push(new THREE.Vector3(Math.cos(a0) * r, 0, Math.sin(a0) * r), new THREE.Vector3(Math.cos(a1) * r, 0, Math.sin(a1) * r));
    }
    const line = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
      tint(new THREE.LineBasicMaterial({ transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false })));
    line.rotation.x = tilt;
    line.userData.speed = speed;
    rings.add(line);
  }
  ring(1.05, 96, Math.PI / 2 - 0.12, 0.08);
  ring(1.18, 48, Math.PI / 2 + 0.3, -0.05);
  ring(0.92, 160, 0.25, 0.12);

  // ---- drifting dust ----
  const N = 700, dust = new Float32Array(N * 3), speeds = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const r = Math.sqrt(Math.random()) * 2.6, a = Math.random() * Math.PI * 2;
    dust.set([Math.cos(a) * r, Math.random() * 2.6, Math.sin(a) * r], i * 3);
    speeds[i] = 0.02 + Math.random() * 0.06;
  }
  const dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute("position", new THREE.BufferAttribute(dust, 3));
  const points = new THREE.Points(dustGeo, tint(new THREE.PointsMaterial({
    size: 0.012, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false,
  })));
  scene.add(points);

  // ---- the model's pivot ----
  const pivot = new THREE.Group();
  pivot.position.y = LIFT;
  scene.add(pivot);
  let holo = null;  // hologramize() result for the model on show
  let explodeT = 0, explodeTo = 0;
  const spin = { on: false, v: new THREE.Vector2() };  // v = inertia (rad/s)
  let scale = 1, scaleTo = 1;

  // ---- post ----
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  // Threshold above the faint fills: only rims, lines and highlights glow.
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), opts.bloom ?? 0.9, 0.4, 0.12);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  function setColor(hex) {
    look.uColor.value.set(hex);
    for (const m of deco) m.color?.set(hex);
    holo?.recolor();
  }
  setColor(opts.color ?? "#38d6ff");

  // ---- resize ----
  function fit() {
    const w = container.clientWidth || 1, h = container.clientHeight || 1;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    camera.aspect = w / h;
    // Keep the model a similar size on narrow screens.
    camera.position.copy(HOME).multiplyScalar(camera.aspect < 1 ? 1 + (1 - camera.aspect) * 0.9 : 1);
    camera.lookAt(aim);
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(fit);
  ro.observe(container);
  fit();

  // ---- frame loop ----
  const timer = new THREE.Timer();
  let raf = 0, paused = false, disposed = false;
  const frameHooks = new Set();
  const yAxis = new THREE.Vector3(0, 1, 0), xAxis = new THREE.Vector3(1, 0, 0), q = new THREE.Quaternion();

  function turn(dx, dy) {
    // Yaw about the world's up, pitch about the camera's right: it feels
    // like grabbing the object itself, whichever way it already faces.
    q.setFromAxisAngle(yAxis, dx); pivot.quaternion.premultiply(q);
    xAxis.set(1, 0, 0).applyQuaternion(camera.quaternion);
    q.setFromAxisAngle(xAxis, dy); pivot.quaternion.premultiply(q);
  }

  function frame(now) {
    if (disposed || paused) return;
    raf = requestAnimationFrame(frame);
    timer.update(now);
    const dt = Math.min(timer.getDelta(), 0.05);
    const t = (look.uTime.value += dt);
    for (const r of rings.children) r.rotation.z += r.userData.speed * dt;
    for (let i = 0; i < N; i++) {
      let y = dust[i * 3 + 1] + speeds[i] * dt;
      if (y > 2.6) y = 0;
      dust[i * 3 + 1] = y;
    }
    dustGeo.attributes.position.needsUpdate = true;
    disc.material.opacity = 0.75 + 0.2 * Math.sin(t * 2.2);

    // Inertia after a flick, else a gentle auto-spin.
    if (spin.v.lengthSq() > 1e-6) {
      turn(spin.v.x * dt, spin.v.y * dt);
      spin.v.multiplyScalar(Math.pow(0.08, dt));
    } else if (spin.on) {
      turn(0.35 * dt, 0);
    }
    scale += (scaleTo - scale) * (1 - Math.exp(-dt * 10));
    pivot.scale.setScalar(scale);
    if (holo && Math.abs(explodeTo - explodeT) > 1e-3) {
      explodeT += (explodeTo - explodeT) * (1 - Math.exp(-dt * 5));
      holo.explode(explodeT);
    }
    for (const fn of frameHooks) fn(dt);
    composer.render(dt);
  }
  raf = requestAnimationFrame(frame);

  // ---- picking and projection ----
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  function pick(clientX, clientY) {
    if (!holo) return null;
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(holo.meshes, false)[0];
    return hit ? { part: hit.object.userData.part, point: hit.point } : null;
  }
  const v = new THREE.Vector3();
  function project(world) {
    v.copy(world).project(camera);
    const r = renderer.domElement.getBoundingClientRect();
    return { x: r.left + (v.x + 1) / 2 * r.width, y: r.top + (1 - v.y) / 2 * r.height, visible: v.z < 1 };
  }

  const api = {
    scene, camera, renderer, look,
    get holo() { return holo; },
    get exploded() { return explodeTo > 0.5; },
    /** Put a normalised model on the projector (replacing the last one). */
    show(object) {
      api.clear();
      holo = hologramize(object, look);
      pivot.add(object);
      pivot.quaternion.identity();
      explodeT = explodeTo = 0;
      scale = 0.2; scaleTo = 1;  // grows in
      spin.v.set(0, 0);
      return holo;
    },
    clear() {
      if (!holo) return;
      pivot.remove(holo.root);
      holo.dispose();
      holo = null;
    },
    rotateBy(dx, dy) { turn(dx, dy); },
    rollBy(a) { q.setFromAxisAngle(new THREE.Vector3(0, 0, 1).applyQuaternion(camera.quaternion), a); pivot.quaternion.premultiply(q); },
    fling(vx, vy) { spin.v.set(vx, vy); },
    scaleBy(f) { scaleTo = THREE.MathUtils.clamp(scaleTo * f, 0.25, 4); scale = scaleTo; },
    zoom(f) { scaleTo = THREE.MathUtils.clamp(scaleTo * f, 0.25, 4); },
    /** Move in the screen plane, in pixels. */
    moveBy(px, py) {
      const h = renderer.domElement.clientHeight || 1;
      const k = (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.position.distanceTo(pivot.position)) / h;
      const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
      const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
      pivot.position.addScaledVector(right, px * k).addScaledVector(up, -py * k);
    },
    setSpin(on) { spin.on = !!on; },
    get spinning() { return spin.on; },
    explode(on) { explodeTo = on ? 1 : 0; },
    reset() {
      pivot.quaternion.identity(); pivot.position.set(0, LIFT, 0);
      scaleTo = 1; spin.v.set(0, 0); explodeTo = 0;
    },
    setColor,
    setBloom(s) { bloom.strength = s; },
    setGlitch(on) { look.uGlitch.value = on ? 1 : 0; look.uFlicker.value = on ? 0.35 : 0; },
    pick, project,
    onFrame(fn) { frameHooks.add(fn); return () => frameHooks.delete(fn); },
    setPaused(on) {
      if (disposed || paused === !!on) return;
      paused = !!on;
      if (paused) cancelAnimationFrame(raf);
      else { timer.reset?.(); raf = requestAnimationFrame(frame); }
    },
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      ro.disconnect();
      api.clear();
      scene.traverse((o) => { o.geometry?.dispose(); [].concat(o.material || []).forEach((m) => m.dispose()); });
      composer.dispose?.();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
  return api;
}
