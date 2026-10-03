"""Live tab: auto-refreshing feed while the agent works."""
import streamlit as st

from app.db import get_conn
from dashboard import store


@st.fragment(run_every=5)
def feed():
    conn = get_conn()
    try:
        st.caption("refreshes every 5s — keep open during calls")
        left, right = st.columns(2)
        with left:
            st.markdown("**latest calls**")
            for call in store.list_calls(conn, 8):
                st.text(f"#{call['call_id']} {call['customer_id']} {call['tier'] or '?'} → {call['outcome'] or 'open'}")
        with right:
            st.markdown("**latest tool activity**")
            for entry in store.audit_tail(conn, 12):
                st.text(f"{entry['ts'][11:19]} {entry['actor']}/{entry['tool']} {entry['status']}")
    finally:
        conn.close()


def render(conn):
    st.subheader("live feed")
    feed()
