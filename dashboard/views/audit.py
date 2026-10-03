"""Audit tab: every tool call, masked args."""
import streamlit as st

from dashboard import store


def render(conn):
    st.subheader("audit trail")
    limit = st.slider("rows", 20, 500, 100)
    st.dataframe(store.audit_tail(conn, limit), use_container_width=True)
