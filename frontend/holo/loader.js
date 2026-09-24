import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";

// Loads a 3D file from a URL or a dropped File, then centres it and scales it
// to a standard size, so every model sits on the projector the same way.

export const MODEL_EXT = [".glb", ".gltf", ".stl", ".obj"];

const DRACO = "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/libs/draco/gltf/";
let gltf = null;
function gltfLoader() {
  if (!gltf) {
    const draco = new DRACOLoader().setDecoderPath(DRACO);
    gltf = new GLTFLoader().setDRACOLoader(draco).setMeshoptDecoder(MeshoptDecoder);
  }
  return gltf;
}

export const extOf = (name) => (String(name).match(/\.[a-z0-9]+(?=$|[?#])/i)?.[0] ?? "").toLowerCase();

/** { url } or { file } -> a normalised Object3D. `onProgress(0..1)` while downloading.
 *  `ext` says the type when the URL does not end in one (/api/fs/file?path=...). */
export async function loadModel({ url, file, ext: given, onProgress } = {}) {
  const ext = given || extOf(file ? file.name : url);
  if (!MODEL_EXT.includes(ext)) throw new Error(`can't open ${ext || "that"} files as 3D`);
  const src = file ? URL.createObjectURL(file) : url;
  const progress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
  try {
    let root;
    if (ext === ".glb" || ext === ".gltf") {
      root = (await gltfLoader().loadAsync(src, progress)).scene;
    } else if (ext === ".stl") {
      const geo = await new STLLoader().loadAsync(src, progress);
      geo.computeVertexNormals();
      root = new THREE.Mesh(geo);
      root.name = file?.name?.replace(/\.[^.]+$/, "") || "model";
      // STL from CAD is usually Z-up.
      root.rotation.x = -Math.PI / 2;
    } else {
      root = await new OBJLoader().loadAsync(src, progress);
    }
    return normalise(root);
  } finally {
    if (file) URL.revokeObjectURL(src);
  }
}

/** Centred on the origin, largest side 1.5 units, sitting on y = 0. */
export function normalise(root, size = 1.5) {
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) throw new Error("that file has nothing to show");
  const dims = box.getSize(new THREE.Vector3());
  const scale = size / Math.max(dims.x, dims.y, dims.z, 1e-6);
  const centre = box.getCenter(new THREE.Vector3());
  const inner = new THREE.Group();
  inner.add(root);
  root.position.sub(centre);
  const wrap = new THREE.Group();
  wrap.add(inner);
  inner.scale.setScalar(scale);
  wrap.userData.height = dims.y * scale;
  return wrap;
}
