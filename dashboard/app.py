"""Merchant dashboard: metrics + queue, customers, calls, handoffs, audit, live."""
import sys
from pathlib import Path

import streamlit as st

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from app.db import db_file, get_conn
from dashboard import store
from dashboard.views import audit, calls, customers, handoffs, live, queue

st.set_page_config(page_title="autopay merchant (synthetic demo)", layout="wide")
st.title("autopay recovery — merchant dashboard")
st.caption("all data synthetic. phones masked by default.")

if not db_file.exists():
    st.warning("no database yet — run `uv run autopay` once to create and seed it.")
    st.stop()

conn = get_conn()
try:
    top = store.metrics(conn)
    tiles = st.columns(6)
    tiles[0].metric("customers", top["customers"])
    tiles[1].metric("failed", top["failed"])
    tiles[2].metric("at risk (rs)", f"{top['at_risk']:,.2f}")
    tiles[3].metric("recovered", top["recovered"])
    tiles[4].metric("open handoffs", top["open_handoffs"])
    tiles[5].metric("calls today", top["calls_today"])

    tabs = st.tabs(["queue", "customers", "calls", "handoffs", "audit", "live"])
    with tabs[0]:
        queue.render(conn)
    with tabs[1]:
        customers.render(conn)
    with tabs[2]:
        calls.render(conn)
    with tabs[3]:
        handoffs.render(conn)
    with tabs[4]:
        audit.render(conn)
    with tabs[5]:
        live.render(conn)
finally:
    conn.close()
