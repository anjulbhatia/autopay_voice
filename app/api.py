"""FastAPI app: payment page + static assets, both served from web/."""
import json
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.db import get_conn, mask_phone, utcnow
from app import agent as agent_rules
from app import channels, dash, tools

web_dir = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="autopay_voice (synthetic demo)")

app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")
templates = Jinja2Templates(directory=web_dir)


class pay_result_in(BaseModel):
    token: str
    outcome: Literal["paid", "failed"]
    method: Optional[Literal["card", "upi"]] = None
    ref: Optional[str] = None


@app.post("/pay/result")
async def pay_result(body: pay_result_in):
    """Customer page reports a terminal outcome. Paid burns the link (single-use)
    and marks recovery; failed only audits so the same link stays retryable."""
    from app.tools import get_link_context
    found = get_link_context(body.token)
    if found["status"] == "unknown":
        raise HTTPException(status_code=404, detail="unknown token")
    if found["status"] == "used":
        raise HTTPException(status_code=409, detail="link already used")
    if found["status"] == "expired":
        raise HTTPException(status_code=410, detail="link expired")
    now = utcnow()
    conn = get_conn()
    try:
        status = "recovered" if body.outcome == "paid" else "failed"
        if body.outcome == "paid":
            conn.execute("update payment_links set used_at = ? where token = ?", (now, body.token))
            conn.execute("update customers set payment_status = 'recovered' where customer_id = ?",
                         (found["link"]["customer_id"],))
        conn.execute(
            "insert into audit_log (ts, actor, tool, args_masked, status) values (?,?,?,?,?)",
            (now, "pay-page", "pay_result",
             json.dumps({"token": body.token[:4] + "…", "outcome": body.outcome, "method": body.method}),
             "ok"),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "outcome": body.outcome, "payment_status": status}


def extract_tool_call(body):
    """Tolerant tool-call parser. Canonical shape: message.toolCalls[0]
    {name/function.name, arguments}. Confirm against current Vapi docs."""
    message = body.get("message", body) if isinstance(body, dict) else {}
    for key in ("toolCalls", "tool_calls"):
        found = message.get(key)
        if found:
            return found[0]
    for key in ("toolCall", "functionCall", "function_call"):
        single = message.get(key)
        if single:
            return single
    return {}


def provider_call_id(body):
    message = body.get("message", {}) if isinstance(body.get("message"), dict) else {}
    return (body.get("call", {}) or {}).get("id") or body.get("callId") or (message.get("call", {}) or {}).get("id")


@app.post("/vapi/tool")
async def vapi_tool(request: Request):
    """In-call tool dispatch. Binds provider call id -> internal call;
    the model never supplies a customer id. Always 200 with result/error
    so the voice loop keeps talking instead of dropping."""
    import os
    body = await request.json()
    raw = extract_tool_call(body)
    name = raw.get("name") or (raw.get("function", {}) or {}).get("name", "")
    args = raw.get("arguments") or raw.get("args") or raw.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except ValueError:
            args = {}
    conn = get_conn()
    try:
        vapi_id = provider_call_id(body)
        row = conn.execute("select call_id from calls where vapi_call_id = ?", (vapi_id,)).fetchone() \
            if vapi_id else None
        if row is None:
            return {"error": "unknown call; only the generic message may be spoken"}
        call_id = row["call_id"]
        base_url = os.environ.get("BASE_URL", "")
        if name == "verify_identity":
            return {"result": tools.verify_identity(call_id, args.get("answer"), conn)}
        if name == "get_failed_payment":
            return {"result": tools.get_failed_payment(call_id, conn)}
        if name == "send_payment_link":
            return {"result": tools.send_payment_link(call_id, args.get("channel", "console"), base_url, conn)}
        if name == "schedule_retry":
            return {"result": tools.schedule_retry(call_id, args.get("when"), conn)}
        if name == "request_human_handoff":
            return {"result": tools.request_human_handoff(call_id, args.get("reason", "asked_for_human"),
                                                          args.get("notes", ""), conn)}
        if name == "log_outcome":
            return {"result": tools.log_outcome(call_id, args.get("result", "failed"), args.get("notes", ""), conn)}
        return {"error": f"unknown tool {name!r}"}
    finally:
        conn.close()


@app.post("/vapi/events")
async def vapi_events(request: Request):
    """End-of-call report: persist transcript, run guardrail scan, fill
    outcome only when the call never logged one."""
    body = await request.json()
    message = body.get("message", {}) if isinstance(body.get("message"), dict) else {}
    if message.get("type") not in ("end-of-call-report", "end_of_call_report", "call-ended"):
        return {"ok": True, "ignored": True}
    conn = get_conn()
    try:
        vapi_id = provider_call_id(body)
        row = conn.execute("select call_id, outcome from calls where vapi_call_id = ?",
                           (vapi_id,)).fetchone() if vapi_id else None
        if row is None:
            return {"ok": False, "error": "unknown call"}
        transcript = message.get("transcript") or message.get("summary") or ""
        verdict = agent_rules.judge_scan(transcript)
        ended = (message.get("endedReason") or "").lower()
        mapped = "no_answer" if "no-answer" in ended or "did-not-answer" in ended else None
        conn.execute("update calls set transcript = ?, judge_json = ?, ended_at = ?,"
                     " outcome = coalesce(outcome, ?) where call_id = ?",
                     (transcript, json.dumps(verdict), utcnow(), mapped, row["call_id"]))
        conn.commit()
        return {"ok": True, "judge": verdict}
    finally:
        conn.close()


@app.get("/pay/{token}", response_class=HTMLResponse)
async def pay_page(request: Request, token: str):
    # Real link lookup: unknown -> 404, used/expired -> dismissed render.
    # currency drives the Jinja symbol pick (INR -> ₹, else $).
    from app.tools import get_link_context
    found = get_link_context(token)
    if found["status"] == "unknown":
        raise HTTPException(status_code=404, detail="unknown token")
    customer = found["customer"]
    return templates.TemplateResponse(
        request=request,
        name="pay.html",
        context={"token_id": token, "merchant_name": "Demo Merchant",
                 "amount_due": customer["amount_due"], "currency": "INR",
                 "customer_name": customer["name"], "phone": customer["phone"],
                 "transaction_id": "TXN-" + token[:8].upper(),
                 "expires_in": found["expires_in"]},
    )


@app.get("/console", response_class=HTMLResponse)
async def console_page(request: Request):
    import os
    conn = get_conn()
    try:
        customers = [dict(r) for r in conn.execute(
            "select customer_id, name from customers order by customer_id").fetchall()]
        funnel = {r["payment_status"]: r["n"] for r in conn.execute(
            "select payment_status, count(*) as n from customers group by payment_status").fetchall()}
        total = sum(funnel.values()) or 1
        calls = conn.execute("select count(*) from calls").fetchone()[0]
    finally:
        conn.close()
    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={"customers": customers, "funnel": funnel,
                 "recovery_rate": round(100 * funnel.get("recovered", 0) / total),
                 "call_count": calls, "default_base": os.environ.get("BASE_URL", "")})


@app.get("/partials/queue", response_class=HTMLResponse)
async def partial_queue(mode: str = "expected_value"):
    return dash.queue_partial(mode if mode in ("expected_value", "easiest", "highest") else "expected_value")


@app.post("/partials/queue/start", response_class=HTMLResponse)
async def partial_start(request: Request):
    body = await request.json()
    try:
        started = tools.start_call(body.get("customer_id", ""), mode="web", source="dashboard")
    except ValueError as exc:
        return dash.message(str(exc), good=False)
    return dash.message(f"call {started['call_id']} open ({started['tier']}, p={started['p_pay']})")


@app.get("/partials/customers", response_class=HTMLResponse)
async def partial_customers(status: str = "all"):
    return dash.customers_partial(status)


@app.get("/partials/calls", response_class=HTMLResponse)
async def partial_calls():
    return dash.calls_partial()


@app.post("/partials/calls/cut", response_class=HTMLResponse)
async def partial_cut(request: Request):
    body = await request.json()
    tools.log_outcome(int(body.get("call_id", 0)), "no_answer", "cut from console")
    return dash.message(f"call {body.get('call_id')} cut")


@app.post("/partials/calls/join", response_class=HTMLResponse)
async def partial_join(request: Request):
    body = await request.json()
    result = tools.request_human_handoff(int(body.get("call_id", 0)), "human_joined", "merchant joined")
    return dash.message(f"joined — handoff {result['handoff_id']} open")


@app.get("/partials/handoffs", response_class=HTMLResponse)
async def partial_handoffs(open: int = 1):
    return dash.handoffs_partial(bool(open))


@app.post("/partials/handoffs/resolve", response_class=HTMLResponse)
async def partial_resolve(request: Request):
    body = await request.json()
    conn = get_conn()
    try:
        conn.execute("update handoffs set status = 'done', resolved_at = ? where handoff_id = ?",
                     (utcnow(), int(body.get("handoff_id", 0))))
        tools.log_audit(conn, "merchant", "resolve_handoff",
                        f"handoff_id={body.get('handoff_id')}", "ok")
        conn.commit()
    finally:
        conn.close()
    return dash.message(f"handoff {body.get('handoff_id')} resolved")


@app.get("/partials/audit", response_class=HTMLResponse)
async def partial_audit(limit: int = 50):
    return dash.audit_partial(max(1, min(limit, 200)))


@app.get("/partials/events", response_class=HTMLResponse)
async def partial_events():
    return dash.events_partial()


@app.post("/partials/links", response_class=HTMLResponse)
async def partial_link(request: Request):
    import os
    body = await request.json()
    customer_id = body.get("customer_id", "")
    channel = body.get("channel", "console")
    link = tools.create_payment_link(customer_id, body.get("kind", "pay_now"), channel,
                                     body.get("base_url") or None, int(body.get("ttl", 10)))
    conn = get_conn()
    try:
        person = conn.execute("select name, phone, amount_due from customers where customer_id = ?",
                              (customer_id,)).fetchone()
    finally:
        conn.close()
    text = channels.render(channel, person["name"], person["amount_due"], link["url"])
    receipt = channels.send(channel, mask_phone(person["phone"]), text)
    return (f"<p class='font-mono text-sm break-all bg-neutral-100 rounded px-2 py-1'>{link['url']}"
            f" <button onclick=\"navigator.clipboard.writeText('{link['url']}')\""
            f" class='underline text-xs'>copy</button></p>"
            f"<p class='text-xs text-neutral-500 mt-1'>expires {link['expires_at']} · "
            f"{receipt['channel']} {receipt['status']}</p>")
