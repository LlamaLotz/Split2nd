import {
  $, $$, CLOCK_ID, api, connectWS, esc, fmtDate, fmtMs, gradeWord, toast,
} from "./common.js";

const S = {
  playerId: localStorage.getItem("s2.player") || null,
  players: [],
  phase: "idle",
  state: {},
  room: null,
  config: {},
  result: null,
  evidence: [],
  overlayTimers: [],
  health: null,
  connected: false,
};

const ws = connectWS({
  role: "controller",
  playerId: S.playerId,
  onStatus(status) {
    S.connected = status === "open";
    if (status === "closed" || status === "error") {
      $("#hint").textContent = "Controller websocket lost - reconnecting…";
    }
  },
  onMessage: handle,
});

/* ------------------------------------------------------------------ */
/* messages                                                           */
/* ------------------------------------------------------------------ */
function handle(msg) {
  switch (msg.type) {
    case "hello":
      S.config = msg.config || {};
      S.evidence = msg.evidence || [];
      S.players = msg.players || [];
      S.playerId = S.playerId || msg.player?.id || S.players[0]?.id || null;
      S.room = msg.state?.room || null;
      $("#version").textContent = msg.version || "0.1.0";
      if (msg.config?.public_url) $("#phoneUrl").textContent = msg.config.public_url;
      renderPlayers();
      renderBoard(msg.leaderboard || []);
      applyState(msg.state || {});
      break;

    case "players":
      S.players = msg.players || [];
      renderPlayers();
      break;

    case "state":
      applyState(msg);
      break;

    case "schedule":
      if (msg.display === "controller") runOverlay(msg);
      else $("#hint").textContent = "Phone is painting. Watch the athlete.";
      break;

    case "result":
      handleResult(msg);
      break;

    case "room":
    case "room_done":
      S.room = msg.room;
      renderRoom();
      break;

    case "fault":
      toast(msg.message || "Fault");
      $("#hint").textContent = msg.message || "Fault";
      hideOverlay();
      break;

    case "presence":
      $("#clientText").textContent = String((msg.clients || []).length);
      $("#dotClients").className = "dot " + ((msg.clients || []).length ? "ok" : "");
      break;

    case "rig":
      // Low-volume hardware events; useful while wiring the Uno.
      if (msg.event === "HIT") $("#hint").textContent = `Gate ${msg.args?.[0]} ms · ${msg.args?.[1]} cm`;
      if (msg.event === "DIST" && msg.args?.length) {
        $("#camDistance").textContent =
          `GATE ${S.state?.gate_cm ?? 30} · DIST ${msg.args[0]} cm`;
      }
      break;

    default:
      break;
  }
}

function applyState(state) {
  S.state = state;
  S.phase = state.phase || "idle";
  if (state.gate_cm != null && document.activeElement !== $("#gate")) {
    $("#gate").value = state.gate_cm;
    $("#gateVal").textContent = `${state.gate_cm} cm`;
  }
  const cm = state.last_distance_cm;
  $("#camDistance").textContent = cm == null
    ? `GATE ${state.gate_cm ?? 30} · DIST --`
    : `GATE ${state.gate_cm ?? 30} · DIST ${cm} cm`;
  if (state.room) S.room = state.room;
  renderPhase();
  if (state.result && S.phase === "result") renderResultCard(state.result);
  if (state.room) renderRoom();
}

function handleResult(msg) {
  hideOverlay();
  if (!msg.bot && window.Split2ndEdge) {
    window.Split2ndEdge.flash(msg.edge?.color || msg.grade || "R", msg.edge?.ms || 900);
  }
  if (!msg.bot) {
    S.result = msg;
    $("#lastRt").textContent = msg.rt_ms == null ? "--" : fmtMs(msg.rt_ms);
    $("#lastGrade").textContent = msg.grade || "--";
    $("#lastGrade").className = "rt-grade " + (msg.grade || "");
    const bits = [];
    if (msg.hit_ms != null) bits.push(`Z ${msg.hit_ms} ms`);
    if (msg.best_ms != null) bits.push(`best ${msg.best_ms} ms`);
    if (msg.new_pb) bits.push("PERSONAL BEST");
    if (msg.xp?.total) bits.push(`+${msg.xp.total} xp`);
    if (msg.ghost_delta != null) bits.push(`${msg.ghost_delta >= 0 ? "+" : ""}${msg.ghost_delta} vs ghost`);
    if (msg.false_start) bits.push("anticipation");
    if (msg.miss) bits.push("no response");
    $("#lastMeta").textContent = bits.join(" · ") || gradeWord(msg.grade);
    renderTip(msg.tip);
    if (msg.new_pb) toast("Personal best · +20 XP");
    refreshBoard();
    refreshHistory();
  }
  if (msg.room) {
    S.room = msg.room;
    renderRoom();
  }
  if (msg.bot) {
    toast(`BOT ${msg.rt_ms} ms`);
  }
}

function renderResultCard(msg) {
  if (!msg || msg.bot) return;
  $("#lastRt").textContent = msg.rt_ms == null ? "--" : fmtMs(msg.rt_ms);
  $("#lastGrade").textContent = msg.grade || "--";
  $("#lastGrade").className = "rt-grade " + (msg.grade || "");
}

/* ------------------------------------------------------------------ */
/* UI: players, phase, board, room, history, tip                      */
/* ------------------------------------------------------------------ */
function renderPlayers() {
  const select = $("#playerSelect");
  if (!S.players.length) {
    select.innerHTML = `<option value="">No athletes</option>`;
    return;
  }
  select.innerHTML = S.players.map((p) =>
    `<option value="${esc(p.id)}">${esc(p.name)}${p.best_ms ? ` · ${p.best_ms}ms` : ""}</option>`
  ).join("");
  if (!S.players.some((p) => p.id === S.playerId)) S.playerId = S.players[0].id;
  select.value = S.playerId;
  localStorage.setItem("s2.player", S.playerId);
}

function renderPhase() {
  const chip = $("#phaseChip");
  chip.textContent = S.phase;
  chip.className = "chip " + ({
    ready: "live", arm: "warn", wait: "warn", response: "live", result: "live",
  }[S.phase] || "");

  const start = $("#startBtn");
  start.disabled = S.phase !== "idle";
  start.innerHTML = S.phase === "idle"
    ? `START REP <kbd>SPACE</kbd>`
    : `${(S.phase === "response" ? "GO" : S.phase).toUpperCase()}…`;

  const hint = S.state.msg || (S.phase === "idle" ? "Ready." : "");
  if (hint) $("#hint").textContent = S.phase === "idle"
    ? "Ready. Start a rep or hand the phone to the athlete."
    : hint;

  if (S.room?.status === "active" && S.room.turn_name) {
    const turn = S.room.players.find((p) => p.id === S.room.turn);
    if (turn?.bot) $("#hint").textContent = "BOT is taking its rep…";
    else if (S.phase === "idle") $("#hint").textContent = `Next up: ${S.room.turn_name}`;
  }
}

function renderBoard(rows) {
  if (!rows) return;
  const board = $("#board");
  if (!rows.length) {
    board.innerHTML = `<li><span class="rank">–</span><span class="name">No scores yet</span><span class="score">--</span></li>`;
    return;
  }
  board.innerHTML = rows.map((r, i) => `
    <li class="${r.player_id === S.playerId ? "me" : ""}" title="median ${fmtMs(r.median_ms)} ms · ${r.reps} reps">
      <span class="rank">${String(i + 1).padStart(2, "0")}</span>
      <span class="name">${esc(r.name)}${r.demo ? '<span class="tag">demo</span>' : ""}</span>
      <span class="score">${fmtMs(r.best_ms)}<small>ms</small></span>
    </li>`).join("");
}

async function refreshBoard() {
  try { renderBoard(await api("/api/leaderboard")); } catch {}
}

function renderRoom() {
  const body = $("#roomBody");
  const chip = $("#roomChip");
  const room = S.room;
  if (!room) {
    chip.textContent = "no room";
    chip.className = "chip";
    body.innerHTML = `<div class="tip-body">Create a match against a seeded bot, or type a friend's 4-letter code. A second phone can join the same room and the bot drops out.</div>`;
    return;
  }
  chip.textContent = room.status;
  chip.className = "chip " + (room.status === "done" ? "warn" : "live");

  const rows = room.players.map((p) => {
    const dots = Array.from({ length: room.target }, (_, i) => {
      const rt = p.rts[i];
      return `<i class="${rt == null ? "" : rt < 0 ? "miss" : "hit"}"></i>`;
    }).join("");
    return `
      <div class="rp ${room.turn === p.id ? "turn" : ""}">
        <span class="rp-name">${esc(p.name)}${p.bot ? "<small>BOT</small>" : ""}</span>
        <span class="rp-median">${p.median_ms ? p.median_ms + "ms" : "--"}</span>
        <span class="rp-score"><span class="dots">${dots}</span></span>
      </div>`;
  }).join("");

  const winner = room.winner
    ? `<div class="winner">Winner <b>${esc(room.winner)}</b> · decided by median RT, not one lucky rep.</div>`
    : "";
  const startMatch = (room.status === "active" && room.turn &&
    !room.players.find((p) => p.id === room.turn)?.bot && room.players.every((p) => p.reps === 0))
    ? `<button class="btn btn-sm" id="startMatch">Start match</button>` : "";

  body.innerHTML = `
    <div style="display:flex;align-items:baseline;gap:10px">
      <span class="room-code">${esc(room.code)}</span>
      <span style="font-family:var(--mono);font-size:10px;color:var(--dim)">${esc(room.status)}</span>
    </div>
    <div class="room-players">${rows}</div>
    ${winner}
    <div class="room-actions" style="padding-top:4px">${startMatch}</div>`;

  const btn = $("#startMatch");
  if (btn) btn.onclick = () => {
    const first = room.players.find((p) => !p.bot);
    if (first) ws.send({ cmd: "start", player_id: first.id, room: room.code, auto: true });
  };
}

function renderTip(tip) {
  if (!tip) return;
  const refs = (tip.refs || []).map((r) =>
    `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)} — ${esc(r.publisher)}, ${esc(r.year)}</a>`
  ).join("");
  $("#tipBody").innerHTML = `
    <div class="tip-headline">${esc(tip.headline)}</div>
    <div class="tip-body">${esc(tip.body)}</div>
    ${refs ? `<div class="tip-refs">${refs}</div>` : ""}
    <div class="disclaimer">${esc(tip.disclaimer || "")}</div>`;
}

async function refreshHistory() {
  try {
    const rows = await api("/api/history?limit=12");
    $("#repChip").textContent = `${rows.length} recent`;
    $("#repBody").innerHTML = rows.length ? rows.map((r) => `
      <tr>
        <td>${esc(fmtDate(r.created_at))}</td>
        <td>${esc(r.player_name)}</td>
        <td>${r.rt_ms == null ? "--" : fmtMs(r.rt_ms)}</td>
        <td class="g-${esc(r.grade)}">${esc(r.grade)}</td>
        <td>${r.hit_ms == null ? "--" : r.hit_ms}</td>
        <td>${r.xp ?? 0}</td>
      </tr>`).join("") : `<tr><td colspan="6">No reps yet.</td></tr>`;
  } catch {}
}

/* ------------------------------------------------------------------ */
/* the F1 start overlay (only when the laptop is the display)         */
/* ------------------------------------------------------------------ */
function runOverlay(msg) {
  const overlay = $("#stimOverlay");
  const lights = $$("#overlayLights .light");
  clearOverlayTimers();
  overlay.classList.remove("hidden");
  lights.forEach((l) => l.classList.remove("on"));
  $("#goText").classList.remove("show");
  $("#overlayLabel").textContent = "LIGHTS OUT";

  const n = Math.min(msg.lights || 5, lights.length);
  const delay = Math.max(600, msg.delay_ms || 3000);
  let interval = msg.light_interval_ms || 420;
  if (delay < n * interval + 200) interval = Math.max(70, (delay - 250) / n);

  for (let i = 0; i < n; i += 1) {
    S.overlayTimers.push(setTimeout(() => lights[i]?.classList.add("on"), i * interval));
  }

  S.overlayTimers.push(setTimeout(() => {
    const tPaint = performance.now();
    lights.forEach((l) => l.classList.remove("on"));
    $("#goText").classList.add("show");
    $("#overlayLabel").textContent = "GO";
    // Ack immediately: tPaint is the anchor for both channels. Waiting for
    // rAF here would delay the Arduino STIM and bias the ultrasonic HIT.
    ws.send({ type: "painted", ts: tPaint, clock: CLOCK_ID });
    S.overlayTimers.push(setTimeout(() => {
      overlay.classList.add("hidden");
      $("#goText").classList.remove("show");
    }, 600));
  }, delay));
}

function clearOverlayTimers() {
  S.overlayTimers.forEach(clearTimeout);
  S.overlayTimers = [];
}

function hideOverlay() {
  clearOverlayTimers();
  $("#stimOverlay").classList.add("hidden");
}

/* ------------------------------------------------------------------ */
/* controls                                                           */
/* ------------------------------------------------------------------ */
function activePlayerForStart() {
  if (S.room?.status === "active" && S.room.turn) return S.room.turn;
  return S.playerId;
}

$("#startBtn").onclick = () => {
  const pid = activePlayerForStart();
  if (!pid) return toast("Add an athlete first");
  const turn = S.room?.players?.find((p) => p.id === pid);
  if (turn?.bot) return toast("It's the bot's turn");
  ws.send({
    cmd: "start", player_id: pid,
    room: S.room?.status === "active" ? S.room.code : null,
    source: $("#source").value,
  });
};

$("#playerSelect").onchange = (e) => {
  S.playerId = e.target.value;
  localStorage.setItem("s2.player", S.playerId);
  ws.send({ cmd: "select", player_id: S.playerId });
  refreshBoard();
};

$("#addPlayer").onclick = async () => {
  const name = prompt("Athlete name");
  if (!name?.trim()) return;
  try {
    const p = await api("/api/players", { method: "POST", body: JSON.stringify({ name }) });
    S.playerId = p.id;
    localStorage.setItem("s2.player", p.id);
    ws.send({ cmd: "select", player_id: p.id });
    S.players = await api("/api/players");
    renderPlayers();
    toast(`${p.name} added`);
  } catch (err) { toast(err.message); }
};

$("#copyLink").onclick = async () => {
  if (!S.playerId) return toast("Pick an athlete first");
  const base = (S.config && S.config.public_url) || location.origin;
  const url = `${base}/play?player=${encodeURIComponent(S.playerId)}` +
    (S.room?.code ? `&room=${encodeURIComponent(S.room.code)}` : "");
  try {
    await navigator.clipboard.writeText(url);
    toast("Player link copied");
  } catch {
    prompt("Copy this link", url);
  }
};

$("#gate").oninput = (e) => { $("#gateVal").textContent = `${e.target.value} cm`; };
$("#gate").onchange = (e) => ws.send({ cmd: "gate", cm: Number(e.target.value) });
$("#source").onchange = (e) => ws.send({ cmd: "source", value: e.target.value });
$("#autoNext").onchange = (e) => ws.send({ cmd: "auto", value: e.target.checked });

$("#createRoom").onclick = () => {
  if (!S.playerId) return toast("Pick an athlete first");
  ws.send({ cmd: "room", player_id: S.playerId, bot: true });
};

$("#joinRoom").onclick = () => {
  const code = ($("#joinCode").value || "").trim().toUpperCase();
  if (!code) return toast("Enter a room code");
  ws.send({ cmd: "join", room: code, player_id: S.playerId });
};

$$("[data-sim]").forEach((btn) => {
  btn.onclick = () => ws.send({ cmd: "sim", kind: btn.dataset.sim });
});

$("#ledTest").onclick = () => {
  ["G", "Y", "R", "0"].forEach((v, i) => setTimeout(() => ws.send({ cmd: "led", value: v }), i * 420));
};

$("#seedBtn").onclick = async () => {
  try {
    await api("/api/dev/seed", { method: "POST" });
    S.players = await api("/api/players");
    renderPlayers();
    refreshBoard();
    toast("Demo history seeded");
  } catch (err) { toast(err.message); }
};

function setCamExpanded(on) {
  document.body.classList.toggle("cam-expanded", on);
  const btn = $("#camExpand");
  if (btn) btn.textContent = on ? "Close" : "Expand";
}

$("#camExpand").onclick = () => {
  setCamExpanded(!document.body.classList.contains("cam-expanded"));
};

document.addEventListener("keydown", (e) => {
  const tag = document.activeElement?.tagName;
  if (e.key === "Escape") {
    setCamExpanded(false);
    return;
  }
  if ((e.key === "f" || e.key === "F") && !["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(tag)) {
    e.preventDefault();
    setCamExpanded(!document.body.classList.contains("cam-expanded"));
    return;
  }
  if (e.code !== "Space") return;
  if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(tag)) return;
  e.preventDefault();
  $("#startBtn").click();
});

/* ------------------------------------------------------------------ */
/* health poll                                                        */
/* ------------------------------------------------------------------ */
async function poll() {
  try {
    const h = await api("/api/health");
    S.health = h;
    const rig = h.rig || {};
    const vis = h.vision || {};
    const clients = h.clients || [];

    $("#rigText").textContent = rig.backend === "serial"
      ? `${rig.port || "COM?"}${rig.connected ? "" : " (no link)"}`
      : "FAKE";
    $("#dotRig").className = "dot " + (
      rig.connected ? (rig.backend === "serial" ? "ok" : "warn") : "bad"
    );

    $("#visionText").textContent = `${(vis.mode || "?").toUpperCase()} ${vis.fps || 0} FPS`;
    const visBad = vis.local_error && vis.mode === "local";
    $("#dotVision").className = "dot " + (vis.fps > 0 ? "ok" : visBad ? "bad" : "warn");

    $("#clientText").textContent = String(clients.length);
    $("#dotClients").className = "dot " + (clients.length ? "ok" : "");

    $("#camChip").textContent = vis.has_preview ? "live" : "no signal";
    $("#camChip").className = "chip " + (vis.has_preview ? "live" : "warn");
    $("#nosignal").style.display = vis.has_preview ? "none" : "grid";
    $("#camStats").textContent = [
      `${vis.fps || 0} fps`,
      `${vis.proc_ms || 0} ms/frame`,
      vis.pose_ok ? "pose" : "motion",
      `${vis.frames || 0} frames`,
      vis.remote_active ? "phone" : (vis.local_ok ? "laptop" : "fake"),
    ].join(" · ");
    const distance = h.drill?.last_distance_cm ?? rig.last_distance_cm;
    if (distance != null) {
      $("#camDistance").textContent =
        `GATE ${h.drill?.gate_cm ?? 30} · DIST ${distance} cm`;
    }
  } catch {
    $("#dotRig").className = "dot bad";
    $("#dotVision").className = "dot bad";
  }
}

renderPhase();
refreshBoard();
refreshHistory();
poll();
setInterval(poll, 2500);
