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

/* ------------------------------------------------------------------ */
/* boxing helpers (shared by the controller and the phone)            */
/* ------------------------------------------------------------------ */
export const MODE_KEY = "s2.mode";

export function loadMode(fallback = "racing") {
  try {
    const stored = localStorage.getItem(MODE_KEY);
    if (stored === "boxing" || stored === "racing") return stored;
    return fallback === "boxing" ? "boxing" : "racing";
  } catch {
    return fallback === "boxing" ? "boxing" : "racing";
  }
}

export function saveMode(mode) {
  try { localStorage.setItem(MODE_KEY, mode === "boxing" ? "boxing" : "racing"); } catch {}
}

const BOX_LABELS = {
  jab: "JAB",
  cross: "CROSS",
  hook: "HOOK",
  hook_l: "LEFT HOOK",
  hook_r: "RIGHT HOOK",
  uppercut: "UPPERCUT",
  uppercut_l: "LEFT UPPERCUT",
  uppercut_r: "RIGHT UPPERCUT",
  slip: "SLIP",
  slip_l: "SLIP LEFT",
  slip_r: "SLIP RIGHT",
  slip_left: "SLIP L",
  slip_right: "SLIP R",
  duck: "DUCK",
  block: "BLOCK",
  none: "MISS",
  miss: "MISS",
  false_start: "FALSE START",
  early: "FALSE START",
};

export function boxingLabel(action) {
  const key = String(action ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (!key) return "—";
  if (BOX_LABELS[key]) return BOX_LABELS[key];
  return key.replace(/_/g, " ").toUpperCase();
}

/** Normalise a cue hand (lead/rear/left/right) into a screen side. */
export function boxingHandSide(hand, stance = "orthodox") {
  const h = String(hand ?? "").trim().toLowerCase();
  const lead = stance === "southpaw" ? "right" : "left";
  const rear = lead === "left" ? "right" : "left";
  if (h === "lead") return lead;
  if (h === "rear") return rear;
  if (h === "left" || h === "l") return "left";
  if (h === "right" || h === "r") return "right";
  if (h === "down" || h === "duck") return "down";
  return "center";
}

/** Short label for the cue hand, e.g. "LEAD · LEFT". */
export function boxingHandLabel(hand, stance = "orthodox", sideOverride = null) {
  const h = String(hand ?? "").trim().toLowerCase();
  const side = sideOverride || boxingHandSide(hand, stance);
  if (h === "lead" || h === "rear") return `${h.toUpperCase()} · ${side.toUpperCase()}`;
  if (h === "left" || h === "right") return `${h.toUpperCase()} HAND`;
  if (h === "down" || h === "duck") return "DUCK";
  if (h === "both" || h === "center" || h === "middle") return "GUARD";
  return "";
}

/** Sentence-case hand/zone label for the arena cue. */
export function boxingCueHand(cue = {}, stance = "orthodox") {
  const action = String(cue.action || cue.id || "").trim().toLowerCase();
  const side = cue.hand_side || boxingHandSide(cue.hand, cue.stance || stance);
  if (action.startsWith("slip")) return `Head ${side}`;
  if (action === "block") return "Guard up";
  const hand = String(cue.hand || "").trim().toLowerCase();
  if (hand === "lead" || hand === "rear") return `${hand} · ${side}`;
  if (side === "left" || side === "right") {
    const label = String(cue.label || "").toLowerCase();
    if (/_l$|_r$/.test(action) || label.startsWith("left") || label.startsWith("right")) return side;
    return `${side} hand`;
  }
  return "Match the cue";
}

export function clampNumber(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

/** Accuracy may arrive as 0..1 or 0..100; render it as a percentage. */
export function fmtPct(value, digits = 0) {
  if (value === null || value === undefined || value === "") return "--";
  let n = Number(value);
  if (!Number.isFinite(n)) return "--";
  if (n > 0 && n <= 1) n *= 100;
  return `${n.toFixed(digits)}%`;
}

export function fmtSigned(value, unit = "ms") {
  if (value === null || value === undefined || value === "") return "--";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const rounded = Math.round(n);
  return `${rounded > 0 ? "+" : ""}${rounded}${unit ? ` ${unit}` : ""}`;
}

export function median(values = []) {
  const nums = values.map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  if (!nums.length) return null;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
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
