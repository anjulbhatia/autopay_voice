"""smoke: schema builds, seed loads 10, hashing helper round-trips."""
import hashlib

from app.db import count_calls_today, get_conn, init_db, seed_from_json, verify_security_answer


def test_seed_and_helpers(tmp_path):
    db_path = tmp_path / "smoke.db"
    conn = get_conn(db_path)
    init_db(conn)
    new_rows = seed_from_json("data/customers.json", conn)
    assert new_rows == 10
    assert seed_from_json("data/customers.json", conn) == 0  # idempotent
    total = conn.execute("select count(*) from customers").fetchone()[0]
    assert total == 10

    salt, answer = "abc123", "1990"
    stored = hashlib.sha256((salt + answer).encode()).hexdigest()
    assert verify_security_answer(stored, salt, "1990") is True
    assert verify_security_answer(stored, salt, "1991") is False

    assert count_calls_today(conn, "CUST001") == 0
    conn.close()
