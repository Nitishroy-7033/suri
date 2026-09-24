// The server's per-process token (backend/core/security.py). It arrives in
// the "ready" message; routes that read files or change the machine refuse
// requests without it. A restart makes a new one and the reconnect's "ready"
// brings it, so nothing here needs to persist.

let token = "";

export const setToken = (t) => { token = t || ""; };
export const getToken = () => token;

/** fetch() with the token header. Throws on a non-2xx answer: the message
 *  is "<status> <body>"; `status` and `detail` (the server's reason, in
 *  words) are on the error too. */
export async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...opts.headers, "X-Jarvis-Token": token } });
  if (!res.ok) {
    const body = (await res.text().catch(() => "")).slice(0, 200);
    const err = new Error(`${res.status} ${body}`);
    err.status = res.status;
    try { err.detail = JSON.parse(body).detail; } catch {}
    if (typeof err.detail !== "string") err.detail = res.status === 401 ? "not connected to Jarvis yet" : body || `HTTP ${res.status}`;
    throw err;
  }
  return res;
}

/** For URLs the browser fetches itself (<img>, <video>, <iframe>): the token
 *  goes in the query, since those cannot send headers. */
export function withToken(url) {
  const u = new URL(url, location.href);
  u.searchParams.set("token", token);
  return u.pathname + u.search;
}
