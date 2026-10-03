"""Transparent heuristic ranking per AGENT.md section 5.

Lower propensity gets more patience, never more pressure (tier only
controls call length and empathy). Every score carries human reasons.
"""
import math
from datetime import datetime, timedelta, timezone

daily_cap = 2
total_cap = 5
cooldown_hours = 24

failure_boost = {
    "insufficient_balance": 0.9,   # money problem, often retries fine
    "mandate_expired": -0.2,       # needs mandate renewal first
    "bank_decline": -0.5,          # bank said no, harder
    "other": -0.3,
}


def parse_utc(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def score_customer(row, calls_today, now=None):
    """Score one customer row. Returns p_pay, tier, priority, eligibility, reasons."""
    now = now or datetime.now(timezone.utc)
    reasons = []
    amount = float(row["amount_due"] or 0)
    failure = row["failure_reason"] or "other"
    defaults = int(row["default_history"] or 0)
    attempts = int(row["attempts_total"] or 0)

    if int(row["do_not_call"] or 0):
        return {"customer_id": row["customer_id"], "eligible": False,
                "reason": "do-not-call flag set", "reasons": ["suppressed: do-not-call"]}
    if attempts >= total_cap:
        return {"customer_id": row["customer_id"], "eligible": False,
                "reason": f"total cap reached ({attempts}/{total_cap})",
                "reasons": [f"suppressed: {attempts} total attempts (cap {total_cap})"]}
    if calls_today >= daily_cap:
        return {"customer_id": row["customer_id"], "eligible": False,
                "reason": f"daily cap reached ({calls_today}/{daily_cap})",
                "reasons": [f"suppressed: {calls_today} calls today (cap {daily_cap})"]}
    last_call = parse_utc(row["last_call_at"])
    if last_call and now - last_call < timedelta(hours=cooldown_hours):
        return {"customer_id": row["customer_id"], "eligible": False,
                "reason": "called within last 24h",
                "reasons": ["suppressed: last call <24h ago"]}

    logit = 0.2
    boost = failure_boost.get(failure, failure_boost["other"])
    logit += boost
    reasons.append(f"failure reason {failure} ({boost:+.1f})")
    if defaults:
        logit -= 0.4 * defaults
        reasons.append(f"{defaults} prior defaults (-{0.4 * defaults:.1f})")
    if attempts:
        logit -= 0.15 * attempts
        reasons.append(f"{attempts} prior attempts (-{0.15 * attempts:.1f})")
    last_msg = parse_utc(row["last_message_at"])
    if last_msg and now - last_msg < timedelta(hours=cooldown_hours):
        logit -= 0.6
        reasons.append("very recent message, likely already engaged (-0.6)")

    prob = 1.0 / (1.0 + math.exp(-logit))
    tier = "short" if prob >= 0.7 else "standard" if prob >= 0.4 else "extended"
    reasons.append(f"tier {tier}: {'likely' if tier == 'short' else 'needs explanation' if tier == 'standard' else 'needs patience, early handoff'} (p={prob:.2f})")
    return {"customer_id": row["customer_id"], "eligible": True, "p_pay": round(prob, 4),
            "tier": tier, "priority": round(prob * amount, 2),
            "amount_due": amount, "reasons": reasons}


def rank_customers(conn, mode="expected_value"):
    """Score every customer; eligible ones sorted per mode. Modes: expected_value, easiest, highest."""
    from app.db import count_calls_today
    now = datetime.now(timezone.utc)
    scored = []
    for row in conn.execute("select * from customers order by customer_id"):
        item = score_customer(row, count_calls_today(conn, row["customer_id"]), now)
        item["name"] = row["name"]
        scored.append(item)
    eligible = [s for s in scored if s["eligible"]]
    key = {"expected_value": lambda s: s["priority"],
           "easiest": lambda s: s["p_pay"],
           "highest": lambda s: s["amount_due"]}.get(mode, lambda s: s["priority"])
    eligible.sort(key=key, reverse=True)
    return {"mode": mode, "eligible": eligible,
            "ineligible": [s for s in scored if not s["eligible"]]}
