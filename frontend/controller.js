import {
  $, $$, CLOCK_ID, api, boxingHandLabel, boxingHandSide, boxingLabel,
  clampNumber, connectWS, esc, fmtDate, fmtMs, fmtPct, fmtSigned, gradeWord,
  loadMode, median, saveMode, toast,
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
  mode: loadMode(),
  stance: localStorage.getItem("s2.stance") === "southpaw" ? "southpaw" : "orthodox",
  ghostSessionId: "",
  ghosts: [],
  box: {
    cues: [],
    actions: {},
    handRts: { lead: [], rear: [] },
    reps: 0,
    xp: 0,
    combo: 0,
    accuracy: null,
    report: null,
    reportMeta: null,
    scored: new Set(),
    timers: [],
    countTimer: null,
  },
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
/* icons (no emoji anywhere in the boxing UI)                         */
/* ------------------------------------------------------------------ */
const SVG_CHECK = `<svg class="box-icon ok" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const SVG_CROSS = `<svg class="box-icon bad" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>`;
const SVG_STAR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.6l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5-5.8-3-5.8 3 1.1-6.5L2.6 9.4l6.5-.9z"/></svg>`;
const SVG_CHEST = `<svg class="box-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8.5C3 6.6 4.6 5 6.5 5h11C19.4 5 21 6.6 21 8.5V10H3V8.5zM3 12h8v6H6.5A3.5 3.5 0 0 1 3 14.5V12zm10 0h8v2.5a3.5 3.5 0 0 1-3.5 3.5H13v-6zm-1-5.2a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/></svg>`;

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
      if (S.mode === "boxing") refreshBoard();
      break;

    case "players":
      S.players = msg.players || [];
      renderPlayers();
      break;

    case "state":
      applyState(msg);
      break;

    case "schedule":
      if (msg.mode === "boxing") runBoxSchedule(msg);
      else runRaceSchedule(msg);
      break;

    case "result":
      if (msg.mode === "boxing") handleBoxResult(msg);
      else handleResult(msg);
      break;

    case "session_end":
      handleSessionEnd(msg);
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
      hideBoxOverlay();
      break;

    case "presence":
      $("#clientText").textContent = String((msg.clients || []).length);
      $("#dotClients").className = "dot " + ((msg.clients || []).length ? "ok" : "");
      break;

    case "rig":
      // Low-volume hardware events; useful while wiring the Uno.
      if (msg.event === "HIT") $("#hint").textContent = `Gate ${msg.args?.[0]} ms · ${msg.args?.[1]} cm`;
      break;

    default:
      break;
  }
}

function applyState(state) {
  const prev = S.phase;
  S.state = state;
  S.phase = state.phase || "idle";
  if (state.room) S.room = state.room;

  // While a set is live the server's mode wins (a phone can start the set).
  // While idle the controller's own toggle stays authoritative.
  const mode = state.mode === "boxing" || state.mode === "racing" ? state.mode : null;
  if (mode && S.phase !== "idle" && mode !== S.mode) setMode(mode, { refresh: false });

  if (state.result) {
    if (state.result.mode === "boxing") renderBoxResultCard(state.result);
    else if (S.phase === "result") renderResultCard(state.result);
  }

  renderPhase();
  if (state.room) renderRoom();

  if (mode === "boxing" && prev !== S.phase && S.phase === "idle" && !S.box.report) {
    $("#hint").textContent = "Boxing idle. Pick a stance and ghost, then start a set.";
  }
}

/* ------------------------------------------------------------------ */
/* racing result                                                      */
/* ------------------------------------------------------------------ */
function handleResult(msg) {
  hideOverlay();
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
/* boxing messages                                                    */
/* ------------------------------------------------------------------ */
function runRaceSchedule(msg) {
  // The racing payload predates the mode field. Absence of a mode means
  // racing, so a stale boxing toggle can never swallow a racing schedule.
  if (S.mode !== "racing") setMode("racing");
  hideBoxOverlay();
  if (msg.display === "controller") runOverlay(msg);
  else $("#hint").textContent = "Phone is painting. Watch the athlete.";
}

function runBoxSchedule(msg) {
  if (S.mode !== "boxing") setMode("boxing");
  hideOverlay();
  logBoxCue(msg);
  const cue = msg.cue || {};
  $("#hint").textContent = `Cue ${boxingLabel(cue.action)} · ${boxingHandLabel(cue.hand, S.stance)}`;
  if (msg.display === "controller") runBoxOverlay(msg);
  else $("#hint").textContent = "Phone is painting the cue. Watch the athlete.";
}

function isEarly(msg) {
  const detected = String(msg?.detected ?? "").toLowerCase();
  return msg?.false_start === true || /false|early|anticip/.test(detected);
}

function handleBoxResult(msg) {
  hideBoxOverlay();
  S.result = msg;

  const early = isEarly(msg);
  const rt = msg.reaction_ms ?? null;
  $("#lastRt").textContent = rt == null ? "--" : fmtMs(rt);
  $("#rtUnit").textContent = "ms";
  $("#lastGrade").textContent = msg.correct ? "CLEAN" : early ? "EARLY" : "WRONG";
  $("#lastGrade").className = "rt-grade " + (msg.correct ? "G" : early ? "F" : "R");

  const bits = [`expected ${boxingLabel(msg.expected)}`];
  if (msg.detected != null && msg.detected !== "") bits.push(`got ${boxingLabel(msg.detected)}`);
  if (msg.confidence != null) bits.push(`conf ${fmtConfidence(msg.confidence)}`);
  if (msg.execution_ms != null) bits.push(`exec ${fmtMs(msg.execution_ms)} ms`);
  if (msg.combo != null) bits.push(`combo ${msg.combo}`);
  if (msg.ghost_delta != null) bits.push(`${fmtSigned(msg.ghost_delta)} vs ghost`);
  if (msg.xp?.total) bits.push(`+${msg.xp.total} xp`);
  if (msg.session) bits.push(`rep ${msg.session.index}/${msg.session.total} · ${fmtPct(msg.session.accuracy)}`);
  $("#lastMeta").textContent = bits.join(" · ");
  renderTip(msg.tip);

  recordBoxResult(msg);
  refreshBoard();
  refreshHistory();
  $("#hint").textContent = msg.correct
    ? "Clean cue. Keep the guard up."
    : early
      ? "Early movement. Wait for the cue to paint."
      : "Cue mismatch. Reset the guard and go again.";
}

function renderBoxResultCard(msg) {
  if (!msg) return;
  $("#lastRt").textContent = msg.reaction_ms == null ? "--" : fmtMs(msg.reaction_ms);
  $("#lastGrade").textContent = msg.correct ? "CLEAN" : isEarly(msg) ? "EARLY" : "WRONG";
  $("#lastGrade").className = "rt-grade " + (msg.correct ? "G" : isEarly(msg) ? "F" : "R");
}

function handleSessionEnd(msg) {
  if (!msg.report) return;
  hideBoxOverlay();
  S.box.report = msg.report;
  S.box.reportMeta = {
    stars: msg.stars,
    chest: msg.chest,
    mascot: msg.mascot,
    ghost: msg.ghost,
  };
  if (msg.report.accuracy != null) S.box.accuracy = msg.report.accuracy;
  renderBoxPanel();
  $("#hint").textContent = "Set complete. Review the report, then start the next set.";
  toast(`Boxing set complete · ${fmtPct(msg.report.accuracy)}`);
  refreshBoard();
  refreshHistory();
  refreshGhosts();
}

function logBoxCue(msg) {
  const cue = msg.cue || {};
  const entry = {
    rep: msg.rep,
    action: cue.action,
    label: cue.label || boxingLabel(cue.action),
    hand: cue.hand,
    at: Date.now(),
    correct: null,
    expected: cue.action,
    detected: null,
    reaction_ms: null,
    confidence: null,
    scored: false,
  };
  const idx = msg.rep != null ? S.box.cues.findIndex((c) => c.rep === msg.rep) : -1;
  if (idx >= 0) S.box.cues[idx] = entry;
  else S.box.cues.unshift(entry);
  S.box.cues = S.box.cues.slice(0, 14);
  renderBoxPanel();
}

function findCue(rep) {
  if (rep != null) return S.box.cues.find((c) => c.rep === rep) || null;
  return S.box.cues.find((c) => !c.scored) || null;
}

function recordBoxResult(msg) {
  const rep = msg.rep;
  if (rep != null && S.box.scored.has(rep)) {
    renderBoxPanel();
    return;
  }
  if (rep != null) S.box.scored.add(rep);

  const cue = findCue(rep);
  if (cue) {
    cue.correct = !!msg.correct;
    cue.expected = msg.expected ?? cue.action;
    cue.detected = msg.detected ?? null;
    cue.reaction_ms = msg.reaction_ms ?? null;
    cue.confidence = msg.confidence ?? null;
    cue.scored = true;
  }

  const action = String(msg.expected || cue?.action || "").toLowerCase();
  if (action) {
    const bucket = S.box.actions[action] || (S.box.actions[action] = { n: 0, correct: 0, rts: [] });
    bucket.n += 1;
    if (msg.correct) bucket.correct += 1;
    if (msg.reaction_ms != null && Number.isFinite(Number(msg.reaction_ms))) {
      bucket.rts.push(Number(msg.reaction_ms));
    }
  }

  if (msg.reaction_ms != null && cue?.hand) {
    const side = biasSide(cue.hand);
    if (side) S.box.handRts[side].push(Number(msg.reaction_ms));
  }

  if (msg.session?.accuracy != null) S.box.accuracy = msg.session.accuracy;
  S.box.combo = msg.combo != null ? Number(msg.combo) : (msg.correct ? S.box.combo + 1 : 0);
  S.box.xp += Number(msg.xp?.total || 0);
  S.box.reps = Math.max(
    S.box.reps,
    Number.isFinite(Number(msg.session?.index)) ? Number(msg.session.index) : S.box.reps + 1,
  );
  renderBoxPanel();
}

function biasSide(hand) {
  const h = String(hand ?? "").toLowerCase();
  const lead = S.stance === "southpaw" ? "right" : "left";
  if (h === "lead") return "lead";
  if (h === "rear") return "rear";
  if (h === "left" || h === "right") return h === lead ? "lead" : "rear";
  return null;
}

/* ------------------------------------------------------------------ */
/* boxing panel rendering                                             */
/* ------------------------------------------------------------------ */
const ACTION_ORDER = ["jab", "cross", "hook", "uppercut", "slip", "slip_left", "slip_right", "duck", "block"];

function renderBoxPanel() {
  if (!$("#ctrlBoxAcc")) return;
  const report = S.box.report;
  const localRows = Object.entries(S.box.actions).map(([action, bucket]) => ({
    action,
    accuracy: bucket.n ? bucket.correct / bucket.n : 0,
    median_rt: median(bucket.rts),
    n: bucket.n,
    correct: bucket.correct,
  }));
  const rows = report?.per_action?.length
    ? report.per_action.map((a) => ({
      action: a.action,
      accuracy: a.accuracy,
      median_rt: a.median_rt,
      n: a.n ?? a.reps,
    }))
    : localRows;

  const totals = Object.values(S.box.actions).reduce((acc, bucket) => {
    acc.n += bucket.n;
    acc.correct += bucket.correct;
    acc.rts.push(...bucket.rts);
    return acc;
  }, { n: 0, correct: 0, rts: [] });

  const accuracy = report?.accuracy ?? (totals.n ? totals.correct / totals.n : S.box.accuracy);
  $("#ctrlBoxAcc").textContent = fmtPct(accuracy);
  $("#ctrlBoxReps").textContent = String(report ? Math.max(S.box.reps, totals.n) : totals.n);
  $("#ctrlBoxBias").textContent = report?.lead_rear != null
    ? formatLeadRear(report.lead_rear)
    : formatLeadRearFromHands();
  $("#ctrlBoxFatigue").textContent = report?.fatigue != null
    ? formatFatigue(report.fatigue)
    : formatFatigueFromRts(totals.rts);
  $("#ctrlBoxBias").title = $("#ctrlBoxBias").textContent;
  $("#ctrlBoxFatigue").title = $("#ctrlBoxFatigue").textContent;

  renderBoxActionStats(rows);
  renderBoxCueLog();
  renderBoxSummary();
}

function renderBoxActionStats(rows) {
  const el = $("#ctrlBoxActions");
  if (!el) return;
  if (!rows.length) {
    el.innerHTML = `<div class="box-empty">Start a set to see per-action accuracy.</div>`;
    return;
  }
  rows.sort((a, b) => {
    const ai = ACTION_ORDER.indexOf(String(a.action).toLowerCase());
    const bi = ACTION_ORDER.indexOf(String(b.action).toLowerCase());
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
  el.innerHTML = rows.map((row) => {
    const pct = Number(row.accuracy);
    const width = Number.isFinite(pct) ? clampNumber(pct > 0 && pct <= 1 ? pct * 100 : pct, 0, 100, 0) : 0;
    const pctLabel = fmtPct(row.accuracy);
    const cls = width < 50 ? "bad" : width < 80 ? "warn" : "";
    const rt = row.median_rt != null ? `${fmtMs(row.median_rt)} ms` : "--";
    const count = row.n != null ? ` title="${esc(row.correct ?? "")}${row.correct != null ? "/" : ""}${esc(row.n)} scored"` : "";
    return `<div class="box-action-row"${count}>
      <span class="ba-name">${esc(boxingLabel(row.action))}</span>
      <span class="ba-bar"><i class="${cls}" style="width:${width.toFixed(1)}%"></i></span>
      <span class="ba-pct">${pctLabel}</span>
      <span class="ba-rt">${rt}</span>
    </div>`;
  }).join("");
}

function renderBoxCueLog() {
  const el = $("#ctrlBoxCueLog");
  if (!el) return;
  if (!S.box.cues.length) {
    el.innerHTML = `<div class="box-empty">No cues yet. Start a set.</div>`;
    return;
  }
  el.innerHTML = S.box.cues.map((cue) => {
    let result = `<span class="cue-result pending">WAIT</span>`;
    if (cue.correct === true) {
      result = `<span class="cue-result ok">${SVG_CHECK}${cue.reaction_ms != null ? ` ${fmtMs(cue.reaction_ms)} ms` : ""}</span>`;
    } else if (cue.correct === false) {
      const early = isEarly(cue);
      result = `<span class="cue-result ${early ? "early" : "bad"}">${SVG_CROSS}${cue.reaction_ms != null ? ` ${fmtMs(cue.reaction_ms)} ms` : ""}</span>`;
    }
    const detected = cue.correct === false && cue.detected
      ? ` → ${esc(boxingLabel(cue.detected))}`
      : "";
    const hand = cue.hand ? `<small>${esc(boxingHandLabel(cue.hand, S.stance))}</small>` : "";
    const title = `rep ${cue.rep ?? "?"} · expected ${boxingLabel(cue.expected || cue.action)}`
      + (cue.detected ? ` · detected ${boxingLabel(cue.detected)}` : "")
      + (cue.confidence != null ? ` · confidence ${fmtConfidence(cue.confidence)}` : "");
    return `<div class="box-cue-row" title="${esc(title)}">
      <span class="cue-rep">${cue.rep ?? "–"}</span>
      <span class="cue-name">${esc(cue.label || boxingLabel(cue.action))}${detected}${hand}</span>
      ${result}
    </div>`;
  }).join("");
}

function renderBoxSummary() {
  const el = $("#ctrlBoxSummary");
  if (!el) return;
  const report = S.box.report;
  if (!report) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const meta = S.box.reportMeta || {};
  const stars = starsHtml(meta.stars);
  const chest = chestLabel(meta.chest);
  const reps = Math.max(S.box.reps, Object.values(S.box.actions).reduce((n, b) => n + b.n, 0));
  const ghost = ghostLabel(meta.ghost);
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="box-summary-head">
      <span>Session report</span>
      <span>${stars}${chest ? ` ${SVG_CHEST} ${esc(chest)}` : ""}</span>
    </div>
    <div class="box-summary-grid">
      <span>Accuracy</span><b>${fmtPct(report.accuracy)}</b>
      <span>Reps</span><b>${reps || "--"}</b>
      <span>Lead / rear</span><b>${esc(formatLeadRear(report.lead_rear))}</b>
      <span>Fatigue</span><b>${esc(formatFatigue(report.fatigue))}</b>
      <span>Guard drops</span><b>${report.guard_drops ?? "--"}</b>
      <span>Combo breaks</span><b>${report.combo_breaks ?? "--"}</b>
    </div>
    ${ghost ? `<div class="box-summary-ghost">Ghost · ${esc(ghost)}</div>` : ""}`;
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

  const box = S.mode === "boxing";
  const start = $("#startBtn");
  start.disabled = S.phase !== "idle";
  if (S.phase === "idle") {
    start.innerHTML = box ? "START BOXING SET" : `START REP <kbd>SPACE</kbd>`;
  } else if (box) {
    start.innerHTML = `${{ arm: "GET SET", wait: "WAIT FOR CUE", response: "CUE", result: "RESULT" }[S.phase] || S.phase.toUpperCase()}…`;
  } else {
    start.innerHTML = `${(S.phase === "response" ? "GO" : S.phase).toUpperCase()}…`;
  }

  const stance = $("#stanceSelect");
  if (stance) stance.disabled = S.phase !== "idle";
  const ghost = $("#ghostSelect");
  if (ghost) ghost.disabled = S.phase !== "idle" || !S.ghosts.length;

  if (box) {
    if (S.phase === "idle") {
      $("#hint").textContent = "Pick a stance and ghost, then start a set. The phone paints; the laptop scores.";
    } else {
      $("#hint").textContent = S.state.msg || ({
        arm: "Get set. Stand in frame.",
        wait: "Cue is being scheduled.",
        response: "Cue is live. Match it.",
        result: "Scoring the cue.",
      }[S.phase] || "");
    }
    return;
  }

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
  try {
    renderBoard(await api(`/api/leaderboard?mode=${encodeURIComponent(S.mode)}`));
  } catch {}
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
  const refs = (tip.refs || []).map((r) => {
    const title = r.title || r.label || r.publisher || "source";
    const meta = [r.publisher && r.publisher !== title ? r.publisher : "", r.year].filter(Boolean).join(", ");
    return `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(title)}${meta ? ` — ${esc(meta)}` : ""}</a>`;
  }).join("");
  $("#tipBody").innerHTML = `
    <div class="tip-headline">${esc(tip.headline)}</div>
    <div class="tip-body">${esc(tip.body)}</div>
    ${refs ? `<div class="tip-refs">${refs}</div>` : ""}
    <div class="disclaimer">${esc(tip.disclaimer || "")}</div>`;
}

function renderRepHead() {
  const head = $("#repHead");
  if (!head) return;
  head.innerHTML = S.mode === "boxing"
    ? `<tr><th>Time</th><th>Athlete</th><th>Cue</th><th>RT</th><th>Acc</th><th>XP</th></tr>`
    : `<tr><th>Time</th><th>Athlete</th><th>RT</th><th>Grade</th><th>Z</th><th>XP</th></tr>`;
}

async function refreshHistory() {
  try {
    const rows = await api(`/api/history?limit=12&mode=${encodeURIComponent(S.mode)}`);
    // Older backends ignore the mode query param. Filter if the rows say.
    const visible = rows.some((r) => typeof r.mode === "string")
      ? rows.filter((r) => r.mode === S.mode)
      : rows;
    renderRepHead();
    $("#repChip").textContent = `${visible.length} recent`;
    $("#repBody").innerHTML = visible.length
      ? visible.map((r) => (S.mode === "boxing" ? boxingHistoryRow(r) : racingHistoryRow(r))).join("")
      : `<tr><td colspan="6">No reps yet.</td></tr>`;
  } catch {}
}

function racingHistoryRow(r) {
  return `
    <tr>
      <td>${esc(fmtDate(r.created_at))}</td>
      <td>${esc(r.player_name)}</td>
      <td>${r.rt_ms == null ? "--" : fmtMs(r.rt_ms)}</td>
      <td class="g-${esc(r.grade)}">${esc(r.grade)}</td>
      <td>${r.hit_ms == null ? "--" : r.hit_ms}</td>
      <td>${r.xp ?? 0}</td>
    </tr>`;
}

function boxingHistoryRow(r) {
  const expected = r.expected || r.action || r.cue?.action || "";
  const detected = r.detected || "";
  const cue = r.correct === false && detected
    ? `${boxingLabel(expected)} → ${boxingLabel(detected)}`
    : boxingLabel(expected);
  const rt = r.reaction_ms ?? r.rt_ms;
  const acc = r.correct === true ? "HIT" : r.correct === false ? "MISS" : "--";
  const cls = r.correct === true ? "g-G" : r.correct === false ? "g-R" : "";
  return `
    <tr>
      <td>${esc(fmtDate(r.created_at))}</td>
      <td>${esc(r.player_name)}</td>
      <td>${esc(cue || "--")}</td>
      <td>${rt == null ? "--" : fmtMs(rt)}</td>
      <td class="${cls}">${acc}</td>
      <td>${r.xp ?? 0}</td>
    </tr>`;
}

/* ------------------------------------------------------------------ */
/* ghost / friend sessions                                            */
/* ------------------------------------------------------------------ */
async function refreshGhosts() {
  try {
    const rows = await api("/api/history?limit=160&mode=boxing");
    const sessions = new Map();
    for (const r of rows) {
      if (!r.session_id) continue;
      if (typeof r.mode === "string" && r.mode !== "boxing") continue;
      let s = sessions.get(r.session_id);
      if (!s) {
        s = {
          id: r.session_id,
          player: r.player_name || "Athlete",
          at: r.created_at || "",
          rts: [],
        };
        sessions.set(r.session_id, s);
      }
      const rt = r.reaction_ms ?? r.rt_ms;
      if (rt != null && Number.isFinite(Number(rt))) s.rts.push(Number(rt));
      if (r.created_at && r.created_at > s.at) s.at = r.created_at;
    }
    S.ghosts = [...sessions.values()]
      .map((s) => ({ ...s, reps: s.rts.length, median: median(s.rts) }))
      .filter((s) => s.reps > 0)
      .sort((a, b) => String(b.at).localeCompare(String(a.at)))
      .slice(0, 24);

    const select = $("#ghostSelect");
    if (!select) return;
    select.innerHTML = `<option value="">Solo / personal best</option>` + S.ghosts.map((s) =>
      `<option value="${esc(s.id)}">${esc(s.player)} · ${esc(fmtDate(s.at))} · ${s.reps} reps · ${fmtMs(s.median)} ms</option>`
    ).join("");
    if (S.ghosts.some((s) => s.id === S.ghostSessionId)) select.value = S.ghostSessionId;
    else { S.ghostSessionId = ""; select.value = ""; }
    select.disabled = S.phase !== "idle" || !S.ghosts.length;
  } catch {}
}

/* ------------------------------------------------------------------ */
/* formatting helpers                                                 */
/* ------------------------------------------------------------------ */
function fmtConfidence(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "--";
  return n > 0 && n <= 1 ? n.toFixed(2) : `${Math.round(n)}%`;
}

function starsHtml(stars) {
  const n = clampNumber(stars, 0, 5, 0);
  return Array.from({ length: 5 }, (_, i) => {
    const svg = SVG_STAR.replace("<svg", `<svg class="${i < n ? "on" : ""}"`);
    return svg;
  }).join("");
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

function formatLeadRearFromHands() {
  const lead = median(S.box.handRts.lead);
  const rear = median(S.box.handRts.rear);
  if (lead == null && rear == null) return "--";
  const bits = [
    `L ${lead != null ? fmtMs(lead) : "--"}`,
    `R ${rear != null ? fmtMs(rear) : "--"}`,
  ];
  if (lead != null && rear != null) bits.push(fmtSigned(rear - lead));
  return bits.join(" · ");
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

function formatFatigueFromRts(rts) {
  if (!rts.length || rts.length < 4) return "--";
  const mid = Math.floor(rts.length / 2);
  const first = median(rts.slice(0, mid));
  const last = median(rts.slice(mid));
  if (first == null || last == null || first <= 0) return "--";
  const pct = ((last - first) / first) * 100;
  return `${pct > 0 ? "+" : ""}${Math.round(pct)}%`;
}

/* ------------------------------------------------------------------ */
/* mode                                                               */
/* ------------------------------------------------------------------ */
function setMode(mode, { persist = true, refresh = true } = {}) {
  if (mode !== "racing" && mode !== "boxing") return;
  const changed = mode !== S.mode;
  S.mode = mode;
  if (persist) saveMode(mode);
  document.body.classList.toggle("mode-boxing", mode === "boxing");

  $$("#ctrlMode [data-mode]").forEach((btn) => {
    const on = btn.dataset.mode === mode;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", String(on));
  });

  const chip = $("#boardChip");
  if (chip) {
    chip.textContent = mode;
    chip.className = "chip " + (mode === "boxing" ? "purple" : "live");
  }

  if (mode === "boxing") hideOverlay();
  else hideBoxOverlay();

  if (changed) toast(mode === "boxing" ? "Boxing mode" : "Racing mode");
  renderPhase();
  renderBoxPanel();
  if (refresh && changed) {
    refreshBoard();
    refreshHistory();
    if (mode === "boxing") refreshGhosts();
  }
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
/* boxing cue overlay (controller as display fallback)                */
/* ------------------------------------------------------------------ */
function runBoxOverlay(msg) {
  const overlay = $("#boxOverlay");
  clearBoxTimers();
  overlay.classList.add("hidden");
  const cue = msg.cue || {};
  const label = cue.label || boxingLabel(cue.action);
  const windowMs = clampNumber(msg.window_ms, 400, 5000, 1400);
  const delay = clampNumber(msg.delay_ms, 0, 5000, 650);

  $("#boWord").textContent = label;
  $("#boWord").classList.toggle("long", label.length > 6);
  $("#boHand").textContent = boxingHandLabel(cue.hand, S.stance);
  overlay.dataset.side = boxingHandSide(cue.hand, S.stance);
  $("#boTimerBar").classList.remove("run");
  $("#boCount").textContent = `${(windowMs / 1000).toFixed(1)}s`;

  S.box.timers.push(setTimeout(() => {
    overlay.classList.remove("hidden");
    const tPaint = performance.now();
    ws.send({ type: "painted", ts: tPaint, clock: CLOCK_ID });
    const bar = $("#boTimerBar");
    bar.style.animationDuration = `${windowMs}ms`;
    void bar.offsetWidth;
    bar.classList.add("run");

    const end = performance.now() + windowMs;
    S.box.countTimer = setInterval(() => {
      const left = Math.max(0, end - performance.now());
      $("#boCount").textContent = `${(left / 1000).toFixed(1)}s`;
      if (left <= 0) clearInterval(S.box.countTimer);
    }, 100);

    S.box.timers.push(setTimeout(hideBoxOverlay, windowMs + 500));
  }, delay));
}

function clearBoxTimers() {
  S.box.timers.forEach(clearTimeout);
  S.box.timers = [];
  if (S.box.countTimer) {
    clearInterval(S.box.countTimer);
    S.box.countTimer = null;
  }
}

function hideBoxOverlay() {
  clearBoxTimers();
  $("#boxOverlay").classList.add("hidden");
}

/* ------------------------------------------------------------------ */
/* controls                                                           */
/* ------------------------------------------------------------------ */
function activePlayerForStart() {
  if (S.mode === "boxing") return S.playerId;
  if (S.room?.status === "active" && S.room.turn) return S.room.turn;
  return S.playerId;
}

$("#startBtn").onclick = () => {
  const pid = activePlayerForStart();
  if (!pid) return toast("Add an athlete first");

  if (S.mode === "boxing") {
    if (S.phase !== "idle") return;
    resetBoxStats();
    const payload = {
      cmd: "start",
      player_id: pid,
      mode: "boxing",
      stance: S.stance,
    };
    if (S.ghostSessionId) payload.ghost_session_id = S.ghostSessionId;
    const sent = ws.send(payload);
    $("#hint").textContent = sent
      ? "Set queued. Stand in frame and keep your guard up."
      : "Controller is offline - reconnecting…";
    return;
  }

  const turn = S.room?.players?.find((p) => p.id === pid);
  if (turn?.bot) return toast("It's the bot's turn");
  ws.send({
    cmd: "start", player_id: pid,
    room: S.room?.status === "active" ? S.room.code : null,
    source: $("#source").value,
  });
};

$("#ctrlMode").onclick = (e) => {
  const btn = e.target.closest("[data-mode]");
  if (btn) setMode(btn.dataset.mode);
};

$("#stanceSelect").onchange = (e) => {
  S.stance = e.target.value === "southpaw" ? "southpaw" : "orthodox";
  localStorage.setItem("s2.stance", S.stance);
  renderBoxPanel();
};

$("#ghostSelect").onchange = (e) => {
  S.ghostSessionId = e.target.value || "";
};

$("#clearCueLog").onclick = () => {
  resetBoxStats();
  toast("Boxing log cleared");
};

function resetBoxStats() {
  S.box.cues = [];
  S.box.actions = {};
  S.box.handRts = { lead: [], rear: [] };
  S.box.reps = 0;
  S.box.xp = 0;
  S.box.combo = 0;
  S.box.accuracy = null;
  S.box.report = null;
  S.box.reportMeta = null;
  S.box.scored.clear();
  renderBoxPanel();
}

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

document.addEventListener("keydown", (e) => {
  if (e.code !== "Space") return;
  const tag = document.activeElement?.tagName;
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
    $("#camMeta").textContent = [
      `${vis.fps || 0} fps`,
      `${vis.proc_ms || 0} ms/frame`,
      vis.pose_ok ? "pose" : "motion",
      `${vis.frames || 0} frames`,
      vis.remote_active ? "phone" : (vis.local_ok ? "laptop" : "fake"),
    ].join(" · ");
  } catch {
    $("#dotRig").className = "dot bad";
    $("#dotVision").className = "dot bad";
  }
}

/* ------------------------------------------------------------------ */
/* boot                                                               */
/* ------------------------------------------------------------------ */
$("#stanceSelect").value = S.stance;
setMode(S.mode, { persist: false, refresh: false });
renderPhase();
renderBoxPanel();
refreshBoard();
refreshHistory();
refreshGhosts();
poll();
setInterval(poll, 2500);
