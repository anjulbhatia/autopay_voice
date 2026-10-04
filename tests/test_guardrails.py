"""Guardrails enforced in code: binding, gate, hours, caps, opt-out, judge, webhooks."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import agent, api, tools
from app.db import get_conn, init_db


def seed_known(conn, customer_id, answer, attempts=0):
    salt = "guard-salt"
    conn.execute(
        "insert into customers (customer_id, name, phone, amount_due, failure_reason,"
        " security_question, security_answer_hash, security_salt, attempts_total)"
        " values (?,?,?,?,?,?,?,?,?)",
        (customer_id, f"Guard {customer_id}", "+91-90000-00092", 500.0, "bank_decline",
         "Year?", hashlib.sha256((salt + answer).encode()).hexdigest(), salt, attempts),
    )
    conn.commit()
    return answer


def test_calls_start_from_dashboard_only(tmp_path):
    conn = get_conn(tmp_path / "s.db")
    init_db(conn)
    seed_known(conn, "GS001", "1111")
    try:
        tools.start_call("GS001", mode="sim", enforce_hours=False, conn=conn)
        raise AssertionError("non-dashboard start should refuse")
    except ValueError as exc:
        assert "dashboard" in str(exc)
    conn.close()


def test_per_call_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'g.db'}")
    conn = get_conn()
    init_db(conn)
    seed_known(conn, "GA001", "1111")
    seed_known(conn, "GA002", "2222")
    alice = tools.start_call("GA001", mode="sim", enforce_hours=False, source="dashboard", conn=conn)
    bob = tools.start_call("GA002", mode="sim", enforce_hours=False, source="dashboard", conn=conn)
    assert tools.verify_identity(alice["call_id"], "1111", conn)["ok"] is True
    # bob unverified: refused, and alice's data never reachable through bob's call
    assert tools.get_failed_payment(bob["call_id"], conn)["refused"] is True
    mine = tools.get_failed_payment(alice["call_id"], conn)
    assert mine["amount_due"] == 500.0
    conn.close()


def test_hours_and_caps(tmp_path):
    from app.agent import ist
    assert agent.calling_allowed(datetime(2026, 5, 1, 4, 0, tzinfo=timezone.utc)) is True  # 09:30 IST
    assert agent.calling_allowed(datetime(2026, 5, 1, 3, 0, tzinfo=timezone.utc)) is False  # 08:00 IST
    assert agent.calling_allowed(datetime(2026, 5, 1, 16, 0, tzinfo=timezone.utc)) is False  # 21:30 IST
    conn = get_conn(tmp_path / "h.db")
    init_db(conn)
    seed_known(conn, "GC001", "1111", attempts=5)
    try:
        tools.start_call("GC001", mode="sim", enforce_hours=False, source="dashboard", conn=conn)
        raise AssertionError("total cap should refuse")
    except ValueError:
        pass
    seed_known(conn, "GC002", "1111")
    # last contact old so only the daily cap can bite (mocked to 2 calls today)
    conn.execute("update customers set last_call_at = '2020-01-01T00:00:00+00:00' where customer_id = 'GC002'")
    conn.commit()
    import unittest.mock as mock
    with mock.patch("app.tools.count_calls_today", return_value=2):
        try:
            tools.start_call("GC002", mode="sim", enforce_hours=False, source="dashboard", conn=conn)
            raise AssertionError("daily cap should refuse")
        except ValueError:
            pass
    conn.close()


def test_opt_out_and_generic():
    message = agent.generic_message()
    assert "₹" not in message and "$" not in message
    assert not any(char.isdigit() for char in message)


def test_prompt_has_no_threats():
    # ban-list quotes ("Hard bans" paragraph) forbid the language; everywhere
    # else the prompt must never script it as agent speech
    lines = [line for line in agent.load_texts()["prompt"].lower().splitlines()
             if "no threats" not in line]
    body = "\n".join(lines)
    for phrase in agent.threat_phrases:
        assert phrase not in body, phrase


def test_vapi_webhooks(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'v.db'}")
    conn = get_conn()
    init_db(conn)
    seed_known(conn, "GV001", "1111")
    call = tools.start_call("GV001", mode="web", vapi_call_id="vapi-1", enforce_hours=False, source="dashboard", conn=conn)
    conn.close()
    client = TestClient(api.app)

    def tool_body(name, args):
        return {"call": {"id": "vapi-1"},
                "message": {"type": "tool-calls", "toolCallList": [
                    {"id": "tu-1", "type": "function",
                     "function": {"name": name, "arguments": args}}]}}

    def dispatched(resp):
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1 and results[0]["toolCallId"] == "tu-1"
        item = results[0]
        return json.loads(item["result"]) if "result" in item else item

    assert client.post("/vapi/tool", json={"call": {"id": "nope"},
                                           "message": {"type": "tool-calls", "toolCallList": [
                                               {"id": "tu-9", "type": "function",
                                                "function": {"name": "get_failed_payment",
                                                             "arguments": {}}}]}}
                       ).json()["results"][0]["error"]
    gated = dispatched(client.post("/vapi/tool", json=tool_body("get_failed_payment", {})))
    assert gated["refused"] is True
    verified = dispatched(client.post("/vapi/tool", json=tool_body("verify_identity", {"answer": "1111"})))
    assert verified["ok"] is True
    assert dispatched(client.post("/vapi/tool", json=tool_body("nope_tool", {})))["error"]

    evil = {"call": {"id": "vapi-1"}, "message": {"type": "end-of-call-report",
            "artifact": {"transcript": "Pay or face court. Give your OTP now."},
            "endedReason": "customer-ended-call"}}
    events = client.post("/vapi/events", json=evil).json()
    assert events["ok"] is True and events["judge"]["passed"] is False
    conn = get_conn()
    row = conn.execute("select transcript, judge_json from calls where call_id = ?",
                       (call["call_id"],)).fetchone()
    assert "court" in row["transcript"] and "violations" in row["judge_json"]
    conn.close()
    assert client.post("/vapi/events", json={"message": {"type": "end-of-call-report"}}).json()["ok"] is False
