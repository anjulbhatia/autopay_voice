"""agent.py assembly/contracts + provider.py payload/guards (no network)."""
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
    assert payload["server"]["url"] == "https://base.test/vapi/tool"
    assert payload["first_message"] == agent.first_message()
    assert "₹" not in payload["first_message"] and "$" not in payload["first_message"]
    assert payload["serverMessages"] == ["tool-calls", "end-of-call-report"]
    assistants_tools = payload["model"]["tools"]
    assert [t["function"]["name"] for t in assistants_tools] == provider.tool_names
    assert all(t["type"] == "function" for t in assistants_tools)
    assert all(t["server"]["url"] == "https://base.test/vapi/tool" for t in assistants_tools)

    class fake_calls:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return {"id": "call-1", "webCallUrl": "https://vapi.test/c/1"}

    class fake_client:
        def __init__(self):
            self.calls = fake_calls()

    web = fake_client()
    out = provider.start_web_call("sys", "https://base.test", client=web)
    assert out["id"] == "call-1" and "phone_number_id" not in web.calls.kwargs
    assert web.calls.kwargs["assistant"]["server"]["url"] == "https://base.test/vapi/tool"
    try:
        provider.start_phone_call("sys", "https://base.test", "+91-90000-00001", client=fake_client())
        raise AssertionError("should have raised")
    except provider.vapi_error:
        pass
    monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "phone-uuid-1")
    phone = fake_client()
    placed = provider.start_phone_call("sys", "https://base.test", "+91-90000-00001",
                                       confirm=True, client=phone)
    assert placed["id"] == "call-1"
    assert phone.calls.kwargs["customer"] == {"number": "+91-90000-00001"}
    assert phone.calls.kwargs["phone_number_id"] == "phone-uuid-1"


def test_tool_envelope_helpers():
    body = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "tu-1", "type": "function",
         "function": {"name": "verify_identity", "arguments": {"answer": "1990"}}},
        {"id": "tu-2", "type": "function",
         "function": {"name": "log_outcome", "arguments": '{"result": "failed"}'}},
    ]}}
    items = provider.tool_calls_of(body)
    assert [provider.tool_call_id_of(i) for i in items] == ["tu-1", "tu-2"]
    assert provider.tool_name_args(items[0]) == ("verify_identity", {"answer": "1990"})
    assert provider.tool_name_args(items[1]) == ("log_outcome", {"result": "failed"})
    legacy = {"functionCall": {"name": "verify_identity", "parameters": {"answer": "1990"}}}
    assert provider.tool_name_args(provider.tool_calls_of(
        {"message": legacy})[0]) == ("verify_identity", {"answer": "1990"})
    ok = provider.tool_result("tu-1", {"ok": True})
    assert ok == {"toolCallId": "tu-1", "result": '{"ok": true}'}
    err = provider.tool_error("tu-2", "nope")
    assert err == {"toolCallId": "tu-2", "error": "nope"}
    assert provider.tool_calls_of({}) == []
