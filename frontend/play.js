import {
  $, $$, CLOCK_ID, api, boxingHandLabel, boxingHandSide, boxingLabel,
  clampNumber, connectWS, esc, fmtMs, fmtPct, fmtSigned, gradeWord,
  loadMode, saveMode, toast,
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
  mode: loadMode(),
  stance: localStorage.getItem("s2.stance") === "southpaw" ? "southpaw" : "orthodox",
  box: {
    cue: null,
    cuePainted: false,
    timers: [],
    countTimer: null,
    edgeTimer: null,
    windowMs: 1400,
    combo: 0,
    xp: 0,
    accuracy: null,
    session: null,
    mascotState: "idle",
    mascotOk: null,
    reportOpen: false,
    report: null,
  },
};

const video = $("#video");
const canvas = $("#canvas");
const ctx = canvas.getContext("2d", { alpha: false });
const GLOVE_SVG = $("#boxMascot")?.innerHTML || "";
let ws = null;

const RACE_GATE_COPY =
  "Your phone is the sensor and the scoreboard. Prop it up at eye level, step back so your upper body is in frame, then race the lights.";
const BOX_GATE_COPY =
  "Your phone is the cue board and the camera. Prop it up at eye level, step back so your upper body is in frame, then shadow-box the cue. Nobody punches the screen.";

const SVG_CHECK = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const SVG_CROSS = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>`;
const SVG_STAR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.6l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5-5.8-3-5.8 3 1.1-6.5L2.6 9.4l6.5-.9z"/></svg>`;
const SVG_CHEST = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8.5C3 6.6 4.6 5 6.5 5h11C19.4 5 21 6.6 21 8.5V10H3V8.5zM3 12h8v6H6.5A3.5 3.5 0 0 1 3 14.5V12zm10 0h8v2.5a3.5 3.5 0 0 1-3.5 3.5H13v-6zm-1-5.2a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/></svg>`;

/* ------------------------------------------------------------------ */
/* boot                                                               */
/* ------------------------------------------------------------------ */
async function boot() {
  applyMode(S.mode, { persist: false });
  probeMascot();

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
      if (msg.state?.mode && msg.state.phase && msg.state.phase !== "idle") {
        applyMode(msg.state.mode);
      }
      maybeJoinRoom(msg);
      break;
    case "state":
      onState(msg);
      break;
    case "schedule":
      if (msg.mode === "boxing") {
        applyMode("boxing");
        runBoxCue(msg);
      } else {
        applyMode("racing");
        runLights(msg);
      }
      break;
    case "result":
      if (msg.mode === "boxing") {
        applyMode("boxing");
        onBoxResult(msg);
      } else {
        applyMode("racing");
        onResult(msg);
      }
      break;
    case "session_end":
      if (msg.report) {
        applyMode("boxing");
        onSessionEnd(msg);
      }
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
/* mode                                                               */
/* ------------------------------------------------------------------ */
function applyMode(mode, { persist = true } = {}) {
  if (mode !== "racing" && mode !== "boxing") return;
  const changed = mode !== S.mode;
  S.mode = mode;
  if (persist) saveMode(mode);
  document.body.classList.toggle("boxing-mode", mode === "boxing");

  $$(".mode-toggle [data-mode]").forEach((btn) => {
    const on = btn.dataset.mode === mode;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", String(on));
  });

  $("#gateCopy").textContent = mode === "boxing" ? BOX_GATE_COPY : RACE_GATE_COPY;

  if (changed) {
    if (mode === "boxing") {
      clearTimers();
      setLightsOff();
      clearResult();
      hideActions();
      status("READY");
      boxIdle("READY", "WAITING FOR CONTROLLER");
    } else {
      clearBoxCue();
      hideBoxReport();
      hideBoxVerdict();
      hideReward();
      setEdge("");
      status("READY");
    }
  }

  if (S.cameraOn) {
    $("#boxApp").classList.toggle("hidden", mode !== "boxing");
    if (mode === "boxing") boxIdle("READY", "WAITING FOR CONTROLLER");
  }
}

$$(".mode-toggle [data-mode]").forEach((btn) => {
  btn.addEventListener("click", () => applyMode(btn.dataset.mode));
});

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
    $("#boxApp").classList.toggle("hidden", S.mode !== "boxing");
    if (S.mode === "boxing") boxIdle("READY", "WAITING FOR CONTROLLER");
    $("#fullBtn").style.display = document.documentElement.requestFullscreen ? "" : "none";
    await lockScreen();
    requestFullscreen();
    requestFrame();
    ws?.send({ type: "ready", camera: true });
    probeMascot();
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
  // Boxing always streams at ~30 fps: the laptop owns pose and timing.
  // Racing keeps the original idle throttle.
  const interval = S.mode === "boxing" ? 33 : (!S.active || S.phase === "idle") ? 260 : 33;
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
/* state + racing lights                                              */
/* ------------------------------------------------------------------ */
function onState(msg) {
  const prev = S.phase;
  S.phase = msg.phase || "idle";
  S.state = msg;
  if (msg.room) { S.room = msg.room; renderRoomPill(); }
  if (msg.player) { S.player = msg.player; renderHud(msg.player); }
  if (typeof msg.player_id === "string") S.activePlayerId = msg.player_id;

  if (msg.mode === "boxing" || msg.mode === "racing") {
    if (msg.mode !== S.mode) applyMode(msg.mode);
    else document.body.classList.toggle("boxing-mode", msg.mode === "boxing");
  }

  if ((msg.mode || S.mode) === "boxing") {
    onBoxState(msg, prev);
    return;
  }

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

function onBoxState(msg, prev) {
  if (msg.player) renderHud(msg.player);
  if (S.phase === "arm") {
    if (prev !== "arm") {
      hideBoxReport();
      resetBoxSessionView();
    }
    boxIdle("GET SET", "HANDS UP");
  } else if (S.phase === "wait") {
    if (!S.box.cuePainted) boxIdle("READY", "WATCH THE LINE");
  } else if (S.phase === "response") {
    if (!S.box.cuePainted) boxIdle("CUE", "WAIT FOR THE CUE");
  } else if (S.phase === "idle") {
    if (!S.box.reportOpen) boxIdle("READY", "WAITING FOR CONTROLLER");
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
/* boxing cue + result                                                */
/* ------------------------------------------------------------------ */
function runBoxCue(msg) {
  clearBoxCue();
  hideBoxReport();
  if (Number(msg.rep) === 1) resetBoxSessionView();
  S.box.cue = { ...(msg.cue || {}), rep: msg.rep };
  S.box.windowMs = clampNumber(msg.window_ms, 400, 5000, 1400);
  const delay = clampNumber(msg.delay_ms, 0, 5000, 650);
  S.box.cuePainted = false;
  hideBoxVerdict();
  hideReward();
  setEdge("");
  boxIdle("READY", "WATCH THE LINE");

  S.box.timers.push(setTimeout(paintBoxCue, delay));
}

function paintBoxCue() {
  const cue = S.box.cue;
  if (!cue) return;
  const label = cue.label || boxingLabel(cue.action);
  const side = boxingHandSide(cue.hand, S.stance);
  const hand = boxingHandLabel(cue.hand, S.stance);
  const word = $("#boxCueWord");
  word.textContent = label;
  word.classList.remove("idle", "correct", "wrong", "early");
  word.classList.toggle("long", label.length > 6);

  const handEl = $("#boxCueHand");
  handEl.textContent = hand || "MATCH THE CUE";
  handEl.classList.remove("idle");

  const arrow = $("#boxArrowWrap");
  arrow.dataset.side = side;
  arrow.classList.add("show");
  setRings(side);

  // Ack immediately after the paint, matching the racing contract.
  const tPaint = performance.now();
  ws?.send({ type: "painted", ts: tPaint, clock: CLOCK_ID });
  S.box.cuePainted = true;

  const bar = $("#boxCountBar");
  bar.classList.remove("run");
  bar.style.animationDuration = `${S.box.windowMs}ms`;
  void bar.offsetWidth;
  bar.classList.add("run");
  startCountdown(S.box.windowMs);
  navigator.vibrate?.(12);
}

function setRings(side) {
  const active = side === "left" || side === "right" ? side : "center";
  $$(".box-ring").forEach((ring) => {
    ring.classList.toggle("on", ring.dataset.side === active);
  });
}

function startCountdown(ms) {
  clearInterval(S.box.countTimer);
  const end = performance.now() + ms;
  const el = $("#boxCountdown");
  el.textContent = `${(ms / 1000).toFixed(1)}s`;
  S.box.countTimer = setInterval(() => {
    const left = Math.max(0, end - performance.now());
    el.textContent = `${(left / 1000).toFixed(1)}s`;
    if (left <= 0) {
      clearInterval(S.box.countTimer);
      S.box.countTimer = null;
    }
  }, 100);
}

function boxIdle(word, hand) {
  const w = $("#boxCueWord");
  w.textContent = word || "READY";
  w.classList.remove("correct", "wrong", "early", "long");
  w.classList.add("idle");
  const h = $("#boxCueHand");
  h.textContent = hand || "";
  h.classList.add("idle");
  $("#boxArrowWrap").classList.remove("show");
  $$(".box-ring").forEach((ring) => ring.classList.remove("on"));
  $("#boxCountdown").textContent = "--";
  $("#boxCountBar").classList.remove("run");
}

function clearBoxCue() {
  S.box.timers.forEach(clearTimeout);
  S.box.timers = [];
  if (S.box.countTimer) {
    clearInterval(S.box.countTimer);
    S.box.countTimer = null;
  }
  S.box.cue = null;
  S.box.cuePainted = false;
  $("#boxCountBar").classList.remove("run");
  $("#boxArrowWrap").classList.remove("show");
  $$(".box-ring").forEach((ring) => ring.classList.remove("on"));
}

function onBoxResult(msg) {
  if (msg.player_id && PLAYER_ID && msg.player_id !== PLAYER_ID) return;
  if (S.box.reportOpen) hideBoxReport();

  const early = isEarly(msg);
  const cue = S.box.cue || {};
  const expected = boxingLabel(msg.expected || cue.action);
  const detected = early
    ? "EARLY"
    : msg.detected
      ? boxingLabel(msg.detected)
      : msg.correct
        ? expected
        : "—";

  if (S.box.countTimer) {
    clearInterval(S.box.countTimer);
    S.box.countTimer = null;
  }
  $("#boxCountBar").classList.remove("run");

  const word = $("#boxCueWord");
  word.textContent = expected;
  word.classList.remove("idle", "wrong", "early", "correct");
  word.classList.toggle("long", expected.length > 6);
  word.classList.add(msg.correct ? "correct" : early ? "early" : "wrong");

  const handEl = $("#boxCueHand");
  handEl.textContent = cue.hand ? boxingHandLabel(cue.hand, S.stance) : "MATCH THE CUE";
  handEl.classList.remove("idle");

  flashEdge(msg.correct ? "ok" : early ? "early" : "bad");
  navigator.vibrate?.(msg.correct ? 55 : [40, 50, 40]);

  S.box.combo = msg.combo != null ? Number(msg.combo) : (msg.correct ? S.box.combo + 1 : 0);
  S.box.xp += Number(msg.xp?.total || 0);
  if (msg.session?.accuracy != null) S.box.accuracy = msg.session.accuracy;
  S.box.session = msg.session || S.box.session;
  setCombo(S.box.combo);
  setXp(S.box.xp);
  setAccuracy(S.box.accuracy);
  setMascot(msg.mascot);
  renderReward(msg.stars, msg.chest);

  $("#vExpected").textContent = `EXPECTED ${expected}`;
  $("#vDetected").textContent = `DETECTED ${detected}`;
  $("#vMark").innerHTML = msg.correct ? SVG_CHECK : SVG_CROSS;
  const verdict = $("#boxVerdict");
  verdict.className = `box-verdict ${msg.correct ? "ok" : early ? "early" : "bad"}`;
  verdict.classList.remove("hidden");

  const bits = [];
  if (msg.reaction_ms != null) bits.push(`${fmtMs(msg.reaction_ms)} ms`);
  if (msg.execution_ms != null) bits.push(`exec ${fmtMs(msg.execution_ms)} ms`);
  if (msg.xp?.total) bits.push(`+${msg.xp.total} XP`);
  if (msg.ghost_delta != null) bits.push(`${fmtSigned(msg.ghost_delta)} vs ${msg.ghost?.name || "ghost"}`);
  if (msg.session) {
    bits.push(`rep ${msg.session.index}/${msg.session.total}`);
    if (msg.session.accuracy != null) bits.push(fmtPct(msg.session.accuracy));
  }
  $("#boxLast").textContent = bits.join(" · ") || "Cue scored.";
  S.phase = "result";
}

function isEarly(msg) {
  const detected = String(msg?.detected ?? "").toLowerCase();
  return msg?.false_start === true || /false|early|anticip/.test(detected);
}

function flashEdge(kind) {
  if (!kind) return;
  const el = $("#edge");
  clearTimeout(S.box.edgeTimer);
  el.className = "box-edge";
  void el.offsetWidth;
  el.classList.add("show", kind);
  S.box.edgeTimer = setTimeout(() => {
    el.classList.remove("show", kind);
    el.className = "box-edge";
  }, 320);
}

function setEdge(kind) {
  clearTimeout(S.box.edgeTimer);
  S.box.edgeTimer = null;
  const el = $("#edge");
  el.className = kind ? `box-edge show ${kind}` : "box-edge";
}

function setCombo(n) {
  const value = Math.max(0, Number(n) || 0);
  S.box.combo = value;
  $("#boxCombo").textContent = String(value);
  $$("#boxComboDots i").forEach((el, i) => {
    el.classList.toggle("on", i < Math.min(value, 5));
  });
}

function setXp(n) {
  const value = Math.max(0, Number(n) || 0);
  S.box.xp = value;
  $("#boxXp").textContent = `+${value}`;
  // The set target is 100 XP; the bar is a session motivator, not a level.
  $("#boxXpBar").style.width = `${Math.min(100, value)}%`;
}

function setAccuracy(value) {
  S.box.accuracy = value;
  $("#boxAcc").textContent = fmtPct(value);
}

function resetBoxSessionView() {
  setCombo(0);
  setXp(0);
  setAccuracy(null);
  S.box.session = null;
  hideBoxVerdict();
  hideReward();
  $("#boxLast").textContent = "Camera preview is live. The controller starts the set.";
}

function hideBoxVerdict() {
  const el = $("#boxVerdict");
  el.className = "box-verdict hidden";
  el.classList.add("hidden");
  $("#vMark").innerHTML = "";
}

function renderReward(stars, chest) {
  const reward = $("#boxReward");
  const starWrap = $("#boxStars");
  const chestEl = $("#boxChest");
  let any = false;

  if (stars !== null && stars !== undefined) {
    starWrap.innerHTML = starsHtml(stars);
    any = true;
  } else {
    starWrap.innerHTML = "";
  }

  const chestText = chestLabel(chest);
  if (chestText) {
    chestEl.innerHTML = `${SVG_CHEST}<span>${esc(chestText)}</span>`;
    chestEl.classList.remove("hidden");
    any = true;
  } else {
    chestEl.innerHTML = "";
    chestEl.classList.add("hidden");
  }

  reward.classList.toggle("hidden", !any);
}

function hideReward() {
  const reward = $("#boxReward");
  reward.classList.add("hidden");
  $("#boxStars").innerHTML = "";
  $("#boxChest").innerHTML = "";
  $("#boxChest").classList.add("hidden");
}

/* ------------------------------------------------------------------ */
/* session report                                                     */
/* ------------------------------------------------------------------ */
function onSessionEnd(msg) {
  const report = msg.report || {};
  S.box.report = report;
  S.box.reportOpen = true;
  clearBoxCue();
  setMascot(msg.mascot || "happy");

  $("#reportAccuracy").textContent = fmtPct(report.accuracy);
  $("#reportStars").innerHTML = starsHtml(msg.stars);
  const chest = chestLabel(msg.chest);
  $("#reportChest").innerHTML = chest ? `${SVG_CHEST}<span>${esc(chest)}</span>` : "";
  $("#reportBias").textContent = formatLeadRear(report.lead_rear);
  $("#reportFatigue").textContent = formatFatigue(report.fatigue);
  $("#reportGuard").textContent = report.guard_drops ?? "--";
  $("#reportBreaks").textContent = report.combo_breaks ?? "--";

  const per = Array.isArray(report.per_action) ? report.per_action : [];
  $("#reportActions").innerHTML = per.length
    ? per.map((a) => `
      <div class="report-action-row">
        <span class="ra-name">${esc(boxingLabel(a.action))}</span>
        <span class="ra-acc">${fmtPct(a.accuracy)}</span>
        <span class="ra-rt">${a.median_rt != null ? `${fmtMs(a.median_rt)} ms` : "--"}</span>
      </div>`).join("")
    : `<div class="box-empty">No per-action data.</div>`;

  const ghost = ghostLabel(msg.ghost);
  const ghostEl = $("#reportGhost");
  ghostEl.classList.toggle("hidden", !ghost);
  ghostEl.innerHTML = ghost ? `<span>Ghost</span><b>${esc(ghost)}</b>` : "";

  if (msg.report.accuracy != null) setAccuracy(msg.report.accuracy);
  const setXpEl = $("#boxXp");
  if (setXpEl) setXpEl.textContent = `+${S.box.xp}`;

  const el = $("#boxReport");
  el.classList.remove("hidden");
  el.scrollTop = 0;
}

function hideBoxReport() {
  S.box.reportOpen = false;
  $("#boxReport").classList.add("hidden");
}

$("#reportDone").onclick = () => {
  hideBoxReport();
  boxIdle("READY", "WAITING FOR CONTROLLER");
};

$("#reportNext").onclick = () => {
  hideBoxReport();
  resetBoxSessionView();
  const payload = {
    cmd: "start",
    player_id: PLAYER_ID,
    mode: "boxing",
    stance: S.stance,
  };
  const sent = ws?.send(payload);
  toast(sent ? "Next set requested" : "Offline - reconnect first");
};

/* ------------------------------------------------------------------ */
/* racing result                                                      */
/* ------------------------------------------------------------------ */
function onResult(msg) {
  clearTimers();
  setLightsOff();
  hideBoxReport();
  hideBoxVerdict();
  setEdge("");

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
  const level = player.level?.level ?? player.level ?? 1;
  $("#hudName").textContent = player.name || "Athlete";
  $("#hudLevel").textContent = level;
  $("#hudStreak").textContent = player.streak ?? 0;
  $("#boxName").textContent = player.name || "Athlete";
  $("#boxLevel").textContent = `LVL ${level}`;
  $("#boxStreak").textContent = player.streak ?? 0;
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
/* mascot                                                             */
/* ------------------------------------------------------------------ */
async function probeMascot() {
  if (S.box.mascotOk !== null) return;
  try {
    const res = await fetch("/static/assets/mascot-idle.svg", {
      method: "HEAD",
      cache: "no-store",
    });
    S.box.mascotOk = res.ok;
  } catch {
    S.box.mascotOk = false;
  }
  setMascot(S.box.mascotState);
}

function setMascot(state) {
  const mood = ["idle", "happy", "sad", "fire"].includes(state) ? state : "idle";
  S.box.mascotState = mood;
  const el = $("#boxMascot");
  if (!el) return;
  el.dataset.mood = mood;
  if (!S.box.mascotOk) {
    if (!el.querySelector("svg")) el.innerHTML = GLOVE_SVG;
    return;
  }
  el.innerHTML = `<img src="/static/assets/mascot-${mood}.svg" alt="" width="82" height="82">`;
  const img = el.querySelector("img");
  img?.addEventListener("error", () => {
    S.box.mascotOk = false;
    el.dataset.mood = mood;
    el.innerHTML = GLOVE_SVG;
  });
}

/* ------------------------------------------------------------------ */
/* formatting helpers                                                 */
/* ------------------------------------------------------------------ */
function starsHtml(stars) {
  const n = clampNumber(stars, 0, 5, 0);
  return Array.from({ length: 5 }, (_, i) =>
    SVG_STAR.replace("<svg", `<svg class="${i < n ? "on" : ""}"`)
  ).join("");
}

function chestLabel(chest) {
  if (chest === null || chest === undefined || chest === false) return "";
  if (typeof chest === "string") return chest;
  if (typeof chest === "boolean") return chest ? "Chest" : "";
  if (typeof chest === "object") {
    return String(chest.name || chest.label || chest.id || chest.rarity || "") || "Chest";
  }
  return String(chest);
}

function ghostLabel(ghost) {
  if (!ghost) return "";
  if (typeof ghost === "string") return ghost;
  if (typeof ghost === "number") return `${fmtMs(ghost)} ms`;
  const name = ghost.name || "Ghost";
  const rt = ghost.rt_ms ?? ghost.rt ?? ghost.reaction_ms;
  return rt != null ? `${name} · ${fmtMs(rt)} ms` : name;
}

function formatLeadRear(value) {
  if (value === null || value === undefined) return "--";
  if (typeof value === "number") return `delta ${fmtSigned(value)}`;
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    const lead = value.lead_ms ?? value.lead ?? value.l;
    const rear = value.rear_ms ?? value.rear ?? value.r;
    const delta = value.delta_ms ?? value.delta ?? value.bias_ms;
    if (lead != null || rear != null) {
      return `L ${lead != null ? fmtMs(lead) : "--"} · R ${rear != null ? fmtMs(rear) : "--"}${delta != null ? ` · ${fmtSigned(delta)}` : ""}`;
    }
    if (delta != null) return `delta ${fmtSigned(delta)}`;
    const bits = Object.entries(value).map(([k, v]) =>
      `${k.replace(/_/g, " ")} ${typeof v === "number" ? fmtSigned(v) : v}`
    );
    return bits.join(" · ") || "--";
  }
  return String(value);
}

function formatFatigue(value) {
  if (value === null || value === undefined) return "--";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    const n = Math.abs(value) <= 1 ? value * 100 : value;
    return `${n > 0 ? "+" : ""}${Math.round(n)}%`;
  }
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    const n = value.pct ?? value.percent ?? value.slowing_pct ?? value.delta_pct ?? value.value;
    if (n != null && Number.isFinite(Number(n))) {
      const pct = Math.abs(Number(n)) <= 1 ? Number(n) * 100 : Number(n);
      return `${pct > 0 ? "+" : ""}${Math.round(pct)}%`;
    }
    const bits = Object.entries(value).map(([k, v]) =>
      `${k.replace(/_/g, " ")} ${typeof v === "number" ? fmtSigned(v) : v}`
    );
    return bits.join(" · ") || "--";
  }
  return String(value);
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
