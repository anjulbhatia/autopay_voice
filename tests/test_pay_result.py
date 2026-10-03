"""POST /pay/result: outcome notify burns link on paid, audits both, rejects bad tokens."""
from fastapi.testclient import TestClient

from app import api
from app.db import get_conn, init_db


def make_link(conn, token, customer="CUST001", expires="2099-01-01T00:00:00+00:00"):
    conn.execute(
        "insert or ignore into customers (customer_id, name, phone, amount_due) values (?,?,?,?)",
        (customer, "Test User", "+91-90000-00099", 999.0),
    )
    conn.execute(
        "insert into payment_links (token, customer_id, kind, channel, created_at, expires_at)"
        " values (?,?,?,?,?,?)",
        (token, customer, "pay_now", "console", "2026-01-01T00:00:00+00:00", expires),
    )
    conn.commit()


def test_pay_result_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'r.db'}")
    conn = get_conn()
    init_db(conn)
    make_link(conn, "tok-paid")
    make_link(conn, "tok-failed")
    make_link(conn, "tok-old", expires="2020-01-01T00:00:00+00:00")
    conn.close()
    client = TestClient(api.app)

    assert client.post("/pay/result", json={"token": "nope", "outcome": "paid"}).status_code == 404

    paid = client.post("/pay/result",
                       json={"token": "tok-paid", "outcome": "paid", "method": "card", "ref": "DEMO-X"})
    assert paid.status_code == 200
    assert paid.json()["payment_status"] == "recovered"
    assert client.post("/pay/result", json={"token": "tok-paid", "outcome": "paid"}).status_code == 409

    failed = client.post("/pay/result", json={"token": "tok-failed", "outcome": "failed", "method": "upi"})
    assert failed.status_code == 200
    assert failed.json()["payment_status"] == "failed"
    # failed attempt does not burn the link: retry still accepted
    retry = client.post("/pay/result", json={"token": "tok-failed", "outcome": "paid"})
    assert retry.status_code == 200

    assert client.post("/pay/result", json={"token": "tok-old", "outcome": "paid"}).status_code == 410

    check = get_conn()
    n = check.execute("select count(*) from audit_log where tool = 'pay_result'").fetchone()[0]
    assert n == 3  # paid + failed + retry-paid
    check.close()
