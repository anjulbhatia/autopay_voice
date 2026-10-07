/* console.js — session gateway, nav, dial queue, call controls, toasts, modal */
(function () {
  "use strict";

  var QUEUE_KEY = "autopay_dial_queue";
  var SESSION_KEY = "autopay_session";
  var dialQueue = [];
  try { dialQueue = JSON.parse(window.localStorage.getItem(QUEUE_KEY) || "[]"); } catch (e) { dialQueue = []; }
  if (!Array.isArray(dialQueue)) dialQueue = [];

  function hasAnime() {
    return typeof window.anime !== "undefined";
  }

  /* ---------- session gateway ---------- */

  function sessionActive() {
    try { return window.sessionStorage.getItem(SESSION_KEY) === "active"; }
    catch (e) { return true; }
  }

  function enterConsole() {
    var name = document.getElementById("gateway-name");
    try {
      window.sessionStorage.setItem(SESSION_KEY, "active");
      if (name && name.value.trim()) window.sessionStorage.setItem("autopay_operator", name.value.trim());
    } catch (e) {}
    var gate = document.getElementById("gateway");
    if (gate) gate.classList.add("hidden");
  }

  function endSession() {
    try { window.sessionStorage.removeItem(SESSION_KEY); } catch (e) {}
    closeMenu();
    var gate = document.getElementById("gateway");
    if (gate) gate.classList.remove("hidden");
  }

  /* ---------- dial queue ---------- */

  function saveQueue() {
    try { window.localStorage.setItem(QUEUE_KEY, JSON.stringify(dialQueue)); } catch (e) {}
  }

  function syncQueueUI() {
    var count = document.getElementById("q-count");
    var next = document.getElementById("q-next");
    var chip = document.getElementById("dial-chip");
    if (count) count.textContent = dialQueue.length + " in dial queue" + (dialQueue.length ? ": " + dialQueue.slice(0, 3).join(" → ") + (dialQueue.length > 3 ? " +" + (dialQueue.length - 3) : "") : "");
    if (next) next.disabled = dialQueue.length === 0;
    if (chip) {
      chip.classList.toggle("hidden", dialQueue.length === 0);
      chip.textContent = dialQueue.length ? "Up next: " + dialQueue[0] + (dialQueue.length > 1 ? " +" + (dialQueue.length - 1) : "") : "";
    }
    document.querySelectorAll(".qpick").forEach(function (box) {
      box.checked = dialQueue.indexOf(box.getAttribute("data-cid")) !== -1;
    });
    var all = document.getElementById("q-all");
    if (all) {
      var boxes = document.querySelectorAll(".qpick");
      all.checked = boxes.length > 0 && Array.prototype.every.call(boxes, function (b) { return b.checked; });
    }
  }

  function addToQueue(cid) {
    if (dialQueue.indexOf(cid) === -1) dialQueue.push(cid);
    saveQueue(); syncQueueUI();
  }

  function removeFromQueue(cid) {
    dialQueue = dialQueue.filter(function (c) { return c !== cid; });
    saveQueue(); syncQueueUI();
  }

  function clearQueue() {
    dialQueue = [];
    saveQueue(); syncQueueUI();
  }

  /* ---------- toast notifications ---------- */

  function toast(html) {
    var stack = document.getElementById("console-msg");
    if (!stack) return;
    var card = document.createElement("div");
    card.className = "pointer-events-auto";
    card.innerHTML = html;
    stack.appendChild(card);
    while (stack.children.length > 3) stack.removeChild(stack.firstChild);
    if (hasAnime()) window.anime.animate(card, { translateY: [12, 0], opacity: [0, 1], duration: 220, easing: "easeOutCubic" });
    setTimeout(function () {
      if (!card.parentNode) return;
      if (hasAnime()) {
        window.anime.animate(card, {
          opacity: [1, 0], duration: 250,
          onComplete: function () { if (card.parentNode) card.parentNode.removeChild(card); },
        });
      } else if (card.parentNode) card.parentNode.removeChild(card);
    }, 6000);
    updateTabTitle();
  }

  function updateTabTitle() {
    var chip = document.getElementById("dial-chip");
    var pending = chip && !chip.classList.contains("hidden") ? chip.textContent : "";
    document.title = pending ? "(" + dialQueue.length + ") Merchant Console - Autopay" : "Merchant Console - Autopay";
  }

  function postJSON(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) { return r.text(); });
  }

  function dialOptions() {
    var mode = document.getElementById("dial-mode");
    var to = document.getElementById("dial-to");
    var confirm = document.getElementById("dial-confirm");
    return {
      mode: mode ? mode.value : "web",
      to_number: to ? to.value.trim() : "",
      confirm: confirm && confirm.checked ? "true" : "",
    };
  }

  function showMsg(html) {
    toast(html);
  }

  function refreshTables() {
    ["#queue-table", "#obs-calls-table", "#customers-table", "#obs-handoffs-table", "#obs-audit-table"].forEach(function (sel) {
      var el = document.querySelector(sel);
      if (el && window.htmx) window.htmx.trigger(el, "refresh");
    });
    refreshActive();
  }

  function refreshActive() {
    var el = document.getElementById("active-call");
    if (el && window.htmx) window.htmx.ajax("GET", "/partials/active-call", { target: "#active-call", swap: "innerHTML" });
  }

  function startNext() {
    if (!dialQueue.length) return;
    var cid = dialQueue[0];
    var opts = dialOptions();
    postJSON("/partials/queue/start", { customer_id: cid, mode: opts.mode, to_number: opts.to_number, confirm: opts.confirm }).then(function (html) {
      showMsg(html);
      if (html.indexOf("open") !== -1 || html.indexOf("dialing") !== -1) {
        dialQueue.shift();
        saveQueue();
      }
      syncQueueUI();
      refreshActive();
    });
  }

  function stopActive() {
    postJSON("/partials/calls/stop-active", {}).then(function (html) {
      showMsg(html);
      refreshActive();
    });
  }

  var nav_on = ["bg-primary", "text-primary-foreground", "font-semibold"];
  var nav_off = ["text-neutral-500"];

  function toggleLogs(force) {
    var drawer = document.getElementById("logs-drawer");
    var show = typeof force === "boolean" ? force : drawer.classList.contains("hidden");
    drawer.classList.toggle("hidden", !show);
    if (show && hasAnime()) {
      window.anime.animate(drawer, { translateX: [40, 0], opacity: [0, 1], duration: 220, easing: "easeOutCubic" });
    }
  }

  function showSection(name) {
    document.querySelectorAll("[data-nav]").forEach(function (btn) {
      var on = btn.getAttribute("data-nav") === name;
      nav_on.forEach(function (c) { btn.classList.toggle(c, on); });
      nav_off.forEach(function (c) { btn.classList.toggle(c, !on); });
    });
    document.querySelectorAll("[data-section]").forEach(function (sec) {
      var on = sec.getAttribute("data-section") === name;
      sec.classList.toggle("hidden", !on);
      if (sec.getAttribute("data-section") === "customers") sec.classList.toggle("flex", on);
      if (sec.getAttribute("data-section") === "observability") {
        sec.classList.toggle("flex", on);
        sec.classList.toggle("sm:grid", on);
        sec.classList.toggle("sm:grid-cols-2", on);
      }
      if (on && hasAnime()) {
        window.anime.animate(sec, { opacity: [0, 1], translateY: [12, 0], duration: 280, easing: "easeOutCubic" });
      }
    });
  }

  function openModal() {
    var overlay = document.getElementById("modal");
    overlay.classList.remove("hidden");
    if (hasAnime()) {
      window.anime.animate("#modal-card", { scale: [0.94, 1], opacity: [0, 1], duration: 220, easing: "easeOutCubic" });
      window.anime.animate("#modal", { opacity: [0, 1], duration: 160 });
    }
  }

  function closeModal() {
    document.getElementById("modal").classList.add("hidden");
    document.getElementById("modal-body").innerHTML = "";
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!sessionActive()) {
      var gate = document.getElementById("gateway");
      if (gate) gate.classList.remove("hidden");
    } else {
      var g = document.getElementById("gateway");
      if (g) g.classList.add("hidden");
    }
    showSection("calls");
    syncQueueUI();
    updateTabTitle();
    document.querySelectorAll("[data-nav]").forEach(function (btn) {
      btn.addEventListener("click", function () { showSection(btn.getAttribute("data-nav")); });
    });
    document.getElementById("modal-backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { closeModal(); }
    });
    document.getElementById("profile-ball").addEventListener("click", function (event) {
      event.stopPropagation();
      document.getElementById("profile-menu").classList.toggle("hidden");
    });
    document.addEventListener("click", closeMenu);

    document.addEventListener("change", function (event) {
      var box = event.target.closest ? event.target.closest(".qpick") : null;
      if (box) {
        var cid = box.getAttribute("data-cid");
        if (box.checked) addToQueue(cid); else removeFromQueue(cid);
        return;
      }
      if (event.target && event.target.id === "q-all") {
        document.querySelectorAll(".qpick").forEach(function (b) {
          var c = b.getAttribute("data-cid");
          if (event.target.checked) { if (dialQueue.indexOf(c) === -1) dialQueue.push(c); }
          else { dialQueue = dialQueue.filter(function (x) { return x !== c; }); }
        });
        saveQueue(); syncQueueUI();
      }
    });

    if (window.htmx) {
      document.body.addEventListener("htmx:afterSwap", function (event) {
        if (event.target.id === "console-msg") {
          var stack = event.target;
          while (stack.children.length > 3) stack.removeChild(stack.firstChild);
          updateTabTitle();
          refreshActive();
          ["#queue-table", "#obs-calls-table", "#obs-handoffs-table"].forEach(function (sel) {
            var el = document.querySelector(sel);
            if (el) window.htmx.trigger(el, "refresh");
          });
        }
        if (event.target.id === "queue-table" || event.target.id === "active-call") {
          syncQueueUI();
        }
      });

    }
  });

  function closeMenu() {
    var menu = document.getElementById("profile-menu");
    if (menu) menu.classList.add("hidden");
  }

  window.openModal = openModal;
  window.closeModal = closeModal;
  window.showSection = showSection;
  window.toggleLogs = toggleLogs;
  window.enterConsole = enterConsole;
  window.endSession = endSession;
  window.startNext = startNext;
  window.stopActive = stopActive;
  window.clearQueue = clearQueue;
  window.refreshActive = refreshActive;
})();
