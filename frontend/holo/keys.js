// Keyboard shortcuts, in one place: workshop.js acts on WORKSHOP_KEYS, and
// the guide (panels/guide.js) shows both lists, so they can't drift apart.
// PAGE_KEYS are app.js's, and work in the workshop too.

export const WORKSHOP_KEYS = [
  { keys: ["?"], label: "Guide: gestures, voice, shortcuts", act: "guide" },
  { keys: ["G"], label: "Hand control on / off (camera)", act: "hands" },
  { keys: ["L"], label: "Model library", act: "library" },
  { keys: ["F"], label: "Files", act: "files" },
  { keys: ["C"], label: "Chat window", act: "chat" },
  { keys: ["S"], label: "Systems gauges", act: "systems" },
  { keys: ["P"], label: "PC controls", act: "pc" },
  { keys: ["E"], label: "Explode / put back together", act: "explode" },
  { keys: ["Space"], label: "Spin on / off", act: "spin" },
  { keys: ["R"], label: "Reset the view", act: "reset" },
  { keys: ["←", "→"], label: "Previous / next model", act: "step" },
  { keys: ["+", "−"], label: "Zoom in / out", act: "zoom" },
  { keys: ["A"], label: "Arrange the windows", act: "arrange" },
  { keys: ["X"], label: "Close the front window", act: "closeWin" },
];

export const PAGE_KEYS = [
  { keys: ["H"], label: "Open / leave the workshop" },
  { keys: ["M"], label: "Microphone on / off" },
  { keys: ["T"], label: "Talk mode (no wake word)" },
  { keys: ["Esc"], label: "Stop speaking · cancel" },
  { keys: ["/"], label: "Type a message" },
  { keys: [","], label: "Jarvis settings" },
];

export const MOUSE = [
  { keys: ["Drag"], label: "Turn the model (let go mid-move to spin it)" },
  { keys: ["Right-drag"], label: "Move the model" },
  { keys: ["Wheel"], label: "Zoom" },
  { keys: ["Double-click"], label: "Reset the view" },
  { keys: ["Click a part"], label: "Select it and show its name" },
  { keys: ["Drag a title bar"], label: "Move a window (corner: resize)" },
  { keys: ["Drag a file"], label: "To the orb: ask Jarvis · to the trash: Recycle Bin · to empty space: open it" },
  { keys: ["Drop from Explorer"], label: "Open a 3D model, picture, PDF or text file" },
];

/** Which act a keydown means, or null. `dir` for step and zoom. */
export function actFor(e) {
  const k = e.key;
  if (k === "?") return { act: "guide" };
  if (k === " ") return { act: "spin" };
  if (k === "ArrowLeft" || k === "ArrowRight") return { act: "step", dir: k === "ArrowRight" ? 1 : -1 };
  if (k === "+" || k === "=") return { act: "zoom", dir: 1 };
  if (k === "-" || k === "_") return { act: "zoom", dir: -1 };
  const hit = WORKSHOP_KEYS.find((s) => s.keys.length === 1 && s.keys[0].length === 1 && s.keys[0].toLowerCase() === k.toLowerCase());
  return hit ? { act: hit.act } : null;
}
