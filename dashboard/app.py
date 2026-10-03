"""merchant dashboard (primitive): read-only sqlite overview."""
import sqlite3
import sys
from pathlib import Path

import streamlit as st

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from app.db import db_file, mask_phone

st.set_page_config(page_title="autopay merchant (synthetic demo)", layout="wide")
st.title("autopay recovery — merchant dashboard")
st.caption("all data synthetic. phones masked by default.")

if not db_file.exists():
    st.warning("no database yet — run `uv run autopay` once to create and seed it.")
    st.stop()

conn = sqlite3.connect(db_file)
conn.row_factory = sqlite3.Row

total = conn.execute("select count(*) from customers").fetchone()[0]
failed = conn.execute("select count(*) from customers where payment_status = 'failed'").fetchone()[0]
at_risk = conn.execute("select coalesce(sum(amount_due), 0) from customers "
                       "where payment_status = 'failed'").fetchone()[0]
open_handoffs = conn.execute("select count(*) from handoffs where status = 'open'").fetchone()[0]
calls_today = conn.execute("select count(*) from calls "
                           "where date(started_at) = date('now')").fetchone()[0]

top = st.columns(5)
top[0].metric("customers", total)
top[1].metric("failed payments", failed)
top[2].metric("amount at risk (rs)", f"{at_risk:,.2f}")
top[3].metric("open handoffs", open_handoffs)
top[4].metric("calls today", calls_today)

status = st.selectbox("payment status", ["all", "failed", "link_sent", "recovered", "handoff", "opted_out"])
query = ("select customer_id, name, phone, amount_due, due_date, "
         "failure_reason, payment_status, attempts_total, do_not_call from customers")
params: tuple = ()
if status != "all":
    query += " where payment_status = ?"
    params = (status,)
query += " order by customer_id"

rows = []
for row in conn.execute(query, params).fetchall():
    item = dict(row)
    item["phone"] = mask_phone(item["phone"])
    rows.append(item)
conn.close()

st.subheader("customers")
st.dataframe(rows, use_container_width=True)
