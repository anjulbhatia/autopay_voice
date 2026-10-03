"""agent.py assembly/contracts + provider.py payload/guards (no network)."""
import httpx

from app import agent, provider
from app.db import get_conn, init_db
from app import tools


def customer_row(conn, customer_id="CUST001"):
    return conn.execute("select * from customers where customer_id = ?", (customer_id,)).fetchone()


def test_prompt_leaks_nothing_pre_verify(tmp_path):
    conn = get_conn(tmp_path / "a.db")
    init_db(conn)
    tools.ingest_customers_data("data/customers.json", conn)
    row = customer_row(conn)
    pre = agent.assemble_prompt(row, "standard", False, ["r1"])
    assert "Guest" not in pre  # uses real first name for greeting
    assert str(row["amount_due"]) not in pre
    assert row["past_call_notes"] not in pre
    assert row["phone"] not in pre
    assert "ANTI-HALLUCINATION" in pre
    post = agent.assemble_prompt(row, "standard", True, ["r1"])
    assert str(row["amount_due"]) in post
    conn.close()


def test_call_plan_and_suspicious():
    assert agent.call_plan("short")["max_seconds"] == 90
    assert agent.call_plan("extended")["offer_human_at_seconds"] == 120
    assert agent.suspicious(None) is True
    assert agent.suspicious({"verified": 0, "verify_attempts": 2, "outcome": None}) is True
    assert agent.suspicious({"verified": 1, "verify_attempts": 1, "outcome": None}) is False
    assert agent.suspicious({"verified": 0, "verify_attempts": 0, "outcome": "wrong_person"}) is True


def test_provider_guards(monkeypatch):
    monkeypatch.delenv("VAPI_API_KEY", raising=False)
    try:
        provider.api_key()
        raise AssertionError("should have raised")
    except provider.vapi_error:
        pass
    payload = provider.build_assistant("sys", "https://base.test/")
    assert payload["serverUrl"] == "https://base.test/vapi/tool"
    assert [t["name"] for t in payload["serverTools"]] == provider.tool_names

    calls = {}

    class fake_response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "call-1", "webCallUrl": "https://vapi.test/c/1"}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["body"] = json
        return fake_response()

    monkeypatch.setattr(httpx, "post", fake_post)
    out = provider.start_web_call("sys", "https://base.test", key="k")
    assert out["id"] == "call-1" and calls["url"].endswith("/call")
    try:
        provider.start_phone_call("sys", "https://base.test", "+91-90000-00001")
        raise AssertionError("should have raised")
    except provider.vapi_error:
        pass
