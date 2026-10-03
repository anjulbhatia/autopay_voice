"""Ranking, verification gate, links, handoffs (all on throwaway DBs)."""
import hashlib

from app import tools
from app.db import get_conn, init_db


def seed_db(tmp_path, name="t.db"):
    conn = get_conn(tmp_path / name)
    init_db(conn)
    tools.ingest_customers_data("data/customers.json", conn)
    return conn


def test_rank_shapes(tmp_path):
    conn = seed_db(tmp_path)
    queue = tools.rank_queue(conn=conn)
    assert len(queue["eligible"]) == 9  # CUST009 is do-not-call
    assert {c["customer_id"] for c in queue["ineligible"]} == {"CUST009"}
    for item in queue["eligible"]:
        assert item["tier"] in ("short", "standard", "extended")
        assert item["reasons"] and 0 < item["p_pay"] < 1
    amounts = [c["amount_due"] for c in tools.rank_queue("highest", conn)["eligible"]]
    assert amounts == sorted(amounts, reverse=True)
    probs = [c["p_pay"] for c in tools.rank_queue("easiest", conn)["eligible"]]
    assert probs == sorted(probs, reverse=True)
    conn.close()


def test_verify_gate_and_links(tmp_path):
    conn = seed_db(tmp_path)
    call = tools.start_call("CUST001", mode="sim", enforce_hours=False, source="dashboard", conn=conn)
    assert call["tier"] in ("short", "standard", "extended")
    assert tools.get_failed_payment(call["call_id"], conn)["refused"] is True
    assert tools.verify_identity(call["call_id"], "wrong", conn)["ok"] is False
    assert tools.send_payment_link(call["call_id"], conn=conn)["refused"] is True

    salt, answer = "salty12", "1990"
    conn.execute(
        "insert into customers (customer_id, name, phone, amount_due, failure_reason,"
        " security_question, security_answer_hash, security_salt)"
        " values (?,?,?,?,?,?,?,?)",
        ("CUST099", "Probe User", "+91-90000-00099", 1500.0, "insufficient_balance",
         "Year?", hashlib.sha256((salt + answer).encode()).hexdigest(), salt),
    )
    conn.commit()
    call2 = tools.start_call("CUST099", mode="sim", enforce_hours=False, source="dashboard", conn=conn)
    assert tools.verify_identity(call2["call_id"], answer, conn)["ok"] is True
    assert tools.get_failed_payment(call2["call_id"], conn)["amount_due"] == 1500.0

    seen = set()
    for _ in range(20):
        link = tools.create_payment_link("CUST099", base_url="https://x.test", conn=conn)
        assert len(link["token"]) == 6 and link["token"] not in seen
        seen.add(link["token"])
    assert link["url"].startswith("https://x.test/pay/")
    ctx = tools.get_link_context(link["token"], conn)
    assert ctx["status"] == "ok" and 0 < ctx["expires_in"] <= 600
    old = tools.create_payment_link("CUST099", ttl_minutes=-1, conn=conn)
    assert tools.get_link_context(old["token"], conn)["status"] == "expired"

    hand = tools.request_human_handoff(call2["call_id"], "asked_for_human", conn=conn)
    assert hand["ok"] and hand["handoff_id"] > 0
    tools.log_outcome(call["call_id"], "opted_out", conn=conn)
    dnc = conn.execute("select do_not_call from customers where customer_id = 'CUST001'").fetchone()[0]
    assert dnc == 1
    conn.close()
