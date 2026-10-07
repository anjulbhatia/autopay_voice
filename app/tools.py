import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from app import agent as agent_rules, channels, ranking
from app.config import (DEFAULT_TTL_MINUTES, MAX_LINK_TTL, MAX_VERIFY_ATTEMPTS, MIN_LINK_TTL,
                         TOKEN_ALPHABET, TOKEN_LENGTH, VALID_CALL_OUTCOMES, VALID_LINK_CHANNELS,
                         VALID_LINK_KINDS, pay_base)
from app.db import (count_calls_today, get_conn, init_db, repo_root,
                    seed_from_json, utcnow, verify_security_answer)
from app.utils import mask_phone


def log_audit(conn, actor, tool, args_masked="", status="ok", call_id=None):
    conn.execute(
        "insert into audit_log (ts, call_id, actor, tool, args_masked, status) values (?,?,?,?,?,?)",
        (utcnow(), call_id, actor, tool, args_masked, status),
    )
    conn.commit()


def ingest_customers_data(json_path=None, conn=None):
    """Startup ingest: create schema, idempotent seed. Returns inserted/total."""
    own = conn is None
    conn = conn or get_conn()
    try:
        init_db(conn)
        inserted = seed_from_json(json_path or repo_root / "data" / "customers.json", conn)
        total = conn.execute("select count(*) from customers").fetchone()[0]
        log_audit(conn, "system", "ingest_customers_data", f"inserted={inserted}", "ok")
        return {"inserted": inserted, "total": total}
    finally:
        if own:
            conn.close()


def rank_queue(mode="expected_value", conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        return ranking.rank_customers(conn, mode)
    finally:
        if own:
            conn.close()


def start_call(customer_id, mode="web", vapi_call_id=None, enforce_hours=True, source="", conn=None):
    """Open a call row with a ranking snapshot; counts as an attempt.
    Hours gate on by default; sim/tests pass enforce_hours=False.
    For now calls start from the merchant dashboard only."""
    if source != "dashboard":
        raise ValueError("calls start from the merchant dashboard only (for now)")
    if enforce_hours and not agent_rules.calling_allowed():
        raise ValueError("outside allowed calling hours (09:00–21:00 IST)")
    own = conn is None
    conn = conn or get_conn()
    try:
        row = conn.execute("select * from customers where customer_id = ?", (customer_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown customer {customer_id}")
        score = ranking.score_customer(row, count_calls_today(conn, customer_id))
        if not score["eligible"]:
            raise ValueError(f"not eligible: {score['reason']}")
        cur = conn.execute(
            "insert into calls (customer_id, vapi_call_id, mode, started_at, tier, p_pay, score_reasons)"
            " values (?,?,?,?,?,?,?)",
            (customer_id, vapi_call_id, mode, utcnow(), score["tier"],
             score["p_pay"], json.dumps(score["reasons"])),
        )
        conn.execute(
            "update customers set attempts_total = attempts_total + 1, last_call_at = ? where customer_id = ?",
            (utcnow(), customer_id),
        )
        log_audit(conn, "system", "start_call", f"tier={score['tier']}", "ok", cur.lastrowid)
        conn.commit()
        return {"call_id": cur.lastrowid, "customer_id": customer_id,
                "tier": score["tier"], "p_pay": score["p_pay"], "reasons": score["reasons"]}
    finally:
        if own:
            conn.close()


def call_customer(call_id, conn):
    row = conn.execute(
        "select calls.*, customers.name, customers.phone from calls"
        " join customers on customers.customer_id = calls.customer_id"
        " where call_id = ?", (call_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown call {call_id}")
    return row


def verify_identity(call_id, answer, conn=None):
    """Check security answer (2 tries). Never logs the answer itself."""
    own = conn is None
    conn = conn or get_conn()
    try:
        call = call_customer(call_id, conn)
        if call["verified"]:
            return {"ok": True, "attempts_left": MAX_VERIFY_ATTEMPTS - call["verify_attempts"]}
        if call["verify_attempts"] >= MAX_VERIFY_ATTEMPTS:
            log_audit(conn, "agent", "verify_identity", "attempts exhausted", "refused", call_id)
            conn.commit()
            return {"ok": False, "attempts_left": 0}
        customer = conn.execute(
            "select security_answer_hash, security_salt from customers where customer_id = ?",
            (call["customer_id"],)).fetchone()
        good = verify_security_answer(customer["security_answer_hash"], customer["security_salt"], answer or "")
        conn.execute(
            "update calls set verify_attempts = verify_attempts + 1, verified = ? where call_id = ?",
            (1 if good else 0, call_id),
        )
        left = MAX_VERIFY_ATTEMPTS - (call["verify_attempts"] + 1)
        log_audit(conn, "agent", "verify_identity", f"ok={good}", "ok" if good else "refused", call_id)
        conn.commit()
        return {"ok": good, "attempts_left": max(left, 0)}
    finally:
        if own:
            conn.close()


def verified_or_refused(call_id, conn, tool):
    verified = conn.execute("select verified from calls where call_id = ?", (call_id,)).fetchone()
    if verified is None or not verified["verified"]:
        log_audit(conn, "agent", tool, "unverified caller", "refused", call_id)
        conn.commit()
        return {"refused": True, "message": "Identity not verified. No details can be shared."}
    return None


def get_failed_payment(call_id, conn=None):
    """Gated: amount and notes only after verification (notes never pre-verify)."""
    own = conn is None
    conn = conn or get_conn()
    try:
        gate = verified_or_refused(call_id, conn, "get_failed_payment")
        if gate:
            return gate
        call = call_customer(call_id, conn)
        customer = conn.execute(
            "select amount_due, due_date, failure_reason, past_call_notes from customers where customer_id = ?",
            (call["customer_id"],)).fetchone()
        log_audit(conn, "agent", "get_failed_payment", "disclosed post-verify", "ok", call_id)
        conn.commit()
        return {"refused": False, "amount_due": customer["amount_due"], "due_date": customer["due_date"],
                "failure_reason": customer["failure_reason"], "past_call_notes": customer["past_call_notes"]}
    finally:
        if own:
            conn.close()


def mint_token(conn, length=TOKEN_LENGTH):
    for _ in range(20):
        token = "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))
        exists = conn.execute("select 1 from payment_links where token = ?", (token,)).fetchone()
        if not exists:
            return token
    raise RuntimeError("token space exhausted")


def create_payment_link(customer_id, kind="pay_now", channel="console",
                        ttl_minutes=DEFAULT_TTL_MINUTES, call_id=None, conn=None,
                        reuse_live=True):
    """Mint a single-use expiring link on the pay server (:8800)."""
    if kind not in VALID_LINK_KINDS:
        raise ValueError(f"unknown link kind {kind!r}")
    if channel not in VALID_LINK_CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")
    try:
        ttl_minutes = int(ttl_minutes)
    except (TypeError, ValueError):
        raise ValueError(f"bad ttl {ttl_minutes!r}")
    if not MIN_LINK_TTL <= ttl_minutes <= MAX_LINK_TTL:
        raise ValueError(f"bad ttl {ttl_minutes!r} (1-1440 required)")
    resolved_base = pay_base()
    own = conn is None
    conn = conn or get_conn()
    try:
        now = datetime.now(timezone.utc)
        if reuse_live:
            live = conn.execute(
                "select token, expires_at from payment_links where customer_id = ? and kind = ?"
                " and used_at is null and expires_at > ? order by created_at desc limit 1",
                (customer_id, kind, now.isoformat()),
            ).fetchone()
            if live is not None:
                log_audit(conn, "agent" if call_id else "merchant", "create_payment_link",
                          json.dumps({"token": live["token"][:2] + "…", "kind": kind,
                                      "channel": channel, "reused": True}), "ok", call_id)
                conn.commit()
                url = f"{resolved_base.rstrip('/')}/pay/{live['token']}" if resolved_base \
                    else f"/pay/{live['token']}"
                return {"token": live["token"], "url": url,
                        "expires_at": live["expires_at"], "reused": True}
        token = mint_token(conn)
        expires = (now + timedelta(minutes=ttl_minutes)).isoformat()
        conn.execute(
            "insert into payment_links (token, customer_id, call_id, kind, channel, created_at, expires_at)"
            " values (?,?,?,?,?,?,?)",
            (token, customer_id, call_id, kind, channel, now.isoformat(), expires),
        )
        conn.execute("update customers set payment_status = 'link_sent' where customer_id = ?", (customer_id,))
        log_audit(conn, "agent" if call_id else "merchant", "create_payment_link",
                  json.dumps({"token": token[:2] + "…", "kind": kind, "channel": channel}), "ok", call_id)
        conn.commit()
        url = f"{resolved_base.rstrip('/')}/pay/{token}" if resolved_base else f"/pay/{token}"
        return {"token": token, "url": url, "expires_at": expires}
    finally:
        if own:
            conn.close()


def send_payment_link(call_id, channel="console", conn=None):
    """Voice path: verified calls only, mandate failures get an update-mandate link.
    Renders + mock-sends through the channel adapter so the customer gets
    something, and stamps last_message_at for ranking recency."""
    if channel not in VALID_LINK_CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")
    own = conn is None
    conn = conn or get_conn()
    try:
        gate = verified_or_refused(call_id, conn, "send_payment_link")
        if gate:
            return gate
        call = call_customer(call_id, conn)
        kind_row = conn.execute("select failure_reason from customers where customer_id = ?",
                                (call["customer_id"],)).fetchone()
        kind = "update_mandate" if kind_row["failure_reason"] == "mandate_expired" else "pay_now"
        link = create_payment_link(
            call["customer_id"], kind, channel, DEFAULT_TTL_MINUTES, call_id, conn)
        person = conn.execute("select name, phone, amount_due from customers where customer_id = ?",
                              (call["customer_id"],)).fetchone()
        text = channels.render(channel, person["name"], person["amount_due"], link["url"])
        receipt = channels.send(channel, mask_phone(person["phone"]), text)
        conn.execute("update customers set last_message_at = ? where customer_id = ?",
                     (utcnow(), call["customer_id"]))
        log_audit(conn, "agent", "send_payment_link",
                  f"channel={receipt['channel']} status={receipt['status']}", "ok", call_id)
        conn.commit()
        return {"refused": False, **link, "send": receipt["status"]}
    finally:
        if own:
            conn.close()


def get_link_context(token, conn=None):
    """Page + result endpoints share this: link + customer or a dismissal status."""
    own = conn is None
    conn = conn or get_conn()
    try:
        link = conn.execute("select * from payment_links where token = ?", (token,)).fetchone()
        if link is None:
            return {"status": "unknown"}
        now = datetime.now(timezone.utc).isoformat()
        if link["used_at"] is not None:
            status = "used"
        elif link["expires_at"] <= now:
            status = "expired"
        else:
            status = "ok"
        customer = conn.execute("select * from customers where customer_id = ?",
                                (link["customer_id"],)).fetchone()
        remaining = 0
        if status == "ok":
            remaining = max(0, int((datetime.fromisoformat(link["expires_at"])
                                    - datetime.now(timezone.utc)).total_seconds()))
        return {"status": status, "link": dict(link), "customer": dict(customer),
                "expires_in": remaining}
    finally:
        if own:
            conn.close()


def schedule_retry(call_id, when, conn=None):
    try:
        moment = datetime.fromisoformat(str(when))
    except (TypeError, ValueError):
        raise ValueError(f"bad retry date {when!r} (ISO-8601 required)")
    if moment.tzinfo is None:
        raise ValueError(f"bad retry date {when!r} (timezone-aware ISO-8601 required)")
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.execute("update calls set outcome = 'retry_scheduled', retry_at = ? where call_id = ?",
                     (moment.isoformat(), call_id))
        log_audit(conn, "agent", "schedule_retry", f"when={moment.isoformat()}", "ok", call_id)
        conn.commit()
        return {"ok": True, "retry_at": moment.isoformat()}
    finally:
        if own:
            conn.close()


def request_human_handoff(call_id, reason, notes="", conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        call = call_customer(call_id, conn)
        cur = conn.execute(
            "insert into handoffs (customer_id, call_id, reason, created_at, notes) values (?,?,?,?,?)",
            (call["customer_id"], call_id, reason, utcnow(), notes),
        )
        conn.execute("update calls set outcome = 'handoff' where call_id = ?", (call_id,))
        conn.execute("update customers set payment_status = 'handoff' where customer_id = ?",
                     (call["customer_id"],))
        log_audit(conn, "agent", "request_human_handoff", f"reason={reason}", "ok", call_id)
        conn.commit()
        return {"ok": True, "handoff_id": cur.lastrowid}
    finally:
        if own:
            conn.close()


def log_outcome(call_id, outcome, notes="", conn=None):
    if outcome not in VALID_CALL_OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.execute("update calls set outcome = ?, ended_at = ? where call_id = ?",
                     (outcome, utcnow(), call_id))
        call = conn.execute("select customer_id from calls where call_id = ?", (call_id,)).fetchone()
        if call is None:
            raise ValueError(f"unknown call {call_id}")
        if outcome == "opted_out":
            conn.execute("update customers set do_not_call = 1, payment_status = 'opted_out'"
                         " where customer_id = ? and payment_status != 'recovered'",
                         (call["customer_id"],))
        elif outcome in ("link_sent", "handoff"):
            conn.execute("update customers set payment_status = ? where customer_id = ?"
                         " and payment_status != 'recovered'", (outcome, call["customer_id"]))
        log_audit(conn, "agent", "log_outcome", f"outcome={outcome} {notes}"[:200], "ok", call_id)
        conn.commit()
        return {"ok": True}
    finally:
        if own:
            conn.close()


def masked_queue(mode="expected_value", conn=None):
    """Merchant view: ranked queue with phones masked."""
    own = conn is None
    conn = conn or get_conn()
    try:
        queue = ranking.rank_customers(conn, mode)
        for item in queue["eligible"]:
            row = conn.execute("select phone from customers where customer_id = ?",
                               (item["customer_id"],)).fetchone()
            item["phone_masked"] = mask_phone(row["phone"])
        return queue
    finally:
        if own:
            conn.close()
