import { createGestureEngine, BONES } from "./gestures.js";

// Hand control: the webcam, MediaPipe's GestureRecognizer (21 landmarks per
// hand plus a few named gestures, ~30 fps on a laptop, all in the browser),
// the rule engine in gestures.js, and a skeleton drawn over the screen so
// you can see what the camera sees.
//
// Each tracked hand becomes a virtual pointer (pointer.js), so everything a
// mouse can do in the workshop, a hand can too.
//
// The camera is only on while hand control is: stop() releases it and its
// light goes off. Nothing leaves the machine -- MediaPipe runs locally in
// WebAssembly; only its code and model file are downloaded, once.

const VERSION = "1.0.1";
const WASM = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${VERSION}/wasm`;
const MODEL = "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task";

let recognizerPromise = null;  // one per page; loading it takes a few seconds

async function recognizer() {
  recognizerPromise ??= (async () => {
    const { FilesetResolver, GestureRecognizer } = await import("@mediapipe/tasks-vision");
    const files = await FilesetResolver.forVisionTasks(WASM);
    const make = (delegate) => GestureRecognizer.createFromOptions(files, {
      baseOptions: { modelAssetPath: MODEL, delegate },
      runningMode: "VIDEO", numHands: 2,
      minHandDetectionConfidence: 0.6, minHandPresenceConfidence: 0.6, minTrackingConfidence: 0.5,
    });
    try { return await make("GPU"); } catch (err) {
      console.warn("GPU hand tracking unavailable, using the CPU", err);
      return make("CPU");
    }
  })();
  recognizerPromise.catch(() => { recognizerPromise = null; });
  return recognizerPromise;
}

function cameraError(err) {
  switch (err?.name) {
    case "NotAllowedError": return "Camera permission was refused. Allow the camera for this page to use hand control.";
    case "NotFoundError": case "OverconstrainedError": return "No camera found.";
    case "NotReadableError": return "The camera is busy in another app. Close it, or set CAMERA_ENABLED=false if Jarvis's own camera is on.";
    default: return `The camera didn't start: ${err?.message || err}`;
  }
}

export function createHands({ video, overlay, pointers, prefs, onHands, onGesture, onTrack }) {
  const engine = createGestureEngine({ smoothing: prefs().smoothing });
  const ctx2d = overlay.getContext("2d");
  let stream = null, running = false, frameReq = 0, lastTs = -1, known = new Set();

  function sizeOverlay() {
    const dpr = Math.min(devicePixelRatio, 2);
    const w = overlay.clientWidth, h = overlay.clientHeight;
    if (overlay.width !== Math.round(w * dpr) || overlay.height !== Math.round(h * dpr)) {
      overlay.width = Math.round(w * dpr); overlay.height = Math.round(h * dpr);
    }
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function draw(hands) {
    sizeOverlay();
    ctx2d.clearRect(0, 0, overlay.clientWidth, overlay.clientHeight);
    const color = getComputedStyle(overlay).getPropertyValue("--h").trim() || "#38d6ff";
    for (const h of hands) {
      if (!h.visible) continue;
      if (prefs().showSkeleton && h.joints) {
        ctx2d.globalAlpha = h.tracked ? 0.5 : 0.2;
        ctx2d.strokeStyle = color; ctx2d.lineWidth = 1.5;
        ctx2d.beginPath();
        for (const [a, b] of BONES) { ctx2d.moveTo(...h.joints[a]); ctx2d.lineTo(...h.joints[b]); }
        ctx2d.stroke();
        ctx2d.fillStyle = color;
        for (const [x, y] of h.joints) { ctx2d.beginPath(); ctx2d.arc(x, y, 2.2, 0, Math.PI * 2); ctx2d.fill(); }
      }
      // The cursor: a ring, filled while pinching, with a dwell arc while
      // pointing at something and a thumbs-up arc while confirming.
      ctx2d.globalAlpha = 1;
      const grab = h.pinch || h.fist;
      ctx2d.strokeStyle = grab ? "#ffb454" : color;
      ctx2d.lineWidth = 2;
      ctx2d.shadowColor = ctx2d.strokeStyle; ctx2d.shadowBlur = 12;
      ctx2d.beginPath(); ctx2d.arc(h.x, h.y, grab ? 9 : 14, 0, Math.PI * 2); ctx2d.stroke();
      if (grab) { ctx2d.fillStyle = "rgba(255,180,84,.35)"; ctx2d.fill(); }
      const dwell = pointers.dwell(h.id);
      const arc = dwell || h.thumbHeld;
      if (arc > 0) {
        ctx2d.strokeStyle = h.thumbHeld ? "#5cffc8" : color; ctx2d.lineWidth = 3;
        ctx2d.beginPath(); ctx2d.arc(h.x, h.y, 20, -Math.PI / 2, -Math.PI / 2 + arc * Math.PI * 2); ctx2d.stroke();
      }
      ctx2d.shadowBlur = 0;
    }
  }

  function onFrame(now) {
    if (!running) return;
    frameReq = video.requestVideoFrameCallback(onFrame);
    const ts = performance.now();
    if (ts <= lastTs) return;  // MediaPipe wants strictly increasing timestamps
    lastTs = ts;
    let result;
    try { result = rec.recognizeForVideo(video, ts); } catch (err) { console.warn(err); return; }
    const frame = {
      width: overlay.clientWidth, height: overlay.clientHeight,
      aspect: (video.videoWidth || 4) / (video.videoHeight || 3),
      hands: (result.landmarks || []).map((lm, i) => ({
        landmarks: lm,
        handedness: result.handedness?.[i]?.[0]?.categoryName,
        gesture: result.gestures?.[i]?.[0] && { name: result.gestures[i][0].categoryName, score: result.gestures[i][0].score },
      })),
    };
    const { hands, events } = engine.update(frame, ts);
    for (const h of hands) {
      pointers.hand(h.id, h);
      if (h.visible) known.add(h.id); else known.delete(h.id);
    }
    for (const e of events) {
      if (e === "palm") pointers.releaseAll();
      onGesture?.(e);
    }
    draw(hands);
    onTrack?.(hands);
    onHands?.(hands.filter((h) => h.visible).length);
  }

  let rec = null;
  return {
    get running() { return running; },
    async start() {
      if (running) return;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 },
                   ...(prefs().camDevice ? { deviceId: { exact: prefs().camDevice } } : { facingMode: "user" }) },
          audio: false,
        });
      } catch (err) {
        throw new Error(cameraError(err));
      }
      video.srcObject = stream;
      await video.play();
      video.hidden = !prefs().showCam;
      try {
        rec = await recognizer();
      } catch (err) {
        this.stop();
        throw new Error(`hand tracking didn't load: ${err.message}`);
      }
      running = true;
      lastTs = -1;
      engine.reset();
      frameReq = video.requestVideoFrameCallback(onFrame);
    },
    stop() {
      running = false;
      if (frameReq) video.cancelVideoFrameCallback?.(frameReq);
      frameReq = 0;
      for (const id of known) pointers.hand(id, { visible: false });
      known.clear();
      stream?.getTracks().forEach((t) => t.stop());
      stream = null;
      video.srcObject = null;
      video.hidden = true;
      sizeOverlay();
      ctx2d.clearRect(0, 0, overlay.clientWidth, overlay.clientHeight);
      onHands?.(0);
    },
    applyPrefs() {
      engine.set({ smoothing: prefs().smoothing });
      if (running) video.hidden = !prefs().showCam;
    },
  };
}
