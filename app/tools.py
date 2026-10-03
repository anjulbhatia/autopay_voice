"""Shared campaign tools. FastAPI and MCP call these; no logic duplicated.

Call binding: voice tools take call_id, never customer_id. The server maps
call -> customer, so the model only ever sees its own customer.
"""
import json
import secrets
from pathlib import Path

from app import ranking
from app.db import (count_calls_today, get_conn, init_db, mask_phone,
                    repo_root, seed_from_json, utcnow, verify_security_answer)

token_alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/O/1/l/I
token_length = 6
default_ttl_minutes = 10
max_verify_attempts = 2


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
    from app import agent as agent_rules
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
            return {"ok": True, "attempts_left": max_verify_attempts - call["verify_attempts"]}
        if call["verify_attempts"] >= max_verify_attempts:
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
        left = max_verify_attempts - (call["verify_attempts"] + 1)
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


def mint_token(conn, length=token_length):
    for _ in range(20):
        token = "".join(secrets.choice(token_alphabet) for _ in range(length))
        exists = conn.execute("select 1 from payment_links where token = ?", (token,)).fetchone()
        if not exists:
            return token
    raise RuntimeError("token space exhausted")


def create_payment_link(customer_id, kind="pay_now", channel="console", base_url=None,
                        ttl_minutes=default_ttl_minutes, call_id=None, conn=None):
    """Mint a single-use expiring link. base_url falls back to $BASE_URL
    (the cloudflared origin); empty means a relative /pay path for local use."""
    import os
    from datetime import datetime, timedelta, timezone
    base_url = base_url if base_url else os.environ.get("BASE_URL", "")
    own = conn is None
    conn = conn or get_conn()
    try:
        now = datetime.now(timezone.utc)
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
        url = f"{base_url.rstrip('/')}/pay/{token}" if base_url else f"/pay/{token}"
        return {"token": token, "url": url, "expires_at": expires}
    finally:
        if own:
            conn.close()


def send_payment_link(call_id, channel="console", base_url=None, conn=None):
    """Voice path: verified calls only, mandate failures get an update-mandate link."""
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
        return {"refused": False, **create_payment_link(
            call["customer_id"], kind, channel, base_url, default_ttl_minutes, call_id, conn)}
    finally:
        if own:
            conn.close()


def get_link_context(token, conn=None):
    """Page + result endpoints share this: link + customer or a dismissal status."""
    from datetime import datetime, timezone
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
                "expires_in": min(remaining, default_ttl_minutes * 60)}
    finally:
        if own:
            conn.close()


def schedule_retry(call_id, when, conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.execute("update calls set outcome = 'retry_scheduled', retry_at = ? where call_id = ?",
                     (when, call_id))
        log_audit(conn, "agent", "schedule_retry", f"when={when}", "ok", call_id)
        conn.commit()
        return {"ok": True, "retry_at": when}
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
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.execute("update calls set outcome = ?, ended_at = ? where call_id = ?",
                     (outcome, utcnow(), call_id))
        call = conn.execute("select customer_id from calls where call_id = ?", (call_id,)).fetchone()
        if outcome == "opted_out":
            conn.execute("update customers set do_not_call = 1 where customer_id = ?",
                         (call["customer_id"],))
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
