"""HTMX console: page, partials, link flow, call/handoff actions."""
from fastapi.testclient import TestClient

from app import api, tools
from app.db import get_conn, init_db


def live_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'c.db'}")
    conn = get_conn()
    init_db(conn)
    tools.ingest_customers_data("data/customers.json", conn)
    conn.close()
    return TestClient(api.app)


def test_console_flows(tmp_path, monkeypatch):
    client = live_client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.agent.calling_allowed", lambda now=None: True)
    assert client.get("/console").status_code == 200
    queue = client.get("/partials/queue?mode=easiest")
    assert queue.status_code == 200 and "CUST" in queue.text
    assert "CUST" in client.get("/partials/customers?status=failed").text
    assert "no calls yet" in client.get("/partials/calls").text.lower()
    assert "reason" in client.get("/partials/handoffs").text.lower() or "none" in client.get("/partials/handoffs").text
    assert "ingest_customers_data" in client.get("/partials/audit").text
    assert "ingest_customers_data" in client.get("/partials/events").text

    link = client.post("/partials/links", json={"customer_id": "CUST001", "kind": "pay_now",
                                                "channel": "whatsapp", "ttl": 10,
                                                "base_url": "https://base.test"}).text
    assert "https://base.test/pay/" in link and "copy" in link and "mock-sent" in link

    started = client.post("/partials/queue/start", json={"customer_id": "CUST002"})
    assert "open" in started.text
    assert "hx-post" in client.get("/partials/calls").text  # cut/join buttons now live
    conn = get_conn()
    call_id = conn.execute("select call_id from calls where customer_id = 'CUST002'").fetchone()[0]
    joined = client.post("/partials/calls/join", json={"call_id": call_id})
    assert "handoff" in joined.text
    hand_id = conn.execute("select handoff_id from handoffs").fetchone()[0]
    resolved = client.post("/partials/handoffs/resolve", json={"handoff_id": hand_id})
    assert "resolved" in resolved.text
    cut = client.post("/partials/calls/cut", json={"call_id": call_id})
    assert "cut" in cut.text
    detail = client.get(f"/partials/calls/{call_id}")
    assert detail.status_code == 200 and "CUST002" in detail.text
    assert client.get("/partials/calls/99999").status_code == 404
    hdetail = client.get(f"/partials/handoffs/{hand_id}")
    assert hdetail.status_code == 200 and "human_joined" in hdetail.text
    assert client.get("/partials/handoffs/99999").status_code == 404
    conn.close()


def test_web_start_binds_provider_call(tmp_path, monkeypatch):
    client = live_client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.agent.calling_allowed", lambda now=None: True)
    monkeypatch.setattr("app.api.provider.start_web_call",
                        lambda prompt, base: {"id": "vapi-1", "webCallUrl": "https://vapi.test/c/1"})
    started = client.post("/partials/queue/start", json={"customer_id": "CUST003", "mode": "web"})
    assert "open" in started.text and "https://vapi.test/c/1" in started.text
    conn = get_conn()
    vapi_id = conn.execute("select vapi_call_id from calls where customer_id = 'CUST003'"
                           " order by call_id desc limit 1").fetchone()[0]
    conn.close()
    assert vapi_id == "vapi-1"
