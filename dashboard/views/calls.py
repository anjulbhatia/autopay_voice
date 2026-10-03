"""Calls tab: recent calls + full detail (transcript, score, verify state)."""
import json

import streamlit as st

from dashboard import store


def render(conn):
    st.subheader("calls")
    calls = store.list_calls(conn)
    if not calls:
        st.info("no calls yet — start one from the queue tab.")
        return
    st.dataframe(calls, use_container_width=True)
    pick = st.selectbox("inspect call", [c["call_id"] for c in calls])
    detail = store.call_detail(conn, pick)
    left, right = st.columns(2)
    left.metric("tier", detail["tier"] or "—")
    right.metric("outcome", detail["outcome"] or "open")
    st.caption(f"verified: {bool(detail['verified'])} · tries: {detail['verify_attempts']} · p_pay: {detail['p_pay']}")
    if detail["score_reasons"]:
        st.caption("why: " + "; ".join(json.loads(detail["score_reasons"])))
    if detail["transcript"]:
        with st.expander("transcript"):
            st.text(detail["transcript"])
    if detail["judge_json"]:
        with st.expander("guardrail audit"):
            st.json(json.loads(detail["judge_json"]))
