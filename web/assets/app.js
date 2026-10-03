/* app.js — page interactions for web/pay.html: steps, anime.js effects,
   ticker, backend notify. Needs validations.js loaded first. */
(function () {
  "use strict";

  var validators = (typeof window !== "undefined" && window.autopayValidators) || {};
  var digits = validators.digits || function (v) { return (v || "").replace(/\D/g, ""); };

  var theme_default = "#e4c5ff";
  var theme_success = "#16a34a";
  var theme_failed = "#dc2626";
  var current_step = 1;

  function hasAnime() {
    return typeof window !== "undefined" && typeof window.anime !== "undefined";
  }

  function setTheme(hex) {
    if (typeof document === "undefined") return;
    var tag = document.querySelector('meta[name="theme-color"]');
    if (tag) tag.setAttribute("content", hex);
  }

  function showError(input, message) {
    var slot = document.getElementById("err-" + input.id);
    if (!slot) return;
    if (message === true) {
      slot.classList.add("hidden");
      slot.textContent = "";
      input.removeAttribute("aria-invalid");
    } else {
      slot.classList.remove("hidden");
      slot.textContent = message;
      input.setAttribute("aria-invalid", "true");
    }
  }

  function upiActive() {
    var box = document.getElementById("fields-upi");
    return !!box && !box.classList.contains("hidden");
  }

  function validateForm(form) {
    var checks;
    if (upiActive()) {
      checks = [[form.querySelector("#upi-id"), validators.validUpi]];
    } else {
      checks = [
        [form.querySelector("#acc-holder"), validators.validHolder],
        [form.querySelector("#card-number"), validators.validCard],
        [form.querySelector("#card-expiry"), validators.validExpiry],
        [form.querySelector("#card-cvv"), validators.validCvv],
      ];
    }
    var ok = true;
    checks.forEach(function (pair) {
      var result = pair[1](pair[0].value);
      showError(pair[0], result);
      if (result !== true) ok = false;
    });
    return ok;
  }

  var pill_on = ["bg-primary-foreground", "text-white"];
  var pill_off = ["bg-neutral-100", "text-neutral-600"];

  function paintMethod(which) {
    var card = document.getElementById("method-card");
    var upi = document.getElementById("method-upi");
    var fieldsCard = document.getElementById("fields-card");
    var fieldsUpi = document.getElementById("fields-upi");
    var useUpi = which === "upi";
    [[card, !useUpi], [upi, useUpi]].forEach(function (pair) {
      pair[0].setAttribute("aria-pressed", String(pair[1]));
      pill_on.forEach(function (c) { pair[0].classList.toggle(c, pair[1]); });
      pill_off.forEach(function (c) { pair[0].classList.toggle(c, !pair[1]); });
    });
    fieldsCard.classList.toggle("hidden", useUpi);
    fieldsUpi.classList.toggle("hidden", !useUpi);
    fieldsUpi.classList.toggle("flex", useUpi);
  }

  function stepEl(n) {
    return document.querySelector('section[data-step="' + n + '"]');
  }

  function paintDots(n) {
    for (var i = 1; i <= 3; i++) {
      var dot = document.getElementById("step-dot-" + i);
      if (!dot) continue;
      var on = i <= n;
      dot.classList.toggle("bg-primary-foreground", on);
      dot.classList.toggle("bg-gray-300", !on);
    }
  }

  function enterFx(el) {
    if (!hasAnime()) return;
    window.anime.animate(el, {
      opacity: [0, 1],
      translateY: [18, 0],
      duration: 380,
      easing: "easeOutCubic",
    });
  }

  function clearFx(el) {
    if (!el) return;
    el.style.opacity = "";
    el.style.transform = "";
  }

  function goStep(n) {
    var prev = stepEl(current_step);
    var next = stepEl(n);
    if (!next || n === current_step) return;
    var forward = n > current_step;
    current_step = n;
    paintDots(n);
    if (n < 3) setTheme(theme_default);
    if (hasAnime()) window.anime.remove([prev, next]); // kill stacked tweens
    clearFx(prev);
    clearFx(next);
    if (!hasAnime() || !prev) {
      if (prev) prev.classList.add("hidden");
      next.classList.remove("hidden");
      return;
    }
    window.anime.animate(prev, {
      opacity: [1, 0],
      translateX: [0, forward ? -28 : 28],
      duration: 200,
      easing: "easeInQuad",
      onComplete: function () {
        prev.classList.add("hidden");
        clearFx(prev);
        next.classList.remove("hidden");
        enterFx(next);
      },
    });
  }

  function showStatus(which) {
    ["processing", "success", "failed"].forEach(function (name) {
      document.getElementById("status-" + name).classList.toggle("hidden", name !== which);
    });
    var panel = document.getElementById("payment-status");
    if (which === "success") successFx();
    if (which === "failed") failFx();
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function successFx() {
    setTheme(theme_success);
    if (!hasAnime()) return;
    window.anime.animate("#status-success h2", {
      scale: [0.85, 1],
      opacity: [0, 1],
      duration: 450,
      easing: "easeOutBack",
    });
  }

  function failFx() {
    setTheme(theme_failed);
    if (!hasAnime()) return;
    window.anime.animate("#payment-status", {
      translateX: [0, -10, 10, -6, 6, 0],
      duration: 420,
      easing: "easeInOutQuad",
    });
  }

  function notifyBackend(outcome, ref) {
    // best-effort: page already shows the result; backend reacts next (link burn, status, audit)
    var main = document.querySelector("main[data-token]");
    var token = main ? (main.getAttribute("data-token") || "").trim() : "";
    if (!token) return;
    try {
      fetch("/pay/result", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token, outcome: outcome, method: upiActive() ? "upi" : "card", ref: ref || null }),
      }).catch(function () {});
    } catch (e) {}
  }

  function fakeRef() {
    var chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    var out = "DEMO-";
    for (var i = 0; i < 6; i++) out += chars[Math.floor(Math.random() * chars.length)];
    return out;
  }

  function startTicker() {
    var ticker = document.getElementById("expiry-ticker");
    var notes = [document.getElementById("expiry-note"), document.getElementById("expiry-note-2")];
    var payBtn = document.getElementById("pay-btn");
    var continueBtn = document.getElementById("continue-btn");
    if (!ticker) return;
    var left = parseInt(ticker.getAttribute("data-expires-seconds"), 10) || 600;
    function paint() {
      var mm = String(Math.floor(left / 60)).padStart(2, "0");
      var ss = String(left % 60).padStart(2, "0");
      ticker.textContent = mm + ":" + ss;
      var urgent = left <= 120;
      ticker.classList.toggle("text-red-600", urgent);
      ticker.classList.toggle("text-primary-foreground", !urgent);
    }
    paint();
    var timer = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        clearInterval(timer);
        ticker.textContent = "00:00";
        notes.forEach(function (note) { if (note) note.classList.remove("hidden"); });
        if (payBtn) payBtn.disabled = true;
        if (continueBtn) continueBtn.disabled = true;
        return;
      }
      paint();
    }, 1000);
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      if (!validators.validCard) return; // validations.js must load first
      var form = document.getElementById("pay-form");
      if (!form) return;
      startTicker();
      enterFx(stepEl(1)); // load morph entry

      var card = form.querySelector("#card-number");
      card.addEventListener("input", function () {
        var number = digits(card.value).slice(0, 19);
        card.value = (number.match(/.{1,4}/g) || []).join(" ");
      });

      var expiry = form.querySelector("#card-expiry");
      expiry.addEventListener("input", function () {
        var number = digits(expiry.value).slice(0, 4);
        expiry.value = number.length > 2 ? number.slice(0, 2) + "/" + number.slice(2) : number;
      });

      document.getElementById("continue-btn").addEventListener("click", function () {
        goStep(2);
      });
      document.getElementById("back-btn").addEventListener("click", function () {
        goStep(1);
      });
      document.getElementById("method-card").addEventListener("click", function () {
        paintMethod("card");
      });
      document.getElementById("method-upi").addEventListener("click", function () {
        paintMethod("upi");
      });

      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!validateForm(form)) return;
        goStep(3);
        showStatus("processing");
        setTimeout(function () {
          var ref = fakeRef();
          document.getElementById("pay-ref").textContent = ref;
          showStatus("success");
          notifyBackend("paid", ref);
        }, 1200);
      });

      document.getElementById("fail-btn").addEventListener("click", function () {
        goStep(3);
        showStatus("failed");
        notifyBackend("failed", null);
      });
      document.getElementById("retry-btn").addEventListener("click", function () {
        goStep(2);
      });
    });
  }
})();
