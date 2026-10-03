/* console.js — nav, dial queue, active-call refresh, modal for console.html */
(function () {
  "use strict";

  var QUEUE_KEY = "autopay_dial_queue";
  var dialQueue = [];
  try { dialQueue = JSON.parse(window.localStorage.getItem(QUEUE_KEY) || "[]"); } catch (e) { dialQueue = []; }
  if (!Array.isArray(dialQueue)) dialQueue = [];

  function hasAnime() {
    return typeof window.anime !== "undefined";
  }

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
      chip.textContent = dialQueue.length ? "up next: " + dialQueue[0] + (dialQueue.length > 1 ? " +" + (dialQueue.length - 1) : "") : "";
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

  function postJSON(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) { return r.text(); });
  }

  function showMsg(html) {
    var msg = document.getElementById("console-msg");
    if (msg) {
      msg.innerHTML = html;
      if (hasAnime()) window.anime.animate(msg, { translateX: [0, -6, 6, 0], duration: 300 });
    }
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
    postJSON("/partials/queue/start", { customer_id: cid }).then(function (html) {
      showMsg(html);
      if (html.indexOf("open") !== -1) {
        dialQueue.shift();
        saveQueue();
      }
      syncQueueUI();
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

  function fillBaseUrl(scope) {
    var saved = "";
    try { saved = window.localStorage.getItem("autopay_base_url") || ""; } catch (e) {}
    if (!saved) return;
    (scope || document).querySelectorAll('input[name="base_url"]').forEach(function (input) {
      if (!input.value) input.value = saved;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    showSection("calls");
    syncQueueUI();
    document.querySelectorAll("[data-nav]").forEach(function (btn) {
      btn.addEventListener("click", function () { showSection(btn.getAttribute("data-nav")); });
    });
    document.getElementById("modal-backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { closeModal(); closeSettings(); }
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

    try {
      var saved = window.localStorage.getItem("autopay_base_url") || "";
      if (saved) fillBaseUrl(document);
    } catch (e) {}
    if (window.htmx) {
      document.body.addEventListener("htmx:afterSwap", function (event) {
        if (event.target.id === "console-msg") {
          if (hasAnime()) window.anime.animate(event.target, { translateX: [0, -6, 6, 0], duration: 300 });
          refreshActive();
          ["#queue-table", "#obs-calls-table", "#obs-handoffs-table"].forEach(function (sel) {
            var el = document.querySelector(sel);
            if (el) window.htmx.trigger(el, "refresh");
          });
        }
        if (event.target.id === "queue-table" || event.target.id === "active-call") {
          syncQueueUI();
          fillBaseUrl(event.target);
        }
      });

    }
  });

  function openSettings() {
    closeMenu();
    var saved = "";
    try { saved = window.localStorage.getItem("autopay_base_url") || ""; } catch (e) {}
    document.getElementById("settings-base-url").value = saved;
    document.getElementById("settings-modal").classList.remove("hidden");
  }

  function closeSettings() {
    document.getElementById("settings-modal").classList.add("hidden");
  }

  function saveSettings() {
    var value = document.getElementById("settings-base-url").value.trim();
    try { window.localStorage.setItem("autopay_base_url", value); } catch (e) {}
    document.querySelectorAll('input[name="base_url"]').forEach(function (input) {
      input.value = value;
    });
    closeSettings();
  }

  function closeMenu() {
    document.getElementById("profile-menu").classList.add("hidden");
  }

  function signOut() {
    closeMenu();
    var overlay = document.getElementById("signed-out");
    overlay.classList.remove("hidden");
    overlay.classList.add("flex");
  }

  function signIn() {
    var overlay = document.getElementById("signed-out");
    overlay.classList.add("hidden");
    overlay.classList.remove("flex");
  }

  window.openModal = openModal;
  window.closeModal = closeModal;
  window.showSection = showSection;
  window.toggleLogs = toggleLogs;
  window.openSettings = openSettings;
  window.closeSettings = closeSettings;
  window.saveSettings = saveSettings;
  window.signOut = signOut;
  window.signIn = signIn;
  window.startNext = startNext;
  window.clearQueue = clearQueue;
  window.refreshActive = refreshActive;
})();
