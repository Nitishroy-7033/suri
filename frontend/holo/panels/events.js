// Server events for the workshop's panels: file deletions waiting for your
// yes (fs_*), and the forge finding or building models (forge_*).

export function handle(ctx, msg) {
  const d = msg.data || {};
  switch (msg.kind) {
    case "fs_confirm":
      import("./confirm.js").then((m) => m.show(ctx, d));
      break;
    case "fs_confirm_done":
      import("./confirm.js").then((m) => m.done(ctx, d));
      break;
    case "forge_start": case "forge_progress": case "forge_done":
      import("./forge.js").then((m) => m.update(ctx, msg.kind, d));
      break;
  }
}
