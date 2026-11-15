import {
  $, $$, CLOCK_ID, api, connectWS, esc, fmtMs, gradeWord, toast,
} from "./common.js";

const params = new URLSearchParams(location.search);
const ROOM_PARAM = (params.get("room") || "").toUpperCase();
let PLAYER_ID = params.get("player") || localStorage.getItem("s2.player") || null;

const S = {
  phase: "idle",
  state: {},
  room: null,
  player: null,
  cameraOn: false,
  joinedRoom: false,
  running: false,
  active: true,
  timers: [],
  frames: 0,
  bytes: 0,
  fps: 0,
  lastSent: 0,
  lastFpsAt: 0,
  fpsCount: 0,
  wakeLock: null,
};

const video = $("#video");
const canvas = $("#canvas");
const ctx = canvas.getContext("2d", { alpha: false });
let ws = null;

/* ------------------------------------------------------------------ */
/* boot                                                               */
/* ------------------------------------------------------------------ */
async function boot() {
  try {
    const players = await api("/api/players");
    if (!PLAYER_ID || !players.some((p) => p.id === PLAYER_ID)) {
      if (players.length) PLAYER_ID = players[0].id;
      else {
        const created = await api("/api/players", {
          method: "POST", body: JSON.stringify({ name: "Guest" }),
        });
        PLAYER_ID = created.id;
      }
    }
    localStorage.setItem("s2.player", PLAYER_ID);
    const profile = await api(`/api/players/${PLAYER_ID}`);
    S.player = profile.player;
    renderHud(S.player);
    $("#gateHint").textContent =
      `${S.player.name}${ROOM_PARAM ? " · room " + ROOM_PARAM : ""} — camera needs HTTPS. Keep this page open.`;
    refreshBoard();
  } catch (err) {
    showBanner(`Cannot reach the server: ${err.message}`);
  }

  ws = connectWS({
    role: "player",
    playerId: PLAYER_ID,
    room: ROOM_PARAM || undefined,
    onStatus,
    onMessage: handle,
  });
}

function onStatus(status) {
  const dot = $("#connDot");
  const text = $("#connText");
  if (status === "open") {
    dot.className = "dot ok";
    text.textContent = "live";
    if (S.cameraOn) ws.send({ type: "ready", camera: true });
  } else if (status === "connecting") {
    dot.className = "dot warn";
    text.textContent = "connecting";
  } else {
    dot.className = "dot bad";
    text.textContent = "offline";
  }
}

function handle(msg) {
  switch (msg.type) {
    case "hello":
      if (msg.player) { S.player = msg.player; renderHud(msg.player); }
      if (msg.state?.room) { S.room = msg.state.room; renderRoomPill(); }
      if (msg.config) S.config = msg.config;
      maybeJoinRoom(msg);
      break;
    case "state":
      onState(msg);
      break;
    case "schedule":
      runLights(msg);
      break;
    case "result":
      onResult(msg);
      break;
    case "room":
    case "room_done":
      S.room = msg.room;
      renderRoomPill();
      if (msg.type === "room_done") onRoomDone(msg.room);
      break;
    case "fault":
      showBanner(msg.message || "Fault");
      break;
    case "presence":
      break;
    default:
      break;
  }
}

function maybeJoinRoom(msg) {
  if (!ROOM_PARAM || S.joinedRoom) return;
  const room = msg.state?.room;
  if (!room || room.code !== ROOM_PARAM) return;
  if (room.players?.some((p) => p.id === PLAYER_ID)) { S.joinedRoom = true; return; }
  S.joinedRoom = true;
  ws.send({ cmd: "join", room: ROOM_PARAM, player_id: PLAYER_ID });
}

/* ------------------------------------------------------------------ */
/* camera                                                             */
/* ------------------------------------------------------------------ */
$("#startCam").onclick = async () => {
  const btn = $("#startCam");
  btn.disabled = true;
  btn.textContent = "Starting camera…";
  try {
    if (!window.isSecureContext) {
      throw new Error("Camera blocked: this page is not HTTPS. Open the https:// address and trust the CA (see /cam).");
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("This browser has no getUserMedia. Use Safari on iOS 15+.");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: "user",
        width: { ideal: 640 },
        height: { ideal: 480 },
        frameRate: { ideal: 30, max: 30 },
      },
    });
    video.srcObject = stream;
    await video.play();
    S.cameraOn = true;
    S.running = true;
    $("#gate").classList.add("hidden");
    $("#app").classList.remove("hidden");
    $("#fullBtn").style.display = document.documentElement.requestFullscreen ? "" : "none";
    await lockScreen();
    requestFullscreen();
    requestFrame();
    ws?.send({ type: "ready", camera: true });
    toast("Camera live");
  } catch (err) {
    showBanner(err.message || String(err), true);
    btn.disabled = false;
    btn.textContent = "Try camera again";
  }
};

function requestFullscreen() {
  try {

    document.documentElement.requestFullscreen?.().catch(() => {});
  } catch {}
}

async function lockScreen() {
  try {
    if (!navigator.wakeLock) return;
    S.wakeLock = await navigator.wakeLock.request("screen");
    S.wakeLock.addEventListener("release", () => { S.wakeLock = null; });
  } catch {}
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && S.cameraOn) {
    lockScreen();
    S.running = true;
    requestFrame();
  } else if (document.visibilityState === "hidden") {
    S.running = false;
  }
});

function requestFrame() {
  if (!S.running) return;
  if (video.requestVideoFrameCallback) {
    video.requestVideoFrameCallback(onFrame);
  } else {
    setTimeout(() => onFrame(performance.now()), 1000 / 30);
  }
}

function onFrame(now) {
  if (!S.running) return;
  const interval = (!S.active || S.phase === "idle") ? 260 : 33;
  if (now - S.lastSent >= interval) {
    sendFrame(now);
    S.lastSent = now;
  }
  updateFps(now);
  requestFrame();
}

function sendFrame(now) {
  if (!video.videoWidth || video.readyState < 2) return;
  if (!ws?.open) return;
  if (ws.buffered > 1_400_000) return;

  const targetW = 640;
  const targetH = Math.round(video.videoHeight * (targetW / video.videoWidth));
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW;
    canvas.height = targetH;
  }
  try {
    ctx.drawImage(video, 0, 0, targetW, targetH);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.62);
    const comma = dataUrl.indexOf(",");
    if (comma < 0) return;
    const jpeg = dataUrl.slice(comma + 1);
    const ok = ws.send({ type: "frame", ts: now, clock: CLOCK_ID, jpeg });
    if (ok) {
      S.frames += 1;
      S.bytes += jpeg.length;
      S.fpsCount += 1;
    }
  } catch {}
}

function updateFps(now) {
  if (!S.lastFpsAt) S.lastFpsAt = now;
  if (now - S.lastFpsAt >= 1000) {
    S.fps = S.fpsCount;
    S.fpsCount = 0;
    S.lastFpsAt = now;
    $("#camStats").textContent =
      `${canvas.width || 640}p · ${S.fps} fps · ${S.frames} frames · ${(S.bytes / 1024 / 1024).toFixed(1)} MB`;
  }
}

/* ------------------------------------------------------------------ */
/* state + lights                                                     */
/* ------------------------------------------------------------------ */
function onState(msg) {
  const prev = S.phase;
  S.phase = msg.phase || "idle";
  S.state = msg;
  if (msg.room) { S.room = msg.room; renderRoomPill(); }
  if (msg.player) { S.player = msg.player; renderHud(msg.player); }
  if (typeof msg.player_id === "string") S.activePlayerId = msg.player_id;

  if (msg.room?.status === "done") {
    S.active = false;
    status(`${msg.room.winner || "MATCH"} WINS`, true);
    hideActions();
    return;
  }

  const myTurn = !msg.room || msg.room.turn === PLAYER_ID;
  S.active = myTurn;
  if (!myTurn) {
    status(`WAITING FOR ${(msg.room?.turn_name || "OPPONENT").toUpperCase()}`);
    setLightsOff();
    hideActions();
    return;
  }

  if (S.phase === "arm") {
    clearResult();
    status("STAND STILL");
    setLightsOff();
    hideActions();
  } else if (S.phase === "wait") {
    status("LIGHTS OUT SOON");
  } else if (S.phase === "response") {
    status("GO", true);
  } else if (S.phase === "idle") {
    if (prev === "result") {
      status("READY");
      showNext();
    } else {
      status("READY");
      hideActions();
    }
  }
}

function runLights(msg) {
  clearTimers();
  clearResult();
  setLightsOff();
  S.active = true;
  status("LIGHTS OUT", false);
  const lights = $$("#pLights .light");
  const n = Math.min(msg.lights || 5, lights.length);
  const delay = Math.max(600, msg.delay_ms || 3000);
  let interval = msg.light_interval_ms || 420;
  if (delay < n * interval + 200) interval = Math.max(70, (delay - 250) / n);

  for (let i = 0; i < n; i += 1) {
    S.timers.push(setTimeout(() => lights[i]?.classList.add("on"), i * interval));
  }

  S.timers.push(setTimeout(() => {
    const tPaint = performance.now();
    setLightsOff();
    status("GO", true);
    // Ack immediately: tPaint is the anchor for both channels. Do not wait
    // for rAF, or the Arduino's STIM starts late and HIT is biased low.
    ws?.send({ type: "painted", ts: tPaint, clock: CLOCK_ID });
    S.timers.push(setTimeout(() => {
      if (S.phase !== "result") status("");
    }, 480));
  }, delay));
}

function setLightsOff() {
  $$("#pLights .light").forEach((l) => l.classList.remove("on"));
}

function clearTimers() {
  S.timers.forEach(clearTimeout);
  S.timers = [];
}

function status(text, go = false) {
  const el = $("#pStatus");
  el.textContent = text;
  el.classList.toggle("go", go);
}

/* ------------------------------------------------------------------ */
/* result                                                             */
/* ------------------------------------------------------------------ */
function onResult(msg) {
  clearTimers();
  setLightsOff();

  if (msg.bot) {
    toast(`BOT ${msg.rt_ms} ms`);
    refreshBoard();
    return;
  }
  if (msg.player_id !== PLAYER_ID) return;

  S.phase = "result";
  status("");
  navigator.vibrate?.(70);

  const rt = msg.rt_ms;
  $("#pRt").innerHTML = `${rt == null ? "—" : fmtMs(rt)}<small>ms</small>`;
  $("#pRt").classList.remove("hidden");

  const grade = msg.grade || "";
  const label = msg.false_start ? "FALSE START" : msg.miss ? "MISSED" : gradeWord(grade).toUpperCase();
  $("#pGrade").textContent = label;
  $("#pGrade").className = "p-grade " + (msg.false_start ? "g-F" : msg.miss ? "g-R" : "g-" + grade);
  $("#pGrade").classList.remove("hidden");

  const bits = [];
  if (msg.best_ms != null) bits.push(`best ${msg.best_ms} ms`);
  if (msg.hit_ms != null) bits.push(`Z ${msg.hit_ms} ms`);
  if (msg.new_pb) bits.push("PERSONAL BEST");
  if (msg.ghost_delta != null) bits.push(`${msg.ghost_delta >= 0 ? "+" : ""}${msg.ghost_delta} ms vs ghost`);
  $("#pMeta").textContent = bits.join(" · ");
  $("#pMeta").classList.toggle("hidden", !bits.length);

  const xp = msg.xp || {};
  const chips = [];
  if (xp.base) chips.push(`<span class="xp-chip">base <b>${xp.base}</b></span>`);
  if (xp.speed) chips.push(`<span class="xp-chip">speed <b>${xp.speed}</b></span>`);
  if (xp.pb) chips.push(`<span class="xp-chip">PB <b>+${xp.pb}</b></span>`);
  if (xp.streak) chips.push(`<span class="xp-chip">streak <b>+${xp.streak}</b></span>`);
  if (xp.total) chips.push(`<span class="xp-chip">total <b>+${xp.total} XP</b></span>`);
  $("#pXp").innerHTML = chips.join("");
  $("#pXp").classList.toggle("hidden", !chips.length);

  if (msg.ghost_ms != null && rt != null) {
    const max = Math.max(rt, msg.ghost_ms) * 1.15;
    $("#pGhostMe").style.width = `${Math.min(100, (rt / max) * 100)}%`;
    $("#pGhostMark").style.left = `${Math.min(100, (msg.ghost_ms / max) * 100)}%`;
    $("#pGhostLabel").textContent = `you ${rt} ms`;
    $("#pGhostTarget").textContent = `ghost ${msg.ghost_ms} ms`;
    $("#pGhost").classList.remove("hidden");
  } else {
    $("#pGhost").classList.add("hidden");
  }

  const tip = msg.tip;
  if (tip) {
    const refs = (tip.refs || []).map((r) =>
      `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.publisher)} ${esc(r.year)}</a>`
    ).join("");
    $("#pTip").innerHTML = `<b>${esc(tip.headline)}</b>${esc(tip.body)}${refs ? `<div class="refs">${refs}</div>` : ""}`;
    $("#pTip").classList.remove("hidden");
  } else {
    $("#pTip").classList.add("hidden");
  }

  if (msg.player) { S.player = msg.player; renderHud(msg.player); }
  if (msg.room) { S.room = msg.room; renderRoomPill(); }
  showNext();
  refreshBoard();
}

function showNext() {
  const actions = $("#pActions");
  actions.classList.remove("hidden");
  const roomActive = S.room?.status === "active";
  const roomDone = S.room?.status === "done";
  const myTurn = !roomActive || S.room?.turn === PLAYER_ID;
  $("#nextBtn").classList.toggle("hidden", roomDone || !myTurn);
  $("#nextBtn").textContent = "NEXT REP";
  $("#againBtn").textContent = roomDone ? "DONE" : "RESET";
}

function hideActions() {
  $("#pActions").classList.add("hidden");
}

function clearResult() {
  $("#pRt").classList.add("hidden");
  $("#pGrade").classList.add("hidden");
  $("#pMeta").classList.add("hidden");
  $("#pXp").classList.add("hidden");
  $("#pGhost").classList.add("hidden");
  $("#pTip").classList.add("hidden");
}

function onRoomDone(room) {
  if (!room) return;
  const winner = room.winner;
  status(winner === S.player?.name ? "YOU WIN" : `${winner || "MATCH"} WINS`, true);
  showNext();
  $("#nextBtn").classList.add("hidden");
  clearResult();
}

/* ------------------------------------------------------------------ */
/* hud / board                                                        */
/* ------------------------------------------------------------------ */
function renderHud(player) {
  if (!player) return;
  $("#hudName").textContent = player.name || "Athlete";
  $("#hudLevel").textContent = player.level?.level ?? player.level ?? 1;
  $("#hudStreak").textContent = player.streak ?? 0;
}

function renderRoomPill() {
  const pill = $("#roomPill");
  if (!S.room) { pill.textContent = "SOLO"; return; }
  const me = S.room.players?.find((p) => p.id === PLAYER_ID);
  const reps = me ? me.reps : 0;
  const turn = S.room.turn === PLAYER_ID ? " · YOUR TURN" : "";
  pill.textContent = `${S.room.code} · ${reps}/${S.room.target}${turn}`;
}

async function refreshBoard() {
  try {
    const rows = await api("/api/leaderboard");
    const board = $("#pBoard");
    if (!rows.length) { board.innerHTML = ""; return; }
    board.innerHTML = rows.slice(0, 6).map((r) => `
      <div class="row"><b>${esc(r.name)}</b><span>${fmtMs(r.best_ms)}</span></div>
    `).join("");
  } catch {}
}

/* ------------------------------------------------------------------ */
/* actions                                                            */
/* ------------------------------------------------------------------ */
$("#nextBtn").onclick = () => {
  ws?.send({
    cmd: "start",
    player_id: PLAYER_ID,
    room: S.room?.status === "active" ? S.room.code : null,
  });
};

$("#againBtn").onclick = () => {
  clearResult();
  hideActions();
  status("READY");
};

$("#fullBtn").onclick = () => {
  requestFullscreen();
};

function showBanner(message, sticky = false) {
  const el = $("#banner");
  el.textContent = message;
  el.classList.remove("hidden");
  if (!sticky) {
    clearTimeout(showBanner._t);
    showBanner._t = setTimeout(() => el.classList.add("hidden"), 5000);
  }
}

boot();
