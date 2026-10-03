"""Handoffs tab: read each handoff, resolve when done."""
import streamlit as st

from dashboard import store


def render(conn):
    st.subheader("human handoffs")
    only_open = st.checkbox("open only", value=True)
    items = store.list_handoffs(conn, only_open)
    if not items:
        st.info("no handoffs in this view.")
        return
    st.dataframe(items, use_container_width=True)
    pick = st.selectbox("read handoff", [h["handoff_id"] for h in items])
    detail = store.handoff_detail(conn, pick)
    st.markdown(f"**{detail['name']}** · {detail['phone']} · call {detail['call_id']}")
    st.caption(f"reason: {detail['reason']} · status: {detail['status']} · opened: {detail['created_at']}")
    if detail["notes"]:
        st.text(detail["notes"])
    if detail["status"] == "open" and st.button("mark resolved"):
        store.resolve_handoff(conn, pick)
        st.rerun()
