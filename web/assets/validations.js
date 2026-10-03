/* validations.js — pure form validators for web/pay.html. No DOM, no network. */
(function () {
  "use strict";

  function digits(value) {
    return (value || "").replace(/\D/g, "");
  }

  function validHolder(name) {
    return (name || "").trim().length >= 3 || "Enter the full account holder name.";
  }

  function validCard(value) {
    // demo: any numeric string that fits, no luhn gate
    var number = digits(value);
    if (number.length < 13 || number.length > 19) return "Card number must be 13–19 digits.";
    return true;
  }

  function validExpiry(value) {
    var parts = (value || "").trim().split("/");
    if (parts.length !== 2) return "Use MM/YY, e.g. 08/27.";
    var mm = parseInt(parts[0], 10);
    var yy = parseInt(parts[1], 10);
    if (isNaN(mm) || mm < 1 || mm > 12) return "Month must be 01–12.";
    var thisYear = new Date().getFullYear();
    if (isNaN(yy) || 2000 + yy < thisYear) return "Year must be " + thisYear + " onwards.";
    var end = new Date(2000 + yy, mm, 1);
    if (end <= new Date()) return "This card has expired.";
    return true;
  }

  function validCvv(value) {
    return /^\d{3}$/.test((value || "").trim()) || "CVV must be 3 digits.";
  }

  function validUpi(value) {
    return /^[\w.\-]{2,64}@[a-zA-Z]{2,64}$/.test((value || "").trim()) || "Enter a valid UPI ID (name@bank).";
  }

  if (typeof window !== "undefined") {
    window.autopayValidators = {
      digits: digits,
      validHolder: validHolder,
      validCard: validCard,
      validExpiry: validExpiry,
      validCvv: validCvv,
      validUpi: validUpi,
    };
  }
})();
