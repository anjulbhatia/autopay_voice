"""Prod-sim e2e: merchant console -> voice tools -> pay page -> events.

Simulates production happy path with TestClient (no network, provider
mocked at the SDK boundary only). Covers /pay/test backdoor gating.
"""
import hashlib

from fastapi.testclient import TestClient

from app import api, tools
from app.db import get_conn, init_db

E2E_ID = "CUSTE2E"
E2E_SALT = "e2esalt42"
E2E_ANSWER = "1985"


def live_client(tmp_path, monkeypatch, name="e2e.db"):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / name}")
    conn = get_conn()
    init_db(conn)
    tools.ingest_customers_data("data/customers.json", conn)
    conn.execute(
        "insert or ignore into customers (customer_id, name, phone, amount_due,"
        " failure_reason, security_question, security_answer_hash, security_salt)"
        " values (?,?,?,?,?,?,?,?)",
        (E2E_ID, "E2E Prod User", "+91-90000-00042", 2500.0, "insufficient_balance",
         "Birth year?", hashlib.sha256((E2E_SALT + E2E_ANSWER).encode()).hexdigest(), E2E_SALT),
    )
    conn.commit()
    conn.close()
    return TestClient(api.app)


def vapi_tool(client, vapi_id, tid, name, args):
    return client.post("/vapi/tool", json={
        "call": {"id": vapi_id},
        "message": {"type": "tool-calls",
                    "toolCallList": [{"id": tid, "function": {"name": name, "arguments": args}}]},
    })


def test_pay_test_backdoor_gated(tmp_path, monkeypatch):
    client = live_client(tmp_path, monkeypatch, "e2e-gate.db")
    monkeypatch.delenv("ALLOW_TEST_ROUTES", raising=False)
    assert client.get("/pay/test").status_code == 404

    monkeypatch.setenv("ALLOW_TEST_ROUTES", "1")
    r = client.get("/pay/test", follow_redirects=False)
    assert r.status_code == 303
    assert "/pay/" in r.headers["location"]
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "Demo Merchant" in page.text
    assert client.get("/pay/test?customer_id=NOPE").status_code == 404


def test_e2e_prod_recovery(tmp_path, monkeypatch):
    client = live_client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.agent.calling_allowed", lambda now=None: True)
    monkeypatch.setattr("app.api.provider.start_web_call",
                        lambda prompt, base: {"id": "vapi-e2e-1",
                                              "webCallUrl": "https://vapi.test/c/e2e"})

    # Merchant: queue visible, start web call from console.
    assert client.get("/console").status_code == 200
    started = client.post("/partials/queue/start",
                          json={"customer_id": E2E_ID, "mode": "web"})
    assert "open" in started.text and "https://vapi.test/c/e2e" in started.text

    conn = get_conn()
    call = conn.execute("select call_id, vapi_call_id from calls where customer_id = ?"
                        " order by call_id desc limit 1", (E2E_ID,)).fetchone()
    conn.close()
    assert call["vapi_call_id"] == "vapi-e2e-1"
    vid = call["vapi_call_id"]

    # Voice: verify (wrong then right), read dues, agree -> link.
    assert vapi_tool(client, vid, "t1", "verify_identity", {"answer": "0000"}).json()["results"][0]["result"].find('"ok": false') >= 0
    assert vapi_tool(client, vid, "t2", "verify_identity", {"answer": E2E_ANSWER}).json()["results"][0]["result"].find('"ok": true') >= 0
    dues = vapi_tool(client, vid, "t3", "get_failed_payment", {}).json()["results"][0]
    assert '"amount_due": 2500.0' in dues["result"]
    sent = vapi_tool(client, vid, "t4", "send_payment_link", {"channel": "console"}).json()["results"][0]
    assert "error" not in sent
    token = sent["result"].split("/pay/")[1].split('"')[0]

    # Customer: open link, pay.
    assert client.get(f"/pay/{token}").status_code == 200
    paid = client.post("/pay/result", json={"token": token, "outcome": "paid", "method": "upi"})
    assert paid.status_code == 200 and paid.json()["payment_status"] == "recovered"
    assert client.post("/pay/result", json={"token": token, "outcome": "paid"}).status_code == 409

    # Voice: close call, end-of-call report must not clobber outcome.
    vapi_tool(client, vid, "t5", "log_outcome", {"result": "link_sent"})
    rep = client.post("/vapi/events", json={
        "call": {"id": vid},
        "message": {"type": "end-of-call-report", "endedReason": "customer-ended-call",
                    "artifact": {"transcript": "agent: hello, may I confirm? caller: yes 1985. agent: link sent."}},
    })
    assert rep.status_code == 200 and rep.json()["judge"]["passed"] is True

    check = get_conn()
    try:
        cust = check.execute("select payment_status from customers where customer_id = ?",
                             (E2E_ID,)).fetchone()
        assert cust["payment_status"] == "recovered"
        call_row = check.execute("select outcome, transcript from calls where vapi_call_id = ?",
                                 (vid,)).fetchone()
        assert call_row["outcome"] == "link_sent" and "1985" in (call_row["transcript"] or "")
        link_row = check.execute("select used_at from payment_links where token = ?", (token,)).fetchone()
        assert link_row["used_at"] is not None
        n = check.execute("select count(*) from audit_log where call_id = ?",
                          (call["call_id"],)).fetchone()[0]
        assert n >= 5  # verify x2, dues, link mint+send, outcome
    finally:
        check.close()
