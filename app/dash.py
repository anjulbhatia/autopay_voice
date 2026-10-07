"""Console view-models, data only. Markup: web/partials/*.html."""
import json as jsonlib

from app import tools
from app.db import get_conn
from app.utils import mask_phone, parse_reasons

tier_pill = {
    "short": "bg-green-100 text-green-800",
    "standard": "bg-amber-100 text-amber-800",
    "extended": "bg-blue-100 text-blue-800",
}

status_pill = {
    "failed": "bg-red-100 text-red-800",
    "link_sent": "bg-amber-100 text-amber-800",
    "recovered": "bg-green-100 text-green-800",
    "handoff": "bg-blue-100 text-blue-800",
    "opted_out": "bg-neutral-200 text-neutral-500",
}

outcome_pill = {
    "open": "bg-primary text-primary-foreground",
    "link_sent": "bg-amber-100 text-amber-800",
    "retry_scheduled": "bg-blue-100 text-blue-800",
    "handoff": "bg-blue-100 text-blue-800",
    "no_answer": "bg-neutral-200 text-neutral-600",
    "wrong_person": "bg-neutral-200 text-neutral-600",
    "refused": "bg-red-100 text-red-800",
    "opted_out": "bg-neutral-200 text-neutral-500",
    "failed": "bg-red-100 text-red-800",
}

tier_tip = {
    "short": "Likely to pay — short call under 90 seconds, get to the point",
    "standard": "Needs explanation — 2 to 3 minutes, offer two options",
    "extended": "Needs patience — 3 to 5 minutes, offer a human early",
}

display_status = {"failed": "Failed", "link_sent": "Link Sent", "recovered": "Recovered",
                  "handoff": "Handoff", "opted_out": "Opted Out"}
display_outcome = {"open": "Open", "link_sent": "Link Sent", "retry_scheduled": "Retry Scheduled",
                   "handoff": "Handoff", "no_answer": "No Answer", "wrong_person": "Wrong Person",
                   "refused": "Refused", "opted_out": "Opted Out", "failed": "Failed"}


def _vals(**kwargs) -> str:
    """hx-vals payload. Rendered with |safe — ids only, never free text."""
    return jsonlib.dumps(kwargs)


def queue_data(mode="expected_value", q="", tier="all"):
    queue = tools.masked_queue(mode)
    q = (q or "").strip().lower()
    rows = []
    for c in queue["eligible"]:
        if tier not in ("all", "", None) and c["tier"] != tier:
            continue
        if q and q not in c["customer_id"].lower() and q not in (c.get("name") or "").lower():
            continue
        rows.append({
            "customer_id": c["customer_id"],
            "name": c.get("name", ""),
            "p_pay": c["p_pay"],
            "score_pct": max(4, min(100, int(float(c["p_pay"]) * 100))),
            "tier_label": c["tier"].capitalize(),
            "tier_pill": tier_pill.get(c["tier"], "bg-neutral-100 text-neutral-600"),
            "tier_tip": tier_tip.get(c["tier"], ""),
            "priority": f"{c['priority']:,.0f}",
            "phone_masked": c.get("phone_masked", ""),
            "reasons": "; ".join(c.get("reasons", [])),
        })
    return {"rows": rows}


def active_call_data():
    """Live call card model, or None when no call is open."""
    conn = get_conn()
    try:
        row = conn.execute(
            "select calls.*, customers.name, customers.phone, customers.amount_due from calls"
            " join customers on customers.customer_id = calls.customer_id"
            " where calls.outcome is null order by call_id desc limit 1").fetchone()
        if row is None:
            return None
        r = dict(row)
        return {
            "call_id": r["call_id"],
            "customer_id": r["customer_id"],
            "name": r["name"],
            "phone_masked": mask_phone(r["phone"]),
            "amount_due": f"{r['amount_due']:,.2f}",
            "verified": bool(r["verified"]),
            "vals_json": _vals(call_id=r["call_id"]),
            "reasons": "; ".join(parse_reasons(r.get("score_reasons"))),
        }
    finally:
        conn.close()


def customers_data(status="all", q=""):
    conn = get_conn()
    try:
        query = ("select customer_id, name, phone, amount_due, payment_status, attempts_total from customers")
        params: tuple = ()
        if status != "all":
            query += " where payment_status = ?"
            params = (status,)
        needle = (q or "").strip().lower()
        rows = []
        for r in conn.execute(query + " order by customer_id", params).fetchall():
            if needle and needle not in r["customer_id"].lower() and needle not in (r["name"] or "").lower():
                continue
            rows.append({
                "customer_id": r["customer_id"],
                "name": r["name"],
                "phone_masked": mask_phone(r["phone"]),
                "amount_due": f"{r['amount_due']:,.2f}",
                "attempts_total": r["attempts_total"],
                "status_pill": status_pill.get(r["payment_status"], "bg-neutral-100 text-neutral-600"),
                "status_label": display_status.get(r["payment_status"], r["payment_status"]),
            })
        return {"rows": rows}
    finally:
        conn.close()


def customer_detail_data(customer_id):
    conn = get_conn()
    try:
        c = conn.execute("select * from customers where customer_id = ?", (customer_id,)).fetchone()
        if c is None:
            return None
        c = dict(c)
        calls = [{
            "call_id": r["call_id"],
            "tier": r["tier"] or "?",
            "verified": bool(r["verified"]),
            "outcome": r["outcome"] or "open",
            "outcome_pill": outcome_pill.get(r["outcome"] or "open", "bg-neutral-100 text-neutral-600"),
        } for r in conn.execute(
            "select call_id, tier, verified, outcome, started_at from calls where customer_id = ?"
            " order by call_id desc limit 10", (customer_id,)).fetchall()]
        links = [{
            "token": r["token"], "kind": r["kind"], "channel": r["channel"],
            "used": bool(r["used_at"]),
        } for r in conn.execute(
            "select token, kind, channel, created_at, expires_at, used_at from payment_links"
            " where customer_id = ? order by created_at desc limit 5", (customer_id,)).fetchall()]
        handoffs = [{
            "handoff_id": r["handoff_id"], "reason": r["reason"], "status": r["status"],
        } for r in conn.execute(
            "select handoff_id, reason, status, created_at from handoffs where customer_id = ?"
            " order by handoff_id desc limit 5", (customer_id,)).fetchall()]
        return {
            "customer": {
                "customer_id": c["customer_id"],
                "name": c["name"],
                "phone_masked": mask_phone(c["phone"]),
                "amount_due": f"{c['amount_due']:,.2f}",
                "due_date": c["due_date"] or "",
                "payment_status": c["payment_status"],
                "status_pill": status_pill.get(c["payment_status"], "bg-neutral-100 text-neutral-600"),
                "status_label": display_status.get(c["payment_status"], c["payment_status"]),
                "failure_reason": c["failure_reason"] or "",
                "attempts_total": c["attempts_total"],
                "default_history": c["default_history"],
                "security_question": c["security_question"] or "—",
                "past_call_notes": c["past_call_notes"] or "—",
            },
            "calls": calls,
            "links": links,
            "handoffs": handoffs,
            "start_vals_json": _vals(customer_id=c["customer_id"]),
        }
    finally:
        conn.close()


def calls_data(q="", outcome="all"):
    conn = get_conn()
    try:
        needle = (q or "").strip().lower()
        rows = []
        for r in conn.execute(
                "select call_id, customer_id, tier, verified, outcome from calls"
                " order by call_id desc limit 100").fetchall():
            if outcome not in ("all", "", None) and (r["outcome"] or "open") != outcome:
                continue
            if needle and needle not in str(r["call_id"]) and needle not in (r["customer_id"] or "").lower():
                continue
            tier = r["tier"] or ""
            oc = r["outcome"] or "open"
            rows.append({
                "call_id": r["call_id"],
                "customer_id": r["customer_id"],
                "tier_pill": tier_pill.get(tier, "bg-neutral-100 text-neutral-600"),
                "tier_tip": tier_tip.get(tier, ""),
                "tier_label": (tier or "?").capitalize(),
                "verified": bool(r["verified"]),
                "outcome_pill": outcome_pill.get(oc, "bg-neutral-100 text-neutral-600"),
                "outcome_label": display_outcome.get(oc, oc),
                "vals_json": _vals(call_id=r["call_id"]),
            })
        return {"rows": rows}
    finally:
        conn.close()


def call_detail_data(call_id):
    conn = get_conn()
    try:
        call = conn.execute("select calls.*, customers.name from calls"
                            " join customers on customers.customer_id = calls.customer_id"
                            " where call_id = ?", (call_id,)).fetchone()
        if call is None:
            return None
        call = dict(call)
        reasons = ""
        if call["score_reasons"]:
            try:
                reasons = "; ".join(jsonlib.loads(call["score_reasons"]))
            except ValueError:
                reasons = ""
        transcript = call.get("transcript") or ""
        judge_html = ""
        if call.get("judge_json"):
            try:
                verdict = jsonlib.loads(call["judge_json"])
            except ValueError:
                verdict = None
            if verdict:
                flags = verdict.get("flags") or verdict.get("violations") or []
                if flags:
                    import html as _html
                    judge_html += ("<p class='mt-1 text-xs font-semibold text-red-600'>flags: "
                                   + _html.escape(str(flags)) + "</p>")
                import html as _html2
                judge_html += "judge: " + _html2.escape(jsonlib.dumps(verdict)[:300])
        hand = conn.execute("select handoff_id, reason, status, notes from handoffs"
                            " where call_id = ? order by handoff_id desc limit 1", (call_id,)).fetchone()
        audit = [{
            "time": (a["ts"][11:19] if a["ts"] else ""),
            "actor": a["actor"], "tool": a["tool"],
            "args": a["args_masked"] or "", "status": a["status"],
        } for a in conn.execute("select ts, actor, tool, args_masked, status from audit_log"
                                " where call_id = ? order by id desc limit 20", (call_id,)).fetchall()]
        oc = call["outcome"] or "open"
        return {
            "call": {
                "call_id": call["call_id"],
                "name": call.get("name") or "",
                "customer_id": call["customer_id"],
                "word_count": len(transcript.split()) if transcript else 0,
                "tier_label": (call["tier"] or "?").capitalize(),
                "verified": bool(call["verified"]),
                "outcome_label": display_outcome.get(oc, oc if oc != "open" else "Open"),
                "reasons": reasons,
                "transcript": transcript,
                "judge_html": judge_html,
            },
            "handoff": dict(hand) if hand is not None else None,
            "audit": audit,
        }
    finally:
        conn.close()


def handoffs_data(only_open=True, q=""):
    conn = get_conn()
    try:
        query = "select handoff_id, customer_id, call_id, reason, status, created_at from handoffs"
        if only_open:
            query += " where status = 'open'"
        needle = (q or "").strip().lower()
        rows = []
        for r in conn.execute(query + " order by handoff_id desc").fetchall():
            if needle and needle not in (r["customer_id"] or "").lower() \
                    and needle not in (r["reason"] or "").lower():
                continue
            rows.append({
                "handoff_id": r["handoff_id"],
                "customer_id": r["customer_id"],
                "reason": r["reason"],
                "status": r["status"],
                "status_label": display_status.get(r["status"], r["status"].capitalize()),
                "vals_json": _vals(handoff_id=r["handoff_id"]),
            })
        return {"rows": rows}
    finally:
        conn.close()


def handoff_detail_data(handoff_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "select handoffs.*, customers.name from handoffs"
            " join customers on customers.customer_id = handoffs.customer_id"
            " where handoff_id = ?", (handoff_id,)).fetchone()
        if row is None:
            return None
        row = dict(row)
        audit = [{
            "time": (a["ts"][11:19] if a["ts"] else ""),
            "actor": a["actor"], "tool": a["tool"], "status": a["status"],
        } for a in conn.execute("select ts, actor, tool, args_masked, status from audit_log"
                                " where call_id = ? order by id desc limit 10", (row["call_id"],)).fetchall()]
        row["notes"] = row["notes"] or "—"
        return {"handoff": row, "audit": audit,
                "resolve_vals_json": _vals(handoff_id=row["handoff_id"])}
    finally:
        conn.close()


def audit_data(limit=50, q="", call_id=None):
    conn = get_conn()
    try:
        needle = (q or "").strip().lower()
        if call_id is not None:
            rows = conn.execute("select ts, actor, tool, args_masked, status, call_id from audit_log"
                                " where call_id = ? order by id desc limit ?", (call_id, limit)).fetchall()
        else:
            rows = conn.execute("select ts, actor, tool, args_masked, status, call_id from audit_log"
                                " order by id desc limit ?", (limit,)).fetchall()
            if needle:
                rows = [r for r in rows if needle in (r["tool"] or "").lower()
                        or needle in (r["actor"] or "").lower()
                        or needle in (r["args_masked"] or "").lower()]
        return {"rows": [{
            "time": (r["ts"][11:19] if r["ts"] else ""),
            "actor": r["actor"], "tool": r["tool"],
            "args": r["args_masked"] or "", "status": r["status"],
            "call_id": r["call_id"],
        } for r in rows]}
    finally:
        conn.close()


def events_data():
    conn = get_conn()
    try:
        calls = [f"#{r['call_id']} {r['customer_id']} → {r['outcome'] or 'open'}"
                 for r in conn.execute("select call_id, customer_id, outcome from calls"
                                       " order by call_id desc limit 8").fetchall()]
        notes = [f"{r['ts'][11:19]} {r['actor']}/{r['tool']} {r['status']}" if r["ts"] else ""
                 for r in conn.execute("select ts, actor, tool, status from audit_log"
                                       " order by id desc limit 8").fetchall()]
        return {"items": calls + notes}
    finally:
        conn.close()
