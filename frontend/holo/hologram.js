import * as THREE from "three";

// Turns any loaded model into a hologram: a see-through shell that glows at
// its silhouette (fresnel), bands of light running up it (scan lines), a
// little flicker and the odd sideways glitch, plus clean outlines from
// EdgesGeometry (wireframe:true would draw every triangle and look like a
// mess). Every mesh becomes a "part" you can point at, highlight and pull
// out in an exploded view.

const VERT = /* glsl */ `
uniform float uTime;
uniform float uGlitch;
varying vec3 vNormalW;
varying vec3 vPosW;
void main() {
  vec4 w = modelMatrix * vec4(position, 1.0);
  // "Signal unstable": now and then a thin band of the model slips sideways.
  float row = floor(w.y * 14.0);
  float tick = floor(uTime * 9.0);
  float slip = step(0.992, fract(sin(row * 12.9898 + tick * 78.233) * 43758.5453));
  w.x += slip * uGlitch * 0.05;
  vPosW = w.xyz;
  vNormalW = normalize(mat3(modelMatrix) * normal);
  gl_Position = projectionMatrix * viewMatrix * w;
}`;

const FRAG = /* glsl */ `
uniform vec3 uColor;
uniform vec3 uHot;
uniform float uTime;
uniform float uOpacity;
uniform float uFresnelPow;
uniform float uScanFreq;
uniform float uFlicker;
uniform float uHighlight;
uniform float uDim;
varying vec3 vNormalW;
varying vec3 vPosW;
void main() {
  vec3 V = normalize(cameraPosition - vPosW);
  float facing = abs(dot(normalize(vNormalW), V));
  float rim = pow(1.0 - facing, uFresnelPow);
  float scan = pow(0.5 + 0.5 * sin(vPosW.y * uScanFreq - uTime * 2.4), 8.0);
  float fine = 0.5 + 0.5 * sin(vPosW.y * uScanFreq * 7.0 + uTime * 6.0);
  float flick = 1.0 - uFlicker * step(0.965, fract(sin(floor(uTime * 24.0) * 91.7) * 4375.5));
  vec3 base = mix(uColor, uHot, uHighlight);
  // Back faces show through (it is a hologram) but fainter, or a part with
  // many layers piles up into a white blob under additive blending.
  float side = gl_FrontFacing ? 1.0 : 0.35;
  float a = (0.025 + rim * 0.5 + scan * 0.12 + fine * 0.015) * uOpacity * flick * side;
  a *= mix(1.0, 0.25, uDim);
  a = mix(a, max(a, 0.22 + rim * 0.4), uHighlight);
  vec3 col = base * (0.35 + rim * 1.05 + scan * 0.55);
  gl_FragColor = vec4(col, clamp(a, 0.0, 1.0));
}`;

/** Uniforms every part shares, so one change recolours the whole model. */
export function createLook({ color = "#38d6ff", hot = "#ffb454" } = {}) {
  return {
    uTime: { value: 0 },
    uColor: { value: new THREE.Color(color) },
    uHot: { value: new THREE.Color(hot) },
    uOpacity: { value: 1 },
    uFresnelPow: { value: 2.2 },
    uScanFreq: { value: 9 },
    uFlicker: { value: 0.35 },
    uGlitch: { value: 1 },
  };
}

// The shell blends normally, so a stack of overlapping parts settles at the
// hologram colour instead of burning white; the outlines add up and glow.
function shellMaterial(look) {
  return new THREE.ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG,
    uniforms: { ...look, uHighlight: { value: 0 }, uDim: { value: 0 } },
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
  });
}

function edgeMaterial(look) {
  return new THREE.LineBasicMaterial({
    color: look.uColor.value.clone(), transparent: true, opacity: 0.75,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
}

// CAD exports name things "Piston_123-844_0_Parts_1" or just "body_24":
// keep the words that mean something ("Piston", "Spring Link"), drop part
// numbers and filler. Nothing left means the part has no real name.
const GENERIC = /^(body|parts?|mesh|object|node|primitive|group|geometry|shape|solid|polysurface|default|scene|root)$/i;
function cleanName(s) {
  const words = String(s || "").replace(/([a-z])([A-Z])/g, "$1 $2").split(/[\s_\-.:]+/)
    .filter((w) => w && !/\d/.test(w) && !GENERIC.test(w));
  const t = words.join(" ");
  return t ? t[0].toUpperCase() + t.slice(1) : "";
}

function partName(mesh) {
  for (let o = mesh; o; o = o.parent) {
    const n = cleanName(o.name);
    if (n) return n;
  }
  return "";
}

const MAX_EDGE_TRIS = 400_000;  // beyond this, outlines cost more than they add

/**
 * Replace a model's materials with the hologram look.
 * `root` must already be centred and scaled (loader.js does that).
 */
export function hologramize(root, look) {
  const meshes = [];
  root.traverse((o) => { if (o.isMesh && o.geometry) meshes.push(o); });
  const tris = meshes.reduce((n, m) => n + (m.geometry.index ? m.geometry.index.count : m.geometry.attributes.position.count) / 3, 0);
  const withEdges = tris <= MAX_EDGE_TRIS;

  const parts = new Map();  // name -> [{mesh, edges}]
  const old = new Set();
  let unnamed = 0;
  for (const mesh of meshes) {
    [].concat(mesh.material).forEach((m) => m && old.add(m));
    mesh.material = shellMaterial(look);
    mesh.castShadow = mesh.receiveShadow = false;
    let edges = null;
    if (withEdges) {
      edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 28), edgeMaterial(look));
      edges.raycast = () => {};  // pointing hits the shell, not its outline
      mesh.add(edges);
    }
    // Unnamed parts are numbered so each can still be pointed at. Named
    // repeats get "Bolt 2", "Bolt 3"... on small models; on big ones they
    // stay one group, so "the bolts" lights them all.
    let name = partName(mesh);
    if (!name) name = `Part ${++unnamed}`;
    else if (parts.has(name) && meshes.length <= 60) {
      let i = 2;
      while (parts.has(`${name} ${i}`)) i++;
      name = `${name} ${i}`;
    }
    mesh.userData.part = name;
    if (!parts.has(name)) parts.set(name, []);
    parts.get(name).push({ mesh, edges });
  }
  // The originals (and their textures) are never drawn again.
  for (const m of old) {
    for (const v of Object.values(m)) if (v?.isTexture) v.dispose();
    m.dispose();
  }

  // Exploded view: each part slides away from the model's centre, further
  // the further out it already sits. Offsets are worked out once, in each
  // part's parent space, so animating is just a lerp.
  root.updateMatrixWorld(true);
  const centre = new THREE.Box3().setFromObject(root).getCenter(new THREE.Vector3());
  const tmp = new THREE.Box3(), c = new THREE.Vector3(), a = new THREE.Vector3(), b = new THREE.Vector3();
  for (const mesh of meshes) {
    tmp.setFromObject(mesh).getCenter(c);
    const dir = c.clone().sub(centre);
    const len = dir.length();
    if (len < 1e-4) dir.set(0, 1, 0).multiplyScalar(0.05); else dir.multiplyScalar(0.9 + 0.6 / (len + 0.3));
    a.copy(c); b.copy(c).add(dir);
    mesh.parent.worldToLocal(a); mesh.parent.worldToLocal(b);
    mesh.userData.home = mesh.position.clone();
    mesh.userData.away = b.sub(a).clone();
  }

  let highlighted = null;
  function each(fn) { for (const [name, list] of parts) for (const p of list) fn(p, name); }

  return {
    root, meshes, parts,
    names: () => [...parts.keys()],
    get count() { return parts.size; },
    get highlighted() { return highlighted; },
    /** 0 = assembled, 1 = fully apart. */
    explode(t) {
      for (const m of meshes) m.position.copy(m.userData.home).addScaledVector(m.userData.away, t);
    },
    /** Best match for a spoken or clicked name; null clears. Returns the name used. */
    highlight(name) {
      highlighted = name ? findPart([...parts.keys()], name) : null;
      each(({ mesh, edges }, n) => {
        const on = highlighted && n === highlighted;
        mesh.material.uniforms.uHighlight.value = on ? 1 : 0;
        mesh.material.uniforms.uDim.value = highlighted && !on ? 1 : 0;
        if (edges) {
          edges.material.color.copy(on ? look.uHot.value : look.uColor.value);
          edges.material.opacity = highlighted && !on ? 0.18 : on ? 1 : 0.75;
        }
      });
      return highlighted;
    },
    /** Where a part is, for its label. */
    centreOf(name, out = new THREE.Vector3()) {
      const list = parts.get(name);
      if (!list) return null;
      const box = new THREE.Box3();
      for (const { mesh } of list) box.expandByObject(mesh);
      return box.getCenter(out);
    },
    recolor() {
      each(({ edges }, n) => {
        if (edges) edges.material.color.copy(n === highlighted ? look.uHot.value : look.uColor.value);
      });
    },
    dispose() {
      each(({ mesh, edges }) => {
        mesh.material.dispose();
        mesh.geometry.dispose();
        if (edges) { edges.geometry.dispose(); edges.material.dispose(); }
      });
    },
  };
}

/** "the piston" -> "Piston 2"? Exact, then contains, then shared words. */
export function findPart(names, query) {
  const q = String(query).toLowerCase().replace(/\b(the|a|an|part)\b/g, "").trim();
  if (!q) return null;
  const lower = names.map((n) => n.toLowerCase());
  let i = lower.indexOf(q);
  if (i < 0) i = lower.findIndex((n) => n.includes(q));
  if (i < 0) i = lower.findIndex((n) => q.includes(n) && n.length > 2);
  if (i < 0) {
    const words = q.split(/\s+/).filter((w) => w.length > 2);
    i = lower.findIndex((n) => words.some((w) => n.includes(w) || n.includes(w.replace(/s$/, ""))));
  }
  return i < 0 ? null : names[i];
}
