import {
  $, $$, CLOCK_ID, api, boxingCueHand, boxingHandSide, boxingLabel,
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
  player: null,
  evidence: [],
  overlayTimers: [],
  health: null,
  connected: false,
  arenaOpen: false,
  arenaReportOpen: false,
  mode: loadMode("boxing"),
  stance: localStorage.getItem("s2.stance") === "southpaw" ? "southpaw" : "orthodox",
  ghostSessionId: "",
  ghosts: [],
  dailyGoal: 20,
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
    cuePainted: false,
    setStartedAt: 0,
    timerInterval: null,
    lastTip: null,
    sessionId: null,
    profile: null,
  },
};

const ws = connectWS({
  role: "controller",
  playerId: S.playerId,
  onStatus(status) {
    S.connected = status === "open";
    const conn = $("#arenaConn");
    if (conn) {
      conn.innerHTML = `<i class="dot ${S.connected ? "ok" : status === "connecting" ? "warn" : "bad"}"></i>${S.connected ? "live" : status === "connecting" ? "connecting" : "offline"}`;
    }
    if (status === "closed" || status === "error") {
      $("#hint").textContent = "Controller websocket lost - reconnecting…";
      $("#boxHint").textContent = "Controller websocket lost - reconnecting…";
    }
  },
  onMessage: handle,
});

/* ------------------------------------------------------------------ */
/* icons (no emoji anywhere in the boxing UI)                         */
/* ------------------------------------------------------------------ */
const SVG_CHECK = `<svg class="box-icon ok" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const SVG_CROSS = `<svg class="box-icon bad" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>`;
const BADGE_CHEST = `<img class="box-badge" src="/static/assets/badge-chest.svg" alt="">`;

/* ------------------------------------------------------------------ */
/* messages                                                           */
/* ------------------------------------------------------------------ */
function handle(msg) {
  switch (msg.type) {
    case "hello":
      S.config = msg.config || {};
      S.evidence = msg.evidence || [];
      S.players = msg.players || [];
      S.player = msg.player || null;
      S.playerId = S.playerId || msg.player?.id || S.players[0]?.id || null;
      S.room = msg.state?.room || null;
      $("#version").textContent = msg.version || "0.1.0";
      renderPlayers();
      renderBoard(msg.leaderboard || []);
      applyState(msg.state || {});
      renderArenaChrome();
      if (S.mode === "boxing") refreshBoard();
      refreshHistory();
      refreshGhosts();
      refreshDailyGoal();
      refreshBoxingProfile();
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
      clearArenaCue();
      if (S.arenaOpen) {
        setArenaIdle("READY", "CHECK THE MESSAGE");
        $("#arenaLast").textContent = msg.message || "Fault";
      }
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
  if (state.gate_cm != null) {
    $("#gate").value = state.gate_cm;
    $("#gateVal").textContent = `${state.gate_cm} cm`;
  }
  if (typeof state.auto === "boolean") $("#autoNext").checked = state.auto;
  if (state.source) {
    $("#source").value = state.source === "local" ? "local" : "auto";
  }
  if (state.room) S.room = state.room;
  else if (state.room === null) S.room = null;
  const stateMatchesPlayer = !state.player_id || state.player_id === S.playerId;
  if (state.player && stateMatchesPlayer) {
    S.player = state.player;
    renderArenaChrome();
  }
  if (state.stance === "orthodox" || state.stance === "southpaw") {
    S.stance = state.stance;
    localStorage.setItem("s2.stance", S.stance);
    $("#stanceSelect").value = S.stance;
    renderArenaChrome();
  }
  if (state.progress && stateMatchesPlayer) {
    const p = state.progress;
    const cueNo = S.phase === "result" ? Math.max(1, p.done) : Math.min(p.done + 1, p.total);
    if (S.arenaOpen) $("#arenaRepLabel").textContent = `Rep ${cueNo}/${p.total}`;
    if (p.accuracy != null) {
      S.box.accuracy = p.accuracy;
      if (S.arenaOpen) setArenaAccuracy(p.accuracy);
      $("#ctrlBoxAcc").textContent = fmtPct(p.accuracy);
    }
    $("#ctrlBoxReps").textContent = String(p.done ?? 0);
  }

  // While a set is live the server's mode wins (a fallback display can start it).
  // While idle the controller's own toggle stays authoritative.
  const mode = state.mode === "boxing" || state.mode === "racing" ? state.mode : null;
  if (mode && S.phase !== "idle" && mode !== S.mode) setMode(mode, { refresh: false });
  const sessionId = state.session_id || null;
  const newSession = !!sessionId && sessionId !== S.box.sessionId;
  if (sessionId) S.box.sessionId = sessionId;
  if (mode === "boxing" && state.phase === "arm" && prev !== "arm"
      && (newSession || !state.session_id && state.progress?.done === 0)) {
    resetBoxStats();
    if (!S.arenaOpen) openArena();
    resetArenaSession();
    startArenaTimer({ reset: true });
  }

  if (state.result && stateMatchesPlayer) {
    if (state.result.mode === "boxing") renderBoxResultCard(state.result);
    else if (S.phase === "result") renderResultCard(state.result);
  }

  renderPhase();
  renderRoom();

  if (S.arenaOpen && S.mode === "boxing") {
    if (S.phase === "arm") setArenaIdle("GET SET", "HANDS UP");
    else if (S.phase === "wait" && !S.box.cuePainted) setArenaIdle("READY", "WATCH THE LINE");
    else if (S.phase === "response" && !S.box.cuePainted) setArenaIdle("CUE", "WAIT FOR THE CUE");
    else if (S.phase === "idle" && !S.arenaReportOpen) setArenaIdle("READY", "Head and both hands in frame");
  }

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
    setRaceResultIdle(false);
    if (msg.edge?.color || msg.grade) {
      flashResultEdge(msg.edge?.color || msg.grade, msg.edge?.ms || 900);
    }
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
    refreshDailyGoal();
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
  setRaceResultIdle(false);
  $("#lastRt").textContent = msg.rt_ms == null ? "--" : fmtMs(msg.rt_ms);
  $("#lastGrade").textContent = msg.grade || "--";
  $("#lastGrade").className = "rt-grade " + (msg.grade || "");
}

function setRaceResultIdle(idle) {
  const line = $("#rtLine");
  if (!line) return;
  line.classList.toggle("is-idle", idle);
  if (idle) {
    $("#lastRt").textContent = "READY";
    $("#lastGrade").textContent = "--";
    $("#lastGrade").className = "rt-grade";
    $("#lastMeta").textContent = "No reps yet. Pick an athlete and start a set.";
  }
}

/* ------------------------------------------------------------------ */
/* boxing messages                                                    */
/* ------------------------------------------------------------------ */
function runRaceSchedule(msg) {
  // The racing payload predates the mode field. Absence of a mode means
  // racing, so a stale boxing toggle can never swallow a racing schedule.
  if (S.mode !== "racing") setMode("racing");
  if (msg.display === "controller") runOverlay(msg);
  else $("#hint").textContent = "Display client is painting. Watch the athlete.";
}

function runBoxSchedule(msg) {
  if (S.mode !== "boxing") setMode("boxing");
  if (!S.arenaOpen) openArena();
  hideOverlay();
  const stance = msg.stance || msg.cue?.stance;
  if (stance === "orthodox" || stance === "southpaw") {
    S.stance = stance;
    localStorage.setItem("s2.stance", S.stance);
    $("#stanceSelect").value = S.stance;
  }
  logBoxCue(msg);
  renderArenaChrome();
  const cue = msg.cue || {};
  const cueHint = `Cue ${boxingLabel(cue.action)} · ${boxingCueHand(cue, cue.stance || S.stance)}`;
  $("#hint").textContent = cueHint;
  $("#boxHint").textContent = cueHint;
  if (msg.display === "player") {
    $("#hint").textContent = "Fallback display is painting the cue. The arena mirrors the state.";
    $("#boxHint").textContent = $("#hint").textContent;
  }
  runArenaCue(msg);
}

function isEarly(msg) {
  const detected = String(msg?.detected ?? "").toLowerCase();
  return msg?.false_start === true || /false|early|anticip/.test(detected);
}

function boxTone(msg) {
  if (isEarly(msg)) return { key: "false", word: "FALSE START", grade: "F" };
  const outcome = String(msg.outcome || "").toLowerCase();
  if (outcome === "miss" || msg.miss) return { key: "bad", word: "MISS", grade: "R" };
  if (outcome === "wrong" || msg.correct === false) return { key: "bad", word: "WRONG", grade: "R" };
  if (msg.grade === "Y") return { key: "slow", word: "SOLID", grade: "Y" };
  if (msg.grade === "R") return { key: "bad", word: "SLOW", grade: "R" };
  return { key: "good", word: "CLEAN", grade: "G" };
}

function handleBoxResult(msg) {
  clearArenaCue();
  S.result = msg;
  if (msg.player) {
    S.player = msg.player;
    S.players = S.players.map((p) => (p.id === msg.player.id ? { ...p, ...msg.player } : p));
    renderPlayers();
    renderArenaChrome();
  }

  const early = isEarly(msg);
  const tone = boxTone(msg);
  const cue = findCue(msg.rep);
  const expected = msg.expected_action || msg.expected || cue?.action;
  const detected = msg.detected_action ?? msg.detected ?? null;
  const rt = msg.reaction_ms ?? msg.rt_ms ?? null;
  const progress = msg.progress || null;
  const sessionStats = msg.session_stats || null;

  const bits = [];
  if (expected) bits.push(`expected ${boxingLabel(expected)}`);
  if (detected) bits.push(`got ${boxingLabel(detected)}`);
  if (msg.confidence != null) bits.push(`conf ${fmtConfidence(msg.confidence)}`);
  if (msg.execution_ms != null) bits.push(`exec ${fmtMs(msg.execution_ms)} ms`);
  if (msg.combo != null) bits.push(`combo ${msg.combo}`);
  if (msg.hit_ms != null) {
    bits.push(`Z ${fmtMs(msg.hit_ms)} ms${msg.hit_delta_ms != null ? ` (${fmtSigned(msg.hit_delta_ms)})` : ""}`);
  }
  if (msg.ghost_delta != null) bits.push(`${fmtSigned(msg.ghost_delta)} vs ghost`);
  if (progress) bits.push(`cue ${progress.done}/${progress.total} · ${fmtPct(progress.accuracy)}`);
  else if (sessionStats) bits.push(`cue ${sessionStats.cues || 0} · ${fmtPct(sessionStats.accuracy)}`);
  $("#boxLastRt").classList.remove("is-empty");
  $("#boxLastRt").textContent = rt == null ? "—" : fmtMs(rt);
  $("#boxLastGrade").textContent = `${tone.grade} · ${tone.word}`;
  $("#boxLastGrade").className = `console-grade box-${tone.key}`;
  $("#boxLastMeta").textContent = bits.join(" · ") || "Cue scored.";
  renderBoxXpChips(msg.xp);
  if (msg.tip) {
    renderTip(msg.tip);
    S.box.lastTip = msg.tip;
  }

  recordBoxResult(msg);
  renderArenaResult(msg, tone, early);
  refreshBoard();
  refreshHistory();
  refreshDailyGoal();
  refreshBoxingProfile();
  $("#hint").textContent = tone.key === "good"
    ? "Clean cue. Keep the guard up."
    : tone.key === "slow"
      ? "Solid cue. Hands back to guard."
      : msg.outcome === "miss"
        ? "No action detected. Step back so your head and both hands are in frame."
        : msg.outcome === "correct"
          ? "Slow cue. Snap back to guard."
          : "Cue mismatch. Reset the guard and go again.";
  $("#boxHint").textContent = $("#hint").textContent;
}

function renderBoxXpChips(xp) {
  const el = $("#boxXpChips");
  if (!el) return;
  if (!xp || !xp.total) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const rows = [
    ["base", xp.base],
    ["speed", xp.speed],
    ["combo", xp.combo],
    ["accuracy", xp.accuracy],
    ["total", xp.total],
  ];
  el.innerHTML = rows.map(([label, value]) =>
    `<span class="xp-chip-mini">${label} <b>${value ?? 0}</b></span>`
  ).join("");
  el.classList.remove("hidden");
}

function renderBoxResultCard(msg) {
  if (!msg) return;
  const tone = boxTone(msg);
  const expected = msg.expected_action || msg.expected;
  const detected = msg.detected_action || msg.detected;
  const rt = msg.reaction_ms ?? msg.rt_ms ?? null;
  $("#boxLastRt").classList.remove("is-empty");
  $("#boxLastRt").textContent = rt == null ? "—" : fmtMs(rt);
  $("#boxLastGrade").textContent = `${tone.grade} · ${tone.word}`;
  $("#boxLastGrade").className = `console-grade box-${tone.key}`;
  const bits = [];
  if (expected) bits.push(`expected ${boxingLabel(expected)}`);
  if (detected) bits.push(`got ${boxingLabel(detected)}`);
  if (msg.combo != null) bits.push(`combo ${msg.combo}`);
  if (msg.progress) bits.push(`cue ${msg.progress.done}/${msg.progress.total}`);
  $("#boxLastMeta").textContent = bits.join(" · ");
  renderBoxXpChips(msg.xp);
}

function handleSessionEnd(msg) {
  if (!msg.report) return;
  clearArenaCue();
  stopArenaTimer();
  if (msg.player) {
    S.player = msg.player;
    renderArenaChrome();
  }
  if (msg.tip) {
    S.box.lastTip = msg.tip;
    renderTip(msg.tip);
  }
  S.box.report = msg.report;
  S.box.reportMeta = {
    stars: msg.stars,
    chest: msg.chest,
    mascot: msg.mascot,
    ghost: msg.ghost,
  };
  if (msg.report.accuracy != null) S.box.accuracy = msg.report.accuracy;
  renderBoxPanel();
  renderArenaReport(msg);
  $("#hint").textContent = "Set complete. Review the report, then start the next set.";
  $("#boxHint").textContent = $("#hint").textContent;
  toast(`Boxing set complete · ${fmtPct(msg.report.accuracy)}`);
  refreshBoard();
  refreshHistory();
  refreshGhosts();
  refreshDailyGoal();
  refreshBoxingProfile();
  refreshPlayer();
}

function logBoxCue(msg) {
  const cue = msg.cue || {};
  const entry = {
    rep: msg.rep,
    action: cue.action,
    label: cue.label || boxingLabel(cue.action),
    hand: cue.hand,
    hand_side: cue.hand_side,
    stance: cue.stance || S.stance,
    cue_text: cue.cue,
    family: cue.family,
    pattern: cue.pattern,
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
  const expected = msg.expected_action || msg.expected || cue?.action;
  const detected = msg.detected_action ?? msg.detected ?? null;
  if (cue) {
    cue.correct = !!msg.correct;
    cue.outcome = msg.outcome || (msg.correct ? "correct" : "miss");
    cue.expected = expected ?? cue.action;
    cue.detected = detected;
    cue.reaction_ms = msg.reaction_ms ?? msg.rt_ms ?? null;
    cue.confidence = msg.confidence ?? null;
    cue.scored = true;
  }

  const action = String(expected || "").toLowerCase();
  if (action) {
    const bucket = S.box.actions[action] || (S.box.actions[action] = { n: 0, correct: 0, rts: [] });
    bucket.n += 1;
    if (msg.correct) bucket.correct += 1;
    if (msg.reaction_ms != null && Number.isFinite(Number(msg.reaction_ms))) {
      bucket.rts.push(Number(msg.reaction_ms));
    }
  }

  if (msg.reaction_ms != null && cue?.hand) {
    const side = biasSide(cue.hand, cue.hand_side, cue.stance);
    if (side) S.box.handRts[side].push(Number(msg.reaction_ms));
  }

  const progress = msg.progress || null;
  const sessionStats = msg.session_stats || null;
  if (progress?.accuracy != null) S.box.accuracy = progress.accuracy;
  else if (sessionStats?.accuracy != null) S.box.accuracy = sessionStats.accuracy;
  S.box.combo = msg.combo != null ? Number(msg.combo) : (msg.correct ? S.box.combo + 1 : 0);
  S.box.xp += Number(msg.xp?.total || 0);
  S.box.reps = Math.max(
    S.box.reps,
    Number.isFinite(Number(progress?.done)) ? Number(progress.done) : S.box.reps + 1,
  );
  renderBoxPanel();
}

function biasSide(hand, handSide, stance = S.stance) {
  const h = String(hand ?? "").toLowerCase();
  const side = String(handSide ?? "").toLowerCase();
  const lead = stance === "southpaw" ? "right" : "left";
  if (h === "lead") return "lead";
  if (h === "rear") return "rear";
  if (side === "left" || side === "right") return side === lead ? "lead" : "rear";
  if (h === "left" || h === "right") return h === lead ? "lead" : "rear";
  return null;
}

/* ------------------------------------------------------------------ */
/* boxing panel rendering                                             */
/* ------------------------------------------------------------------ */
const ACTION_ORDER = [
  "jab", "cross", "hook", "hook_l", "hook_r",
  "uppercut", "uppercut_l", "uppercut_r",
  "slip", "slip_l", "slip_r", "slip_left", "slip_right",
  "duck", "block",
];

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
  const reportRows = reportActionRows(report);
  const profile = S.box.profile;
  const profileRows = profile?.actions && typeof profile.actions === "object"
    ? Object.entries(profile.actions).map(([action, value]) => ({ action, ...value }))
    : [];
  const normalized = (rows) => rows.map((a) => ({
    action: a.action,
    accuracy: a.accuracy,
    median_rt: a.median_rt_ms ?? a.median_rt,
    n: a.cues ?? a.n ?? a.reps,
    correct: a.correct,
  }));
  const rows = reportRows.length
    ? normalized(reportRows)
    : localRows.length
      ? localRows
      : normalized(profileRows);

  const totals = Object.values(S.box.actions).reduce((acc, bucket) => {
    acc.n += bucket.n;
    acc.correct += bucket.correct;
    acc.rts.push(...bucket.rts);
    return acc;
  }, { n: 0, correct: 0, rts: [] });

  const accuracy = report?.accuracy
    ?? (totals.n ? totals.correct / totals.n : (profile?.accuracy ?? S.box.accuracy));
  $("#ctrlBoxAcc").textContent = fmtPct(accuracy);
  $("#ctrlBoxReps").textContent = String(report?.cues ?? report?.n ?? (totals.n || profile?.cues || 0));
  const biasFull = report?.lead_rear != null
    ? formatLeadRear(report.lead_rear)
    : formatLeadRearFromHands(false) !== "--"
      ? formatLeadRearFromHands(false)
      : formatProfileBias(profile, false);
  $("#ctrlBoxBias").textContent = report?.lead_rear != null
    ? biasFull
    : formatLeadRearFromHands(true) !== "--"
      ? formatLeadRearFromHands(true)
      : formatProfileBias(profile, true);
  $("#ctrlBoxFatigue").textContent = report?.fatigue != null
    ? formatFatigue(report.fatigue)
    : totals.rts.length >= 4
      ? formatFatigueFromRts(totals.rts)
      : formatFatigue(profile?.fatigue);
  $("#ctrlBoxBias").title = biasFull;
  $("#ctrlBoxFatigue").title = $("#ctrlBoxFatigue").textContent;

  renderBoxActionStats(rows);
  renderBoxCueLog();
  renderBoxSummary();
}

function reportActionRows(report) {
  if (!report) return [];
  if (Array.isArray(report.per_action)) return report.per_action;
  if (report.by_action && typeof report.by_action === "object") {
    return Object.entries(report.by_action).map(([action, value]) => ({ action, ...value }));
  }
  return [];
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
    const hand = cue.hand ? `<small>${esc(boxingCueHand(cue, S.stance))}</small>` : "";
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
  const reps = report.cues ?? report.n
    ?? Math.max(S.box.reps, Object.values(S.box.actions).reduce((n, b) => n + b.n, 0));
  const ghost = ghostLabel(meta.ghost);
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="box-summary-head">
      <span>Session report</span>
      <span>${stars}${chest ? ` ${BADGE_CHEST} ${esc(chest)}` : ""}</span>
    </div>
    <div class="box-summary-grid">
      <span>Accuracy</span><b>${fmtPct(report.accuracy)}</b>
      <span>Cues</span><b>${reps || "--"}</b>
      <span>Median RT</span><b>${report.median_rt_ms != null ? `${fmtMs(report.median_rt_ms)} ms` : "--"}</b>
      <span>XP</span><b>${report.xp ?? "--"}</b>
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
  const phaseClass = {
    ready: "live", arm: "warn", wait: "warn", response: "live", result: "live",
  }[S.phase] || "";
  const chip = $("#phaseChip");
  chip.textContent = S.phase;
  chip.className = "chip " + phaseClass;
  const boxChip = $("#boxPhaseChip");
  if (boxChip) {
    boxChip.textContent = S.phase;
    boxChip.className = "chip " + phaseClass;
  }
  const setChip = $("#boxSetChip");
  if (setChip) {
    setChip.textContent = S.phase === "idle" ? "no set" : S.phase;
    setChip.className = "chip " + (S.phase === "idle" ? "" : phaseClass);
  }

  const box = S.mode === "boxing";
  const start = $("#startBtn");
  const boxStart = $("#boxStartBtn");
  const idleLabel = box ? `START SET <kbd>SPACE</kbd>` : `START REP <kbd>SPACE</kbd>`;
  const activeLabel = box
    ? `${{ arm: "GET SET", wait: "WAIT FOR CUE", response: "CUE", result: "RESULT" }[S.phase] || S.phase.toUpperCase()}…`
    : `${(S.phase === "response" ? "GO" : S.phase).toUpperCase()}…`;
  if (start) {
    start.disabled = S.phase !== "idle";
    start.innerHTML = S.phase === "idle" ? idleLabel : activeLabel;
  }
  if (boxStart) {
    boxStart.disabled = S.phase !== "idle";
    boxStart.innerHTML = S.phase === "idle" ? idleLabel : activeLabel;
  }

  const stance = $("#stanceSelect");
  if (stance) stance.disabled = S.phase !== "idle";
  const ghost = $("#ghostSelect");
  if (ghost) ghost.disabled = S.phase !== "idle";

  updateArenaButtons();

  if (box) {
    const hint = S.phase === "idle"
      ? "Step back so your head and both hands are in frame. Pick a stance and ghost, then start a set."
      : S.state.msg || ({
        arm: "Get set. Stand in frame.",
        wait: "Cue is being scheduled.",
        response: "Cue is live. Match it.",
        result: "Scoring the cue.",
      }[S.phase] || "");
    $("#boxHint").textContent = hint;
    $("#hint").textContent = hint;
    return;
  }

  const hint = S.state.msg || (S.phase === "idle" ? "Ready." : "");
  if (hint) $("#hint").textContent = S.phase === "idle"
    ? "Ready. Start a rep. The laptop camera is the sensor."
    : hint;

  if (S.room?.status === "active" && S.room.turn_name) {
    const turn = S.room.players.find((p) => p.id === S.room.turn);
    if (turn?.bot) $("#hint").textContent = "BOT is taking its rep…";
    else if (S.phase === "idle") $("#hint").textContent = `Next up: ${S.room.turn_name}`;
  }
}

function renderBoard(rows) {
  if (!rows) return;
  const boxingMode = S.mode === "boxing";
  const html = rows.length
    ? rows.map((r, i) => {
      const score = boxingMode
        ? `${r.accuracy != null ? fmtPct(r.accuracy) : "--"} <small>${r.median_rt_ms != null ? `${fmtMs(r.median_rt_ms)} ms` : ""}</small>`
        : `${fmtMs(r.best_ms)}<small>ms</small>`;
      const title = boxingMode
        ? `${r.cues ?? 0} cues · ${r.correct ?? 0} correct · median ${r.median_rt_ms != null ? `${fmtMs(r.median_rt_ms)} ms` : "--"}`
        : `median ${fmtMs(r.median_ms)} ms · ${r.reps} reps`;
      return `
      <li class="${r.player_id === S.playerId ? "me" : ""}" title="${esc(title)}">
        <span class="rank">${String(i + 1).padStart(2, "0")}</span>
        <span class="name">${esc(r.name)}${r.demo ? '<span class="tag">demo</span>' : ""}</span>
        <span class="score">${score}</span>
      </li>`;
    }).join("")
    : `<li><span class="rank">–</span><span class="name">No scores yet</span><span class="score">--</span></li>`;
  const race = $("#board");
  if (race) race.innerHTML = html;
  const boxing = $("#boxBoard");
  if (boxing) boxing.innerHTML = html;
}

async function refreshBoard() {
  try {
    renderBoard(await api(`/api/leaderboard?mode=${encodeURIComponent(S.mode)}`));
  } catch {}
}

async function refreshBoxingProfile() {
  if (!S.playerId) return;
  try {
    const data = await api(`/api/players/${encodeURIComponent(S.playerId)}/boxing`);
    S.box.profile = data.stats || null;
    renderBoxPanel();
  } catch {
    S.box.profile = null;
    renderBoxPanel();
  }
}

async function refreshPlayer() {
  if (!S.playerId) return;
  try {
    const data = await api(`/api/players/${encodeURIComponent(S.playerId)}`);
    S.player = data.player || data;
    S.players = S.players.map((p) => (p.id === S.player.id ? { ...p, ...S.player } : p));
    renderArenaChrome();
  } catch {}
}

async function refreshDailyGoal() {
  const target = S.dailyGoal || 20;
  try {
    const rows = await api("/api/history?limit=200");
    const today = new Date().toDateString();
    const count = rows.filter((r) =>
      r.player_id === S.playerId && r.created_at && new Date(r.created_at).toDateString() === today
    ).length;
    $("#consoleDaily").textContent = `${count} / ${target} reps`;
    $("#consoleDailyBar").style.width = `${Math.min(100, (count / target) * 100)}%`;
  } catch {
    $("#consoleDaily").textContent = `0 / ${target} reps`;
    $("#consoleDailyBar").style.width = "0%";
  }
}

function renderRoom() {
  const card = $("#roomCard");
  const body = $("#roomBody");
  const chip = $("#roomChip");
  const room = S.room;
  if (!room) {
    card.classList.add("hidden");
    chip.textContent = "no room";
    chip.className = "chip";
    body.innerHTML = "";
    return;
  }
  card.classList.remove("hidden");
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
  renderTipInto($("#tipBody"), tip);
  renderTipInto($("#boxTipBody"), tip);
}

function renderTipInto(el, tip) {
  if (!el || !tip) return;
  const refs = (tip.refs || []).map((raw) => {
    const r = typeof raw === "string" ? { title: raw } : raw;
    const title = r.title || r.label || r.publisher || "source";
    const meta = [r.publisher && r.publisher !== title ? r.publisher : "", r.year].filter(Boolean).join(", ");
    const label = `${esc(title)}${meta ? ` — ${esc(meta)}` : ""}`;
    return r.url
      ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${label}</a>`
      : `<span>${label}</span>`;
  }).join("");
  el.innerHTML = `
    <div class="tip-headline">${esc(tip.headline)}</div>
    <div class="tip-body">${esc(tip.body)}</div>
    ${refs ? `<div class="tip-refs">${refs}</div>` : ""}
    ${tip.disclaimer ? `<div class="disclaimer">${esc(tip.disclaimer)}</div>` : ""}`;
  el.classList.remove("hidden");
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
    renderBoxRepList(visible);
  } catch {}
}

function renderBoxRepList(rows) {
  const el = $("#boxRepList");
  if (!el) return;
  $("#boxRepChip").textContent = String(rows.length);
  if (!rows.length) {
    el.innerHTML = `<div class="box-empty">No reps yet.</div>`;
    return;
  }
  el.innerHTML = rows.slice(0, 8).map((r) => {
    const expected = boxingLabel(r.expected_action || r.expected || r.action || r.cue?.action || r.cue);
    const rt = r.reaction_ms ?? r.rt_ms;
    const outcome = r.outcome || (r.correct === true ? "correct" : r.miss ? "miss" : "wrong");
    const result = outcome === "correct" ? "Hit" : outcome === "miss" ? "Miss" : "Wrong";
    const cls = outcome === "correct" ? "box-good" : "box-bad";
    return `<div class="box-rep-row">
      <span class="br-time">${esc(fmtDate(r.created_at))}</span>
      <b>${esc(expected)}</b>
      <span class="br-rt">${rt == null ? "--" : `${fmtMs(rt)} ms`}</span>
      <span class="br-result ${cls}">${result}</span>
    </div>`;
  }).join("");
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
  const expected = r.expected_action || r.expected || r.action || r.cue?.action || r.cue || "";
  const detected = r.detected_action || r.detected || "";
  const cue = r.correct === false && detected
    ? `${boxingLabel(expected)} → ${boxingLabel(detected)}`
    : boxingLabel(expected);
  const rt = r.reaction_ms ?? r.rt_ms;
  const outcome = r.outcome || (r.correct === true ? "correct" : r.miss ? "miss" : "wrong");
  const acc = outcome === "correct" ? "HIT" : outcome === "miss" ? "MISS" : "WRONG";
  const cls = outcome === "correct" ? "g-G" : outcome === "miss" ? "g-R" : "g-Y";
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
  let ghosts = [];
  if (S.playerId) {
    try {
      const data = await api(`/api/ghosts?player_id=${encodeURIComponent(S.playerId)}&mode=boxing&limit=8`);
      ghosts = (data.ghosts || []).map((g) => ({
        id: g.session_id,
        player: g.player_name || S.player?.name || "Athlete",
        at: g.started_at || "",
        cues: g.summary?.cues ?? (Array.isArray(g.trace) ? g.trace.length : 0),
        median: g.summary?.median_rt_ms ?? null,
        accuracy: g.summary?.accuracy ?? null,
      }));
    } catch {}
  }

  // Defensive fallback for an older backend without /api/ghosts.
  if (!ghosts.length) {
    try {
      const rows = await api("/api/history?limit=160&mode=boxing");
      const sessions = new Map();
      for (const r of rows) {
        if (!r.session_id) continue;
        if (typeof r.mode === "string" && r.mode !== "boxing") continue;
        let s = sessions.get(r.session_id);
        if (!s) {
          s = { id: r.session_id, player: r.player_name || "Athlete", at: r.created_at || "", rts: [] };
          sessions.set(r.session_id, s);
        }
        const rt = r.reaction_ms ?? r.rt_ms;
        if (rt != null && Number.isFinite(Number(rt))) s.rts.push(Number(rt));
        if (r.created_at && r.created_at > s.at) s.at = r.created_at;
      }
      ghosts = [...sessions.values()]
        .map((s) => ({
          id: s.id,
          player: s.player,
          at: s.at,
          cues: s.rts.length,
          median: median(s.rts),
          accuracy: null,
        }))
        .filter((s) => s.cues > 0)
        .sort((a, b) => String(b.at).localeCompare(String(a.at)))
        .slice(0, 8);
    } catch {}
  }

  S.ghosts = ghosts;
  const select = $("#ghostSelect");
  if (!select) return;
  select.innerHTML = `<option value="">Solo / personal best</option>` + S.ghosts.map((s) =>
    `<option value="${esc(s.id)}">${esc(s.player)} · ${esc(fmtDate(s.at))} · ${s.accuracy != null ? fmtPct(s.accuracy) : `${s.cues} cues`} · ${s.median != null ? `${fmtMs(s.median)} ms` : "no RT"}</option>`
  ).join("");
  if (S.ghosts.some((s) => s.id === S.ghostSessionId)) select.value = S.ghostSessionId;
  else { S.ghostSessionId = ""; select.value = ""; }
  select.disabled = S.phase !== "idle";
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
  const n = clampNumber(stars, 0, 3, 0);
  const imgs = Array.from({ length: 3 }, (_, i) =>
    `<img class="box-star ${i < n ? "" : "off"}" src="/static/assets/badge-star.svg" alt="">`
  ).join("");
  return `<span class="box-stars" role="img" aria-label="${n} of 3 stars">${imgs}</span>`;
}

function chestParts(chest) {
  if (chest === null || chest === undefined || chest === false) return { title: "", detail: "" };
  if (typeof chest === "string") return { title: chest, detail: "" };
  if (typeof chest === "boolean") return { title: chest ? "Chest" : "", detail: "" };
  if (typeof chest === "object") {
    const tier = chest.tier && chest.tier !== "none" ? String(chest.tier) : "";
    const title = String(chest.title || chest.name || chest.label || chest.id || "") || (tier ? `${tier} chest` : "");
    const detail = String(chest.detail || (tier && !chest.title ? tier : "") || "");
    return { title, detail };
  }
  return { title: String(chest), detail: "" };
}

function chestLabel(chest) {
  return chestParts(chest).title;
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

function formatProfileBias(profile, compact = true) {
  if (!profile) return "--";
  const lead = profile.lead_accuracy;
  const rear = profile.rear_accuracy;
  if (lead != null || rear != null) {
    const bits = [
      `L ${lead != null ? fmtPct(lead) : "--"}`,
      `R ${rear != null ? fmtPct(rear) : "--"}`,
    ];
    if (!compact) {
      if (profile.lead_bias != null) {
        const bias = Number(profile.lead_bias);
        bits.push(`${bias >= 0 ? "lead" : "rear"} +${Math.abs(Math.round(bias * 100))}%`);
      } else if (profile.bias) {
        bits.push(String(profile.bias));
      }
    }
    return bits.join(" · ");
  }
  return profile.bias ? String(profile.bias) : "--";
}

function formatLeadRearFromHands(compact = false) {
  const lead = median(S.box.handRts.lead);
  const rear = median(S.box.handRts.rear);
  if (lead == null && rear == null) return "--";
  const bits = [
    `L ${lead != null ? fmtMs(lead) : "--"}`,
    `R ${rear != null ? fmtMs(rear) : "--"}`,
  ];
  if (!compact && lead != null && rear != null) bits.push(fmtSigned(rear - lead));
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
    const direct = value.pct ?? value.percent ?? value.delta_pct;
    if (direct != null && Number.isFinite(Number(direct))) {
      const pct = Math.round(Number(direct) * 10) / 10;
      return `${pct > 0 ? "+" : ""}${pct}%`;
    }
    const n = value.slowing_pct ?? value.value;
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
  $("#raceConsole").classList.toggle("hidden", mode === "boxing");
  $("#boxConsole").classList.toggle("hidden", mode !== "boxing");

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
  const boxChip = $("#boxBoardChip");
  if (boxChip) {
    boxChip.textContent = mode;
    boxChip.className = "chip " + (mode === "boxing" ? "purple" : "live");
  }

  if (mode === "boxing") {
    hideOverlay();
    renderArenaChrome();
    renderConsoleChrome();
  } else {
    closeArena();
  }
  syncCameraStreams();

  if (changed) toast(mode === "boxing" ? "Boxing mode" : "Racing mode");
  renderPhase();
  renderBoxPanel();
  updateArenaButtons();
  if (refresh && changed) {
    refreshBoard();
    refreshHistory();
    refreshGhosts();
    refreshDailyGoal();
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
/* boxing arena (laptop webcam + laptop screen is the demo surface)   */
/* ------------------------------------------------------------------ */
function openArena() {
  if (!S.arenaOpen) {
    S.arenaOpen = true;
    document.body.classList.add("arena-open");
    $("#arena").classList.remove("hidden");
    const video = $("#arenaVideo");
    if (!video.getAttribute("src")) video.src = "/video";
    renderArenaChrome();
    hideArenaReport();
    setArenaIdle();
    updateArenaButtons();
    startArenaTimer({ reset: S.phase === "idle" });
  }
  updateArenaChrome();
  syncCameraStreams();
}

function closeArena() {
  if (!S.arenaOpen) return;
  S.arenaOpen = false;
  document.body.classList.remove("arena-open");
  $("#arena").classList.add("hidden");
  $("#arenaVideo").removeAttribute("src");
  hideArenaReport();
  clearArenaCue();
  setArenaEdge("");
  stopArenaTimer();
  updateArenaChrome();
  syncCameraStreams();
}

function syncCameraStreams() {
  const race = $("#video");
  const box = $("#boxCam");
  const arena = $("#arenaVideo");
  if (S.arenaOpen) {
    race?.removeAttribute("src");
    box?.removeAttribute("src");
    if (arena && !arena.getAttribute("src")) arena.src = "/video";
    return;
  }
  if (arena) arena.removeAttribute("src");
  if (S.mode === "boxing") {
    race?.removeAttribute("src");
    if (box && !box.getAttribute("src")) box.src = "/video";
  } else {
    box?.removeAttribute("src");
    if (race && !race.getAttribute("src")) race.src = "/video";
  }
}

function toggleArena() {
  if (S.arenaOpen) closeArena();
  else {
    if (S.mode !== "boxing") setMode("boxing");
    openArena();
  }
}

function updateArenaChrome() {
  const btn = $("#arenaBtn");
  if (btn) {
    btn.textContent = S.arenaOpen ? "EXIT ARENA" : "ARENA";
    btn.setAttribute("aria-pressed", String(S.arenaOpen));
  }
  document.body.classList.toggle("arena-open", S.arenaOpen);
  updateArenaButtons();
}

function renderArenaChrome() {
  if (!$("#arenaWho")) return;
  const level = S.player?.level?.level ?? S.player?.level ?? 1;
  $("#arenaWho").textContent = S.player?.name || "Athlete";
  $("#arenaLevel").textContent = `LVL ${level}`;
  $("#arenaStreak").textContent = S.player?.streak ?? 0;
  $("#arenaStanceLabel").textContent = S.stance === "southpaw" ? "Southpaw" : "Orthodox";
  renderConsoleChrome();
}

function renderConsoleChrome() {
  if (!$("#consoleName")) return;
  const level = S.player?.level?.level ?? S.player?.level ?? 1;
  $("#consoleName").textContent = S.player?.name || "Athlete";
  $("#consoleLevel").textContent = `LVL ${level}`;
  $("#consoleStreak").textContent = S.player?.streak ?? 0;
  const chip = $("#boxBoardChip");
  if (chip) {
    chip.textContent = S.mode;
    chip.className = "chip " + (S.mode === "boxing" ? "purple" : "live");
  }
}

function updateArenaButtons() {
  const start = $("#arenaStart");
  if (!start) return;
  start.disabled = S.phase !== "idle";
  start.innerHTML = S.phase === "idle"
    ? `START SET <kbd>SPACE</kbd>`
    : `${{ arm: "GET SET", wait: "WAIT FOR CUE", response: "CUE LIVE", result: "RESULT" }[S.phase] || S.phase.toUpperCase()}…`;
}

function updateArenaTimer() {
  const elapsed = S.box.setStartedAt ? performance.now() - S.box.setStartedAt : 0;
  const total = Math.floor(elapsed / 1000);
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  $("#arenaTimer").textContent = `${mm}:${ss}`;
}

function startArenaTimer({ reset = false } = {}) {
  if (reset || !S.box.setStartedAt) S.box.setStartedAt = performance.now();
  updateArenaTimer();
  if (S.box.timerInterval) return;
  S.box.timerInterval = setInterval(updateArenaTimer, 250);
}

function stopArenaTimer() {
  if (S.box.timerInterval) {
    clearInterval(S.box.timerInterval);
    S.box.timerInterval = null;
  }
}

function resetArenaSession() {
  setArenaCombo(0);
  setArenaXp(0);
  setArenaAccuracy(null);
  setArenaMascot("idle");
  hideArenaVerdict();
  hideArenaReward();
  $("#arenaRepLabel").textContent = "Set ready";
  $("#arenaLast").textContent = "Step back so your head and both hands are in frame. Press Start set.";
}

function setArenaIdle(word = "READY", hand = "Head and both hands in frame") {
  const w = $("#boWord");
  w.textContent = word;
  w.classList.remove("box-good", "box-slow", "box-bad", "box-false", "long");
  w.classList.add("idle");
  const h = $("#boHand");
  h.textContent = hand;
  h.classList.remove("idle");
  $("#boArrowWrap").classList.remove("show");
  $$(".arena-ring").forEach((ring) => ring.classList.remove("on"));
  $("#arenaCountdownWrap").classList.add("hidden");
  $("#boTimerBar").classList.remove("run");
  hideArenaVerdict();
  hideArenaReward();
  setArenaMascot("idle");
  if (!S.box.report) $("#arenaRepLabel").textContent = "No set";
}

function runArenaCue(msg) {
  clearArenaCue();
  hideArenaReport();
  hideArenaVerdict();
  hideArenaReward();
  setArenaEdge("");
  setArenaIdle("READY", "WATCH THE LINE");

  const cue = msg.cue || {};
  const windowMs = clampNumber(msg.window_ms, 400, 5000, 1400);
  const delay = clampNumber(msg.delay_ms, 0, 5000, 650);
  const shouldAck = msg.display !== "player";
  const progress = msg.progress || null;
  $("#arenaRepLabel").textContent = progress
    ? `Rep ${Math.min(progress.done + 1, progress.total)}/${progress.total}`
    : msg.rep != null ? `Rep ${msg.rep}` : "Set live";
  $("#arenaLast").textContent = "Watch the line. The cue is coming.";
  if (progress?.accuracy != null) {
    S.box.accuracy = progress.accuracy;
    setArenaAccuracy(progress.accuracy);
  }

  S.box.cuePainted = false;
  S.box.timers.push(setTimeout(() => {
    const label = cue.label || boxingLabel(cue.action);
    const side = cue.hand_side || boxingHandSide(cue.hand, cue.stance || S.stance);
    const word = $("#boWord");
    word.textContent = label;
    word.classList.remove("idle", "box-good", "box-slow", "box-bad", "box-false");
    word.classList.toggle("long", label.length > 6);
    $("#boHand").textContent = boxingCueHand(cue, cue.stance || S.stance) || "MATCH THE CUE";
    $("#boArrowWrap").dataset.side = side;
    $("#boArrowWrap").classList.add("show");
    setArenaRings(side);

    const tPaint = performance.now();
    if (shouldAck) ws.send({ type: "painted", ts: tPaint, clock: CLOCK_ID });
    S.box.cuePainted = true;

    $("#arenaCountdownWrap").classList.remove("hidden");
    $("#boCount").textContent = `${(windowMs / 1000).toFixed(1)}s`;
    $("#arenaMsLabel").textContent = "Window";
    $("#arenaMsValue").textContent = String(Math.round(windowMs));
    const bar = $("#boTimerBar");
    bar.classList.remove("run");
    bar.style.animationDuration = `${windowMs}ms`;
    void bar.offsetWidth;
    bar.classList.add("run");
    $("#arenaLast").textContent = cue.cue || "Match the cue.";

    const end = performance.now() + windowMs;
    S.box.countTimer = setInterval(() => {
      const left = Math.max(0, end - performance.now());
      $("#boCount").textContent = `${(left / 1000).toFixed(1)}s`;
      $("#arenaMsValue").textContent = String(Math.round(left));
      if (left <= 0) {
        clearInterval(S.box.countTimer);
        S.box.countTimer = null;
      }
    }, 100);
  }, delay));
}

function clearArenaCue() {
  S.box.timers.forEach(clearTimeout);
  S.box.timers = [];
  if (S.box.countTimer) {
    clearInterval(S.box.countTimer);
    S.box.countTimer = null;
  }
  S.box.cuePainted = false;
  $("#boTimerBar").classList.remove("run");
  $("#arenaCountdownWrap").classList.add("hidden");
  $("#boArrowWrap").classList.remove("show");
  $$(".arena-ring").forEach((ring) => ring.classList.remove("on"));
}

function setArenaRings(side) {
  const active = side === "left" || side === "right" ? side : "center";
  $$(".arena-ring").forEach((ring) => {
    ring.classList.toggle("on", ring.dataset.side === active);
  });
}

function renderArenaResult(msg, tone, early) {
  if (!$("#arena")) return;
  const cue = findCue(msg.rep);
  const expected = boxingLabel(msg.expected_action || msg.expected || cue?.action);
  const detected = early
    ? "EARLY"
    : msg.detected_action || msg.detected
      ? boxingLabel(msg.detected_action || msg.detected)
      : msg.correct
        ? expected
        : "—";
  const rt = msg.reaction_ms ?? msg.rt_ms ?? null;
  const progress = msg.progress || null;

  const word = $("#boWord");
  word.textContent = expected;
  word.classList.remove("idle", "box-good", "box-slow", "box-bad", "box-false");
  word.classList.toggle("long", expected.length > 6);
  word.classList.add(`box-${tone.key}`);
  $("#boHand").textContent = cue?.hand ? boxingCueHand(cue, S.stance) : "MATCH THE CUE";
  if (cue?.hand) setArenaRings(cue.hand_side || boxingHandSide(cue.hand, cue.stance || S.stance));

  $("#arenaExpected").textContent = `EXPECTED ${expected}`;
  $("#arenaDetected").textContent = early
    ? tone.word
    : msg.outcome === "miss"
      ? "NO ACTION"
      : `DETECTED ${detected}`;
  $("#arenaMark").innerHTML = msg.correct ? SVG_CHECK : SVG_CROSS;
  $("#arenaRt").textContent = rt == null ? "--" : fmtMs(rt);
  $("#arenaGrade").textContent = `${tone.grade} · ${tone.word}`;
  $("#arenaGrade").className = `arena-result-grade box-${tone.key}`;
  $("#arenaGhost").textContent = msg.ghost_delta == null
    ? "No ghost"
    : `${fmtSigned(msg.ghost_delta)} vs ghost`;
  const edgeColor = msg.edge?.color || (msg.correct ? "G" : msg.false_start ? "F" : "R");
  const edgeMs = msg.edge?.ms || 900;
  const result = $("#arenaResult");
  result.className = `arena-result box-${tone.key}`;
  result.classList.remove("hidden");
  $("#arena").dataset.tone = edgeTone(edgeColor);
  $("#arenaMsLabel").textContent = "RT";
  $("#arenaMsValue").textContent = rt == null ? "--" : fmtMs(rt);

  setArenaCombo(msg.combo != null ? Number(msg.combo) : (msg.correct ? S.box.combo + 1 : 0));
  setArenaXp(S.box.xp);
  if (progress?.accuracy != null) S.box.accuracy = progress.accuracy;
  else if (msg.session_stats?.accuracy != null) S.box.accuracy = msg.session_stats.accuracy;
  setArenaAccuracy(S.box.accuracy);
  setArenaReward(msg.stars, msg.chest);
  setArenaMascot(msg.mascot || arenaMascotForResult(tone, msg));
  if (progress) $("#arenaRepLabel").textContent = `Rep ${progress.done}/${progress.total}`;

  const bits = [];
  if (rt == null && msg.correct) bits.push("held guard");
  if (msg.execution_ms != null) bits.push(`exec ${fmtMs(msg.execution_ms)} ms`);
  if (msg.xp?.total) bits.push(`+${msg.xp.total} XP`);
  if (progress?.accuracy != null) bits.push(fmtPct(progress.accuracy));
  if (msg.combo != null) bits.push(`combo ${msg.combo}`);
  $("#arenaLast").textContent = bits.join(" · ") || "Cue scored.";
  flashResultEdge(edgeColor, edgeMs);
}

function arenaMascotForResult(tone, msg) {
  if (tone.key === "false" || tone.key === "bad") return "sad";
  if (Number(S.player?.streak) >= 3) return "fire";
  if (Number(msg.combo) >= 5) return "fire";
  return "happy";
}

function mascotMood(value) {
  const raw = typeof value === "string" ? value : value?.mood;
  return ({ happy: "happy", neutral: "idle", tired: "sad", fire: "fire", idle: "idle", sad: "sad" })[raw] || "idle";
}

function setArenaMascot(state) {
  const mood = mascotMood(state);
  const wrap = $("#arenaMascot");
  if (!wrap) return;
  wrap.dataset.mood = mood;
  const img = $("#arenaMascotImg");
  if (img) img.src = `/static/assets/mascot-${mood}.svg`;
}

function setArenaCombo(value) {
  const n = Math.max(0, Number(value) || 0);
  S.box.combo = n;
  $("#arenaCombo").textContent = String(n);
  $$("#arenaComboDots i").forEach((el, i) => {
    el.classList.toggle("on", i < Math.min(n, 5));
  });
}

function setArenaXp(value) {
  const n = Math.max(0, Number(value) || 0);
  S.box.xp = n;
  $("#arenaXp").textContent = `+${n}`;
  $("#arenaXpBar").style.width = `${Math.min(100, n)}%`;
}

function setArenaAccuracy(value) {
  S.box.accuracy = value;
  $("#arenaAcc").textContent = fmtPct(value);
}

function setArenaReward(stars, chest) {
  const reward = $("#arenaReward");
  let any = false;
  if (stars !== null && stars !== undefined) {
    $("#arenaStars").innerHTML = starsHtml(stars);
    any = true;
  } else {
    $("#arenaStars").innerHTML = "";
  }
  const label = chestLabel(chest);
  if (label) {
    $("#arenaChest").innerHTML = `${BADGE_CHEST}<span>${esc(label)}</span>`;
    $("#arenaChest").classList.remove("hidden");
    any = true;
  } else {
    $("#arenaChest").innerHTML = "";
    $("#arenaChest").classList.add("hidden");
  }
  reward.classList.remove("hidden");
  reward.classList.toggle("is-empty", !any);
}

function hideArenaVerdict() {
  const result = $("#arenaResult");
  if (result) result.className = "arena-result hidden";
  const mark = $("#arenaMark");
  if (mark) mark.innerHTML = "";
  const arena = $("#arena");
  if (arena) arena.dataset.tone = "";
  $("#arenaMsLabel").textContent = "Window";
  $("#arenaMsValue").textContent = "--";
}

function hideArenaReward() {
  const reward = $("#arenaReward");
  reward.classList.add("is-empty");
  reward.classList.remove("hidden");
  $("#arenaStars").innerHTML = "";
  $("#arenaChest").innerHTML = "";
}

const EDGE_TONES = { G: "good", Y: "slow", R: "bad", F: "false" };

function edgeTone(color) {
  return EDGE_TONES[String(color || "G").toUpperCase()] || "good";
}

/**
 * Prefer the shared hardware edge module when it is present. Its full-screen
 * overlay owns the flash, so the local arena edge stays quiet to avoid a
 * second overlay. The local vignette is the fallback.
 */
function flashResultEdge(color, ms = 900) {
  const c = String(color || "G").toUpperCase();
  if (window.Split2ndEdge && typeof window.Split2ndEdge.flash === "function") {
    try { window.Split2ndEdge.flash(c, ms); return; } catch {}
  }
  flashArenaEdge(edgeTone(c));
}

function flashArenaEdge(tone) {
  const el = $("#arenaEdge");
  if (!el || !tone) return;
  el.className = "box-vignette arena-edge";
  void el.offsetWidth;
  el.classList.add(`box-vignette--${tone}`, "is-flashing");
  clearTimeout(flashArenaEdge._t);
  flashArenaEdge._t = setTimeout(() => {
    el.className = "box-vignette arena-edge";
  }, 320);
}

function setArenaEdge(tone) {
  const el = $("#arenaEdge");
  if (!el) return;
  clearTimeout(flashArenaEdge._t);
  el.className = tone ? `box-vignette arena-edge box-vignette--${tone}` : "box-vignette arena-edge";
}

function renderArenaReport(msg) {
  const report = msg.report || {};
  $("#arenaReportAccuracy").textContent = fmtPct(report.accuracy);
  const starCount = clampNumber(msg.stars, 0, 3, 0);
  $("#arenaReportStars").innerHTML = starsHtml(msg.stars);
  $("#arenaReportStars").setAttribute("role", "img");
  $("#arenaReportStars").setAttribute("aria-label", `${starCount} of 3 stars`);
  const chest = chestParts(msg.chest);
  $("#arenaReportChest").innerHTML = chest.title
    ? `${BADGE_CHEST}<span>${esc(chest.title)}</span>${chest.detail ? `<small>${esc(chest.detail)}</small>` : ""}`
    : "";
  $("#arenaReportCues").textContent = report.cues ?? report.n ?? "--";
  $("#arenaReportMedian").textContent = report.median_rt_ms != null ? `${fmtMs(report.median_rt_ms)} ms` : "--";
  $("#arenaReportBias").textContent = formatLeadRearFromHands();
  $("#arenaReportFatigue").textContent = formatFatigue(report.fatigue);
  $("#arenaReportGuard").textContent = report.guard_drops ?? "--";
  $("#arenaReportBreaks").textContent = report.combo_breaks ?? "--";

  const moodLine = typeof msg.mascot === "object" ? msg.mascot?.line : "";
  const moodEl = $("#arenaReportMood");
  moodEl.textContent = moodLine || "";
  moodEl.classList.toggle("hidden", !moodLine);

  const per = reportActionRows(report);
  $("#arenaReportActions").innerHTML = per.length
    ? per.map((a) => {
      const pct = Number(a.accuracy);
      const width = Number.isFinite(pct) ? clampNumber(pct > 0 && pct <= 1 ? pct * 100 : pct, 0, 100, 0) : 0;
      const rt = a.median_rt_ms ?? a.median_rt;
      return `<div class="report-action-row">
        <span class="ra-name">${esc(boxingLabel(a.action))}</span>
        <span class="ra-bar"><i style="width:${width.toFixed(1)}%"></i></span>
        <span class="ra-acc">${fmtPct(a.accuracy)}</span>
        <span class="ra-rt">${rt != null ? `${fmtMs(rt)} ms` : "--"}</span>
      </div>`;
    }).join("")
    : `<div class="box-empty">No per-action data.</div>`;

  const tip = msg.tip || S.box.lastTip;
  const tipEl = $("#arenaReportTip");
  if (tip) {
    S.box.lastTip = tip;
    renderTipInto(tipEl, tip);
  } else {
    tipEl.classList.add("hidden");
    tipEl.innerHTML = "";
  }

  const ghost = ghostLabel(msg.ghost);
  const ghostEl = $("#arenaReportGhost");
  ghostEl.classList.toggle("hidden", !ghost);
  ghostEl.innerHTML = ghost ? `<span>Ghost</span><b>${esc(ghost)}</b>` : "";

  setArenaMascot(msg.mascot || (starCount < 2 ? "sad" : "happy"));
  if (!S.arenaOpen) return;
  S.arenaReportOpen = true;
  const el = $("#arenaReport");
  el.classList.remove("hidden");
  $("#arena").inert = true;
  el.scrollTop = 0;
}

function hideArenaReport() {
  S.arenaReportOpen = false;
  $("#arena").inert = false;
  $("#arenaReport").classList.add("hidden");
  if (S.arenaOpen && S.phase === "idle") {
    setArenaIdle("READY", "Head and both hands in frame");
    $("#arenaLast").textContent = "Step back so your head and both hands are in frame. Press Start set.";
  }
}

function toggleFullscreen() {
  const root = document.documentElement;
  if (document.fullscreenElement) {
    document.exitFullscreen?.().catch(() => {});
  } else {
    root.requestFullscreen?.().catch(() => toast("Fullscreen is unavailable in this browser"));
  }
}

/* ------------------------------------------------------------------ */
/* controls                                                           */
/* ------------------------------------------------------------------ */
function activePlayerForStart() {
  if (S.mode === "boxing") return S.playerId;
  if (S.room?.status === "active" && S.room.turn) return S.room.turn;
  return S.playerId;
}

function startBoxingSet() {
  const pid = activePlayerForStart();
  if (!pid) return toast("Add an athlete first");
  if (S.phase !== "idle") {
    toast("Set already running");
    return;
  }
  if (S.mode !== "boxing") setMode("boxing");
  openArena();

  resetBoxStats();
  resetArenaSession();
  renderArenaChrome();
  startArenaTimer({ reset: true });
  const payload = {
    cmd: "start",
    player_id: pid,
    mode: "boxing",
    stance: S.stance,
    ghost_session_id: S.ghostSessionId || null,
  };
  const sent = ws.send(payload);
  $("#hint").textContent = sent
    ? "Set queued. Stand in frame and keep your guard up."
    : "Controller is offline - reconnecting…";
  $("#arenaLast").textContent = sent
    ? "Set queued. Stand in frame. Watch the line."
    : "Controller is offline. Reconnecting…";
}

$("#startBtn").onclick = () => {
  const pid = activePlayerForStart();
  if (!pid) return toast("Add an athlete first");

  if (S.mode === "boxing") {
    startBoxingSet();
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

$("#boxStartBtn").onclick = startBoxingSet;
$("#arenaBtn").onclick = toggleArena;
$("#arenaStart").onclick = startBoxingSet;
$("#arenaExit").onclick = closeArena;
$("#arenaFull").onclick = toggleFullscreen;
$("#arenaReportNext").onclick = () => {
  startBoxingSet();
};
$("#arenaReportClose").onclick = () => {
  hideArenaReport();
  closeArena();
};

$("#ctrlMode").onclick = (e) => {
  const btn = e.target.closest("[data-mode]");
  if (!btn) return;
  if (btn.dataset.mode !== S.mode && S.phase !== "idle") {
    toast("Finish the set before switching modes");
    return;
  }
  setMode(btn.dataset.mode);
};

$("#stanceSelect").onchange = (e) => {
  S.stance = e.target.value === "southpaw" ? "southpaw" : "orthodox";
  localStorage.setItem("s2.stance", S.stance);
  renderBoxPanel();
  renderArenaChrome();
};

$("#ghostSelect").onchange = (e) => {
  S.ghostSessionId = e.target.value || "";
};

$("#clearCueLog").onclick = () => {
  resetBoxStats();
  if (S.arenaOpen) resetArenaSession();
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
  S.box.lastTip = null;
  S.box.scored.clear();
  $("#boxXpChips").classList.add("hidden");
  $("#boxXpChips").innerHTML = "";
  $("#boxLastRt").classList.add("is-empty");
  $("#boxLastRt").textContent = "—";
  $("#boxLastGrade").textContent = "—";
  $("#boxLastGrade").className = "console-grade";
  $("#boxLastMeta").textContent = "No cues yet.";
  renderBoxPanel();
}

$("#playerSelect").onchange = (e) => {
  S.playerId = e.target.value;
  localStorage.setItem("s2.player", S.playerId);
  S.player = S.players.find((p) => p.id === S.playerId) || S.player;
  ws.send({ cmd: "select", player_id: S.playerId });
  refreshBoard();
  refreshDailyGoal();
  S.box.profile = null;
  renderBoxPanel();
  refreshBoxingProfile();
  renderArenaChrome();
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

function isTypingTarget(el) {
  return !!el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
}

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (isTypingTarget(document.activeElement)) return;

  if (e.code === "Escape") {
    if (S.arenaReportOpen) {
      e.preventDefault();
      hideArenaReport();
      return;
    }
    if (S.arenaOpen) {
      e.preventDefault();
      closeArena();
    }
    return;
  }

  if (e.repeat) return;

  if (e.code === "KeyF") {
    e.preventDefault();
    toggleFullscreen();
    return;
  }
  if (e.code === "KeyB") {
    e.preventDefault();
    if (S.phase !== "idle") return toast("Finish the set before switching modes");
    setMode("boxing");
    openArena();
    return;
  }
  if (e.code === "KeyR") {
    e.preventDefault();
    if (S.phase !== "idle") return toast("Finish the set before switching modes");
    setMode("racing");
    closeArena();
    return;
  }
  if (e.code === "Space") {
    e.preventDefault();
    if (S.mode === "boxing") startBoxingSet();
    else $("#startBtn").click();
  }
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

    const camText = vis.has_preview ? "live" : "no signal";
    const camClass = "chip " + (vis.has_preview ? "live" : "warn");
    for (const chip of ["#camChip", "#boxCamChip"]) {
      const el = $(chip);
      if (el) { el.textContent = camText; el.className = camClass; }
    }
    for (const overlay of ["#nosignal", "#boxNosignal"]) {
      const el = $(overlay);
      if (el) el.style.display = vis.has_preview ? "none" : "grid";
    }
    const meta = [
      `${vis.fps || 0} fps`,
      `${vis.proc_ms || 0} ms/frame`,
      vis.pose_ok ? "pose" : "motion",
      `${vis.frames || 0} frames`,
      vis.remote_active ? "remote" : (vis.local_ok ? "laptop" : "fake"),
    ].join(" · ");
    $("#camMeta").textContent = meta;
    $("#boxCamMeta").textContent = meta;
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
refreshDailyGoal();
poll();
setInterval(poll, 2500);
