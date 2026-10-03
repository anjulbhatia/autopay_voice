"""Queue tab: ranked outreach + merchant pick + call start."""
import json

import streamlit as st

from app import agent, provider, tools
from dashboard import store

modes = {"expected_value": "easiest value first", "easiest": "easiest first", "highest": "highest amount first"}


def render(conn):
    st.subheader("outreach queue")
    mode = st.radio("order by", list(modes), format_func=modes.get, horizontal=True)
    queue = store.ranked_queue(mode)
    if not queue["eligible"]:
        st.info("queue empty — everyone is capped, cooling down, or opted out.")
    else:
        st.dataframe(queue["eligible"], use_container_width=True)
    if queue["ineligible"]:
        with st.expander(f"skipped ({len(queue['ineligible'])})"):
            st.dataframe(queue["ineligible"], use_container_width=True)

    st.divider()
    st.subheader("start a call")
    options = [c["customer_id"] for c in queue["eligible"]]
    pick = st.selectbox("customer", ["—"] + options)
    if pick == "—":
        return
    row = conn.execute("select * from customers where customer_id = ?", (pick,)).fetchone()
    item = next(c for c in queue["eligible"] if c["customer_id"] == pick)
    left, right = st.columns(2)
    left.metric("p_pay", item["p_pay"])
    right.metric("tier", item["tier"])
    st.caption("why: " + "; ".join(item["reasons"]))
    with st.expander("prompt preview (pre-verify, what the model sees)"):
        st.code(agent.assemble_prompt(row, item["tier"], False, item["reasons"]), language="markdown")
    if st.button("start call", type="primary"):
        try:
            provider.api_key()
        except provider.vapi_error as exc:
            st.error(str(exc))
            return
        try:
            started = tools.start_call(pick, mode="web", conn=conn)
        except ValueError as exc:
            st.warning(str(exc))
            return
        system = agent.assemble_prompt(row, started["tier"], False, started["reasons"])
        try:
            call = provider.start_web_call(system, base_url="")
        except Exception as exc:  # provider/net failure: call row stays, attempt counted
            st.error(f"vapi failed ({exc}); call {started['call_id']} logged, retry from calls tab")
            return
        st.success(f"call {started['call_id']} live — join at {call.get('webCallUrl', 'provider console')}")
        st.json({k: v for k, v in started.items() if k != "reasons"})
