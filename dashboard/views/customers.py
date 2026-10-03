"""Customers tab: filterable masked table."""
import streamlit as st

from dashboard import store

statuses = ["all", "failed", "link_sent", "recovered", "handoff", "opted_out"]


def render(conn):
    st.subheader("customers")
    status = st.selectbox("payment status", statuses)
    st.dataframe(store.list_customers(conn, status), use_container_width=True)
