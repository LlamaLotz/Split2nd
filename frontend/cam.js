import { $, CLOCK_ID, api, connectWS, toast } from "./common.js";

const video = $("#video");
const canvas = $("#canvas");
const ctx = canvas.getContext("2d", { alpha: false });
let ws = null;
let running = false;
let frames = 0;
let bytes = 0;
let lastSent = 0;
let fpsAt = 0;
let fpsCount = 0;
let playerId = localStorage.getItem("s2.player");

$("#cUrl").textContent = location.origin;
$("#cSecure").textContent = window.isSecureContext ? "yes" : "NO - camera will be blocked";
$("#cSecure").style.color = window.isSecureContext ? "var(--green)" : "var(--red)";
$("#cGum").textContent = navigator.mediaDevices?.getUserMedia ? "available" : "missing";
$("#cGum").style.color = navigator.mediaDevices?.getUserMedia ? "var(--green)" : "var(--red)";

const logEl = $("#log");
const lines = [];
function log(line) {
  lines.unshift(`${new Date().toLocaleTimeString([], { hour12: false })}  ${line}`);
  logEl.textContent = lines.slice(0, 30).join("\n");
}

async function boot() {
  try {
    const players = await api("/api/players");
    if (!playerId || !players.some((p) => p.id === playerId)) {
      playerId = players[0]?.id || null;
    }
    if (playerId) localStorage.setItem("s2.player", playerId);
  } catch {}

  try {
    const health = await api("/api/health");
    $("#cHealth").textContent =
      `${health.rig.backend}/${health.rig.connected ? "up" : "down"} · ` +
      `${health.vision.mode} ${health.vision.fps}fps · ` +
      `${health.drill.phase}`;
  } catch (err) {
    $("#cHealth").textContent = err.message;
  }

  ws = connectWS({
    role: "camera",
    playerId,
    onStatus(status) {
      $("#cWs").textContent = status;
      $("#cWs").style.color = status === "open" ? "var(--green)" : "var(--amber)";
      log(`ws ${status}`);
    },
    onMessage(msg) {
      if (msg.type === "hello") {
        log(`hello v${msg.version} player=${msg.player?.name || "none"}`);
      } else if (msg.type !== "state" || !msg.msg) {
        log(`${msg.type} ${msg.phase || msg.message || ""}`);
      }
    },
  });
}

$("#startBtn").onclick = async () => {
  try {
    if (!window.isSecureContext) throw new Error("Not a secure context. Use https:// and trust the CA.");
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
    running = true;
    $("#hint").textContent = "Camera live. Frames are going to the server.";
    requestFrame();
    toast("Camera live");
  } catch (err) {
    $("#hint").textContent = err.message;
    toast(err.message);
  }
};

function requestFrame() {
  if (!running) return;
  if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(onFrame);
  else setTimeout(() => onFrame(performance.now()), 1000 / 30);
}

function onFrame(now) {
  if (!running) return;
  if (now - lastSent >= 33) {
    lastSent = now;
    send(now);
  }
  if (!fpsAt) fpsAt = now;
  if (now - fpsAt >= 1000) {
    $("#cFps").textContent = String(fpsCount);
    fpsCount = 0;
    fpsAt = now;
  }
  requestFrame();
}

function send(now) {
  if (!video.videoWidth || !ws?.open) return;
  if (ws.buffered > 1_400_000) return;
  const w = 640;
  const h = Math.round(video.videoHeight * (w / video.videoWidth));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  try {
    ctx.drawImage(video, 0, 0, w, h);
    const url = canvas.toDataURL("image/jpeg", 0.62);
    const jpeg = url.slice(url.indexOf(",") + 1);
    if (ws.send({ type: "frame", ts: now, clock: CLOCK_ID, jpeg })) {
      frames += 1;
      fpsCount += 1;
      bytes += jpeg.length;
      $("#cFrames").textContent = `${frames} · ${(bytes / 1048576).toFixed(1)} MB`;
      $("#cSize").textContent = `${w}x${h} · ${(jpeg.length / 1024).toFixed(0)} KB b64`;
    }
  } catch (err) {
    log(`encode error ${err.message}`);
  }
}

boot();
