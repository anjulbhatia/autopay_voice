"""Read helpers + the two merchant writes (resolve handoff, start call)."""
from app import tools
from app.db import get_conn, mask_phone, utcnow


def metrics(conn):
    return {
        "customers": conn.execute("select count(*) from customers").fetchone()[0],
        "failed": conn.execute("select count(*) from customers where payment_status = 'failed'").fetchone()[0],
        "at_risk": conn.execute("select coalesce(sum(amount_due), 0) from customers"
                                " where payment_status = 'failed'").fetchone()[0],
        "recovered": conn.execute("select count(*) from customers where payment_status = 'recovered'").fetchone()[0],
        "open_handoffs": conn.execute("select count(*) from handoffs where status = 'open'").fetchone()[0],
        "calls_today": conn.execute("select count(*) from calls where date(started_at) = date('now')").fetchone()[0],
    }


def list_customers(conn, status="all"):
    query = ("select customer_id, name, phone, amount_due, due_date, failure_reason,"
             " payment_status, attempts_total, do_not_call from customers")
    params: tuple = ()
    if status != "all":
        query += " where payment_status = ?"
        params = (status,)
    rows = []
    for row in conn.execute(query + " order by customer_id", params).fetchall():
        item = dict(row)
        item["phone"] = mask_phone(item["phone"])
        rows.append(item)
    return rows


def ranked_queue(mode="expected_value"):
    return tools.masked_queue(mode)


def list_calls(conn, limit=50):
    return [dict(r) for r in conn.execute(
        "select call_id, customer_id, mode, started_at, tier, p_pay,"
        " verified, outcome from calls order by call_id desc limit ?", (limit,)).fetchall()]


def call_detail(conn, call_id):
    call = conn.execute("select * from calls where call_id = ?", (call_id,)).fetchone()
    return dict(call) if call else None


def list_handoffs(conn, only_open=True):
    query = ("select handoff_id, customer_id, call_id, reason, status, created_at from handoffs")
    if only_open:
        query += " where status = 'open'"
    return [dict(r) for r in conn.execute(query + " order by handoff_id desc").fetchall()]


def handoff_detail(conn, handoff_id):
    row = conn.execute(
        "select handoffs.*, customers.name, customers.phone from handoffs"
        " join customers on customers.customer_id = handoffs.customer_id"
        " where handoff_id = ?", (handoff_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["phone"] = mask_phone(item["phone"])
    return item


def resolve_handoff(conn, handoff_id, actor="merchant"):
    conn.execute("update handoffs set status = 'done', resolved_at = ? where handoff_id = ?",
                 (utcnow(), handoff_id))
    conn.execute("insert into audit_log (ts, actor, tool, args_masked, status) values (?,?,?,?,?)",
                 (utcnow(), actor, "resolve_handoff", f"handoff_id={handoff_id}", "ok"))
    conn.commit()


def audit_tail(conn, limit=100):
    return [dict(r) for r in conn.execute(
        "select id, ts, call_id, actor, tool, args_masked, status from audit_log"
        " order by id desc limit ?", (limit,)).fetchall()]
