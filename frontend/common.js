/* Shared browser helpers. No build step, no dependencies. */

export const CLOCK_ID = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random())).slice(0, 8);
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export function fmtMs(v, dash = "--") {
  return v === null || v === undefined || Number.isNaN(Number(v)) ? dash : String(Math.round(Number(v)));
}

export function gradeWord(g) {
  return { G: "Clean", Y: "Solid", R: "Slow", F: "False start", M: "Missed" }[g] || "Result";
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

let toastTimer = null;
export function toast(message, ms = 2400) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
}

export function param(name) {
  return new URLSearchParams(location.search).get(name);
}

export function wsUrl({ role, playerId, room }) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = new URLSearchParams({ role });
  if (playerId) q.set("player_id", playerId);
  if (room) q.set("room", room);
  return `${proto}://${location.host}/ws?${q.toString()}`;
}

/**
 * Reconnecting websocket. Returns a controller with send()/close() and
 * an `onStatus` callback so the UI can show a banner instead of silently
 * freezing.
 */
export function connectWS({ role, playerId, room, onMessage, onStatus }) {
  let ws = null;
  let retry = 0;
  let stopped = false;
  let timer = null;

  const api = {
    send(obj) {
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      try { ws.send(JSON.stringify(obj)); return true; } catch { return false; }
    },
    close() {
      stopped = true;
      clearTimeout(timer);
      try { ws?.close(); } catch {}
    },
    get socket() { return ws; },
    get buffered() { return ws ? ws.bufferedAmount : 0; },
    get open() { return ws?.readyState === WebSocket.OPEN; },
  };

  function open() {
    if (stopped) return;
    onStatus?.("connecting");
    ws = new WebSocket(wsUrl({ role, playerId, room }));
    ws.onopen = () => {
      retry = 0;
      onStatus?.("open");
    };
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      onMessage?.(msg);
    };
    ws.onclose = () => {
      onStatus?.("closed");
      if (stopped) return;
      retry += 1;
      const wait = Math.min(6000, 300 * 2 ** retry);
      timer = setTimeout(open, wait);
    };
    ws.onerror = () => {
      onStatus?.("error");
      try { ws.close(); } catch {}
    };
  }

  open();
  return api;
}

export function renderRefs(refs = []) {
  if (!refs.length) return "";
  return `<div class="tip-refs">${refs.map((r) =>
    `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)} · ${esc(r.publisher)}, ${esc(r.year)}</a>`
  ).join("")}</div>`;
}

export function beep(freq = 880, ms = 50) {
  // Used only for the result, never for the stimulus.
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = freq;
    gain.gain.value = 0.03;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    setTimeout(() => { osc.stop(); ctx.close(); }, ms);
  } catch {}
}
