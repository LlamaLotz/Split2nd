/* Split2nd boxing display: cues, painted ack, results, live coaching. */
import { $, CLOCK_ID, api, connectWS, esc } from "./common.js";

const LABELS = {
  jab: "JAB",
  cross: "CROSS",
  hook_l: "LEFT HOOK",
  hook_r: "RIGHT HOOK",
  uppercut_l: "LEFT UPPERCUT",
  uppercut_r: "RIGHT UPPERCUT",
  slip_l: "SLIP LEFT",
  slip_r: "SLIP RIGHT",
  block: "BLOCK",
};

const S = {
  playerId: localStorage.getItem("s2.player") || null,
  stance: localStorage.getItem("s2.stance") || "orthodox",
  ws: null,
  cue: null,
  timer: null,
  running: false,
  connected: false,
  lastTotal: 0,
};

const $cue = $("#cueLabel");
const $text = $("#cueText");
const $result = $("#result");
const $overlay = $("#overlay");
const $report = $("#report");
const $stars = $("#stars");
const $overlayTip = $("#overlayTip");

function label(id) {
  return LABELS[id] || (id ? String(id).replace(/_/g, " ").toUpperCase() : "UNKNOWN");
}

function setStanceLabel() {
  $("#stanceBtn").textContent = S.stance.toUpperCase();
}

function setStatus(text, cls = "") {
  $("#connText").textContent = text;
  $("#connDot").className = "dot " + cls;
}

function clearCueTimer() {
  if (S.timer) clearTimeout(S.timer);
  S.timer = null;
}

function hideOverlay() {
  $overlay.classList.add("hidden");
}

function showOverlay(title, body) {
  $("#overlayTitle").textContent = title;
  $("#overlayBody").textContent = body;
  $overlay.classList.remove("hidden");
}

async function boot() {
  setStanceLabel();
  try {
    let players = await api("/api/players");
    if (!players.length) {
      const created = await api("/api/players", {
        method: "POST",
        body: JSON.stringify({ name: "Athlete" }),
      });
      players = [created];
    }
    if (!S.playerId || !players.some((p) => p.id === S.playerId)) {
      S.playerId = players[0].id;
    }
    localStorage.setItem("s2.player", S.playerId);
    $("#sessionHint").textContent =
      `${players.find((p) => p.id === S.playerId)?.name || "Athlete"} · ${S.stance}`;
  } catch (err) {
    showOverlay("Server offline", err.message || String(err));
  }

  S.ws = connectWS({
    role: "controller",
    playerId: S.playerId,
    onStatus(status) {
      S.connected = status === "open";
      if (status === "open") setStatus("live", "ok");
      else if (status === "connecting") setStatus("connecting", "warn");
      else setStatus("offline", "");
    },
    onMessage: handle,
  });

  setInterval(pollHealth, 1000);
  pollHealth();
}

function pollHealth() {
  api("/api/health").then((h) => {
    const v = h.vision || {};
    const r = h.rig || {};
    $("#fps").textContent = v.fps ?? "--";
    $("#poseBackend").textContent = (v.pose_backend || "?").toUpperCase();
    const z = r.last_distance_cm;
    const zText = z == null || z < 0 ? "--" : `${z} cm`;
    $("#zLive").textContent = zText;
    $("#zCard").textContent = zText;
    if (!S.connected) {
      const ok = !!r.connected;
      setStatus(r.backend || "offline", ok ? "ok" : "");
    }
  }).catch(() => {});
}

function handle(msg) {
  switch (msg.type) {
    case "hello":
      if (msg.player) S.playerId = msg.player.id;
      if (msg.state?.mode === "boxing") onState(msg.state);
      break;
    case "state":
      onState(msg);
      break;
    case "schedule":
      if (msg.mode === "boxing" && msg.cue) runCue(msg);
      break;
    case "result":
      if (msg.mode === "boxing") showResult(msg);
      break;
    case "session_end":
      if (msg.mode === "boxing") showReport(msg);
      break;
    case "fault":
      S.running = false;
      showOverlay("Rig fault", msg.message || "Unknown fault");
      break;
    default:
      break;
  }
}

function onState(msg) {
  if (msg.mode !== "boxing") return;
  if (msg.stance) S.stance = msg.stance;
  setStanceLabel();
  if (msg.progress) {
    $("#progress").textContent = `${msg.progress.done}/${msg.progress.total}`;
    $("#accuracy").textContent =
      msg.progress.done ? `${Math.round(msg.progress.accuracy * 100)}%` : "--";
  }
  if (msg.phase === "idle" && S.running) {
    S.running = false;
    $("#startBtn").textContent = "START";
  }
  if (msg.error) $("#sessionHint").textContent = msg.error;
}

function runCue(msg) {
  clearCueTimer();
  S.cue = msg.cue;
  S.running = true;
  S.lastTotal = msg.progress?.done ?? 0;
  $("#startBtn").textContent = "RUNNING";
  $("#eyebrow").textContent =
    `CUE ${msg.rep}/${msg.progress?.total ?? 10} · GET READY`;
  $("#progress").textContent = `${msg.progress?.done ?? 0}/${msg.progress?.total ?? 10}`;
  $("#accuracy").textContent =
    msg.progress?.done ? `${Math.round(msg.progress.accuracy * 100)}%` : "--";
  $cue.className = "";
  $cue.textContent = "READY";
  $text.textContent = "Eyes on the cue. Throw only when the punch appears.";
  $result.classList.remove("show", "good", "bad");
  $("#detected").textContent = "--";
  $("#confidence").textContent = "--";
  $("#execution").textContent = "--";
  $("#zDrop").textContent = "--";

  S.timer = setTimeout(() => {
    $cue.textContent = label(S.cue.id);
    $cue.className = "go";
    $text.textContent = S.cue.cue || "Throw the punch, then return to guard.";
    // Ack immediately after paint; do not wait for rAF. The backend anchors
    // t0 here so the local camera and the cue share a time origin.
    S.ws?.send({ type: "painted", ts: performance.now(), clock: CLOCK_ID });
  }, Math.max(120, Number(msg.delay_ms) || 600));
}

function showResult(msg) {
  clearCueTimer();
  const correct = !!msg.correct;
  const outcome = msg.outcome || (correct ? "correct" : "miss");
  $result.classList.add("show", correct ? "good" : "bad");
  $("#resultOutcome").textContent =
    `${outcome.toUpperCase()} · ${msg.cue_label || label(msg.cue)}`;
  const rt = msg.reaction_ms ?? msg.rt_ms;
  $("#resultHeadline").textContent = correct
    ? `${rt == null ? "HELD" : `${rt} ms`} · ${label(msg.detected)}`
    : `${msg.detected ? label(msg.detected) : "NO PUNCH"} · expected ${label(msg.expected)}`;
  $("#resultTip").textContent = suggestion(msg);

  $("#progress").textContent = `${msg.progress?.done ?? 0}/${msg.progress?.total ?? 10}`;
  $("#accuracy").textContent =
    msg.progress?.done ? `${Math.round(msg.progress.accuracy * 100)}%` : "--";
  $("#combo").textContent = msg.combo ?? 0;
  $("#lastRt").textContent = rt == null ? "--" : `${rt} ms`;
  $("#detected").textContent = label(msg.detected);
  $("#confidence").textContent =
    msg.confidence == null ? "--" : `${Math.round(msg.confidence * 100)}%`;
  $("#execution").textContent =
    msg.execution_ms == null ? "--" : `${msg.execution_ms} ms`;
  $("#zDrop").textContent =
    msg.z_drop_cm == null ? "--" : `${msg.z_drop_cm} cm`;

  if (msg.tip) {
    $("#sessionHint").textContent = `${msg.tip.headline} ${msg.tip.body}`;
  } else {
    $("#sessionHint").textContent = shortHint(msg);
  }
  renderCounts(msg.stats);
}

function suggestion(msg) {
  if (msg.tip) return `${msg.tip.headline} ${msg.tip.body}`;
  if (msg.correct) {
    if (msg.reaction_ms == null) return "Guard held. Keep both hands up and eyes open.";
    if (msg.reaction_ms > 500) return "Correct, but slow. Snap the punch and reset immediately.";
    return "Clean. Same path, faster snap, hand back to the cheek.";
  }
  if (msg.outcome === "wrong") {
    return `Detected ${label(msg.detected)} instead of ${label(msg.expected)}. ${msg.cue_text || ""}`;
  }
  return `No clean ${msg.cue_label || "punch"} detected. Make the path obvious and return to guard.`;
}

function shortHint(msg) {
  if (msg.correct) return "Clean cue. Keep the guard tight.";
  if (msg.outcome === "wrong") return "Wrong action detected. Reset and throw the called punch.";
  return "No punch detected. Step in, throw, snap back.";
}

function renderCounts(stats) {
  const actions = stats?.actions || stats?.by_action || {};
  const chips = Object.keys(LABELS).filter((id) => id in actions).map((id) => {
    const s = actions[id] || {};
    return `<span class="chip">${esc(label(id))} <b>${s.correct ?? 0}/${s.cues ?? 0}</b></span>`;
  });
  $("#counts").innerHTML = chips.join("") || `<span class="chip">No cues yet</span>`;
}

function showReport(msg) {
  clearCueTimer();
  S.running = false;
  $("#startBtn").textContent = "START";
  const r = msg.report || {};
  $("#overlayTitle").textContent = "Set complete.";
  $("#overlayBody").textContent =
    msg.mascot?.line || "Accuracy first, then speed. Run it back.";
  $report.style.display = "grid";
  $report.innerHTML = `
    <div class="box"><span>Correct</span><b>${r.correct ?? 0}/${r.cues ?? 0}</b></div>
    <div class="box"><span>Accuracy</span><b>${Math.round((r.accuracy ?? 0) * 100)}%</b></div>
    <div class="box"><span>Median RT</span><b>${r.median_rt_ms ?? "--"} ms</b></div>
    <div class="box"><span>Max combo</span><b>${r.max_combo ?? 0}</b></div>`;
  $stars.style.display = "block";
  $stars.textContent = "★".repeat(msg.stars ?? 0) + "☆".repeat(3 - (msg.stars ?? 0));
  $overlayTip.textContent = msg.tip
    ? `${msg.tip.headline} ${msg.tip.body}`
    : "Single-camera gross-action labels. Compare your own session to your own session.";
  renderCounts(msg.report);
  $("#overlayStart").textContent = "RUN IT BACK";
  $overlay.classList.remove("hidden");
}

function startSession() {
  if (S.running) return;
  if (!S.ws?.open) {
    setStatus("offline", "");
    return;
  }
  hideOverlay();
  S.running = true;
  $("#startBtn").textContent = "RUNNING";
  $("#combo").textContent = "0";
  $("#lastRt").textContent = "--";
  $result.classList.remove("show", "good", "bad");
  S.ws.send({
    cmd: "start",
    player_id: S.playerId,
    mode: "boxing",
    stance: S.stance,
  });
}

$("#startBtn").onclick = startSession;
$("#overlayStart").onclick = startSession;
$("#overlayClose").onclick = hideOverlay;
$("#againBtn").onclick = () => {
  location.reload();
};
$("#stanceBtn").onclick = () => {
  S.stance = S.stance === "orthodox" ? "southpaw" : "orthodox";
  localStorage.setItem("s2.stance", S.stance);
  setStanceLabel();
  $("#sessionHint").textContent = `Stance set to ${S.stance}. Start the next set to apply it.`;
};

function startCam() {
  const cam = $("#cam");
  let inflight = false;
  async function tick() {
    if (inflight) return;
    inflight = true;
    try {
      const r = await fetch(`/frame?t=${Date.now()}`, { cache: "no-store" });
      if (r.ok) {
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const old = cam.dataset.url;
        cam.dataset.url = url;
        cam.src = url;
        if (old) setTimeout(() => URL.revokeObjectURL(old), 1500);
      }
    } catch {}
    inflight = false;
  }
  setInterval(tick, 50);
  tick();
}

startCam();
boot();
