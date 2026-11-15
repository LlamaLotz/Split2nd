/* Split2nd edge glow — self-contained, no imports, no WebSocket access.
 *
 *   <script src="/static/edge.js" defer></script>
 *   window.Split2ndEdge.flash("G", 900);
 *   window.Split2ndEdge.clear();
 *
 * The app result handler calls:
 *   Split2ndEdge.flash(msg.edge?.color || msg.grade, msg.edge?.ms || 900);
 *
 * G -> signal green, Y -> amber, R -> red, F -> violet (false start).
 * The script injects frontend/edge.css if the page did not link it and is
 * safe to load twice.
 */
(function () {
  "use strict";

  if (window.Split2ndEdge) return;

  var CLASSES = {
    G: "edge-good",
    Y: "edge-warn",
    R: "edge-bad",
    F: "edge-false",
    M: "edge-bad"      // boxing miss falls back to red
  };
  var ALL = ["edge-good", "edge-warn", "edge-bad", "edge-false", "is-on"];
  var DEFAULT_MS = 900;
  var timer = 0;
  var el = null;

  function ensureStyles() {
    try {
      var links = document.querySelectorAll('link[rel="stylesheet"]');
      for (var i = 0; i < links.length; i++) {
        if ((links[i].getAttribute("href") || "").indexOf("edge.css") !== -1) {
          return;
        }
      }
      var href = "/static/edge.css";
      var script = document.currentScript;
      if (script && script.src) {
        href = script.src.replace(/edge\.js(?:[?#].*)?$/, "edge.css");
      }
      var link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.setAttribute("data-split2nd-edge", "1");
      (document.head || document.documentElement).appendChild(link);
    } catch (err) {
      /* A missing stylesheet must never break the drill. */
    }
  }

  function ensureOverlay() {
    if (el && el.isConnected) return el;
    if (!document.body) return null;
    el = document.getElementById("edge-glow");
    if (!el) {
      el = document.createElement("div");
      el.id = "edge-glow";
      el.setAttribute("aria-hidden", "true");
      document.body.appendChild(el);
    }
    return el;
  }

  function keyFor(color) {
    var key = String(color == null ? "" : color).trim().toUpperCase();
    if (key === "0" || key === "OFF" || key === "NONE" || key === "CLEAR") {
      return "";
    }
    return key.charAt(0);
  }

  function clear() {
    if (timer) {
      clearTimeout(timer);
      timer = 0;
    }
    var node = el || document.getElementById("edge-glow");
    if (node) node.classList.remove("is-on");
  }

  function flash(color, ms) {
    var key = keyFor(color);
    if (!key || !CLASSES[key]) {
      clear();
      return;
    }
    var node = ensureOverlay();
    if (!node) {
      // The body does not exist yet; wait for it once, then try again.
      document.addEventListener("DOMContentLoaded", function () {
        flash(color, ms);
      }, { once: true });
      return;
    }
    var duration = Number(ms);
    if (!isFinite(duration) || duration < 0) duration = DEFAULT_MS;

    if (timer) {
      clearTimeout(timer);
      timer = 0;
    }
    node.classList.remove("edge-good", "edge-warn", "edge-bad", "edge-false");
    node.classList.add(CLASSES[key]);
    // Restart the fade if a glow is already on screen (same or new colour).
    if (node.classList.contains("is-on")) {
      node.classList.remove("is-on");
      void node.offsetWidth;
    }
    node.classList.add("is-on");
    timer = setTimeout(function () {
      timer = 0;
      node.classList.remove("is-on");
    }, duration);
  }

  ensureStyles();

  window.Split2ndEdge = {
    flash: flash,
    clear: clear
  };
})();
