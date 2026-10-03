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


async def _payload(request: Request) -> dict:
    """Accept JSON (tests, fetch) and urlencoded form (htmx forms)."""
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except ValueError:
            return {}
    try:
        body = await request.json()
        if isinstance(body, dict):
            return body
    except ValueError:
        pass
    try:
        form = await request.form()
        return {k: v for k, v in form.items()}
    except ValueError:
        return {}


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
        open_handoffs = conn.execute("select count(*) from handoffs where status = 'open'").fetchone()[0]
        open_calls = conn.execute("select count(*) from calls where outcome is null").fetchone()[0]
        at_risk = conn.execute("select coalesce(sum(amount_due), 0) from customers"
                               " where payment_status = 'failed'").fetchone()[0]
    finally:
        conn.close()
    from app import tools as campaign_tools
    queue_count = len(campaign_tools.masked_queue()["eligible"])
    return templates.TemplateResponse(
        request=request, name="console.html",
        context={"customers": customers, "funnel": funnel,
                 "recovery_rate": round(100 * funnel.get("recovered", 0) / total),
                 "call_count": calls, "open_handoffs": open_handoffs,
                 "queue_count": queue_count, "failed_count": funnel.get("failed", 0),
                 "at_risk": f"{at_risk:,.0f}", "open_calls": open_calls,
                 "default_base": os.environ.get("BASE_URL", "")})


@app.get("/partials/queue", response_class=HTMLResponse)
async def partial_queue(mode: str = "expected_value", q: str = "", tier: str = "all"):
    return dash.queue_partial(mode if mode in ("expected_value", "easiest", "highest") else "expected_value",
                              q=q, tier=tier)


@app.get("/partials/active-call", response_class=HTMLResponse)
async def partial_active_call():
    return dash.active_call_partial()


@app.post("/partials/queue/start", response_class=HTMLResponse)
async def partial_start(request: Request):
    body = await _payload(request)
    try:
        started = tools.start_call(body.get("customer_id", ""), mode="web", source="dashboard")
    except ValueError as exc:
        return dash.message(str(exc), good=False)
    return dash.message(f"call {started['call_id']} open ({started['tier']}, p={started['p_pay']})")


@app.get("/partials/customers", response_class=HTMLResponse)
async def partial_customers(status: str = "all", q: str = ""):
    return dash.customers_partial(status, q)


@app.get("/partials/customers/{customer_id}", response_class=HTMLResponse)
async def partial_customer_detail(customer_id: str):
    found = dash.customer_detail_partial(customer_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown customer")
    return found


@app.get("/partials/calls", response_class=HTMLResponse)
async def partial_calls(q: str = "", outcome: str = "all"):
    return dash.calls_partial(q=q, outcome=outcome)


@app.post("/partials/calls/outcome", response_class=HTMLResponse)
async def partial_outcome(request: Request):
    body = await _payload(request)
    tools.log_outcome(int(body.get("call_id", 0)), body.get("outcome", "failed"),
                      str(body.get("notes", ""))[:200])
    return dash.message(f"call {body.get('call_id')} → {body.get('outcome')}")


@app.post("/partials/calls/cut", response_class=HTMLResponse)
async def partial_cut(request: Request):
    body = await _payload(request)
    tools.log_outcome(int(body.get("call_id", 0)), "no_answer", "cut from console")
    return dash.message(f"call {body.get('call_id')} cut")


@app.post("/partials/calls/join", response_class=HTMLResponse)
async def partial_join(request: Request):
    body = await _payload(request)
    result = tools.request_human_handoff(int(body.get("call_id", 0)), "human_joined", "merchant joined")
    return dash.message(f"joined — handoff {result['handoff_id']} open")


@app.get("/partials/handoffs", response_class=HTMLResponse)
async def partial_handoffs(open: int = 1, q: str = ""):
    return dash.handoffs_partial(bool(open), q)


@app.post("/partials/handoffs/create", response_class=HTMLResponse)
async def partial_handoff_create(request: Request):
    body = await _payload(request)
    try:
        result = tools.request_human_handoff(int(body.get("call_id", 0)),
                                             body.get("reason", "asked_for_human"),
                                             str(body.get("notes", ""))[:500])
    except ValueError as exc:
        return dash.message(str(exc), good=False)
    return dash.message(f"handoff {result['handoff_id']} open for call {body.get('call_id')}")


@app.post("/partials/handoffs/resolve", response_class=HTMLResponse)
async def partial_resolve(request: Request):
    body = await _payload(request)
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
async def partial_audit(limit: int = 50, q: str = "", call_id: Optional[int] = None):
    return dash.audit_partial(max(1, min(limit, 200)), q=q, call_id=call_id)


@app.get("/partials/events", response_class=HTMLResponse)
async def partial_events():
    return dash.events_partial()


@app.get("/partials/calls/{call_id}", response_class=HTMLResponse)
async def partial_call_detail(call_id: int):
    found = dash.call_detail_partial(call_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown call")
    return found


@app.get("/partials/handoffs/{handoff_id}", response_class=HTMLResponse)
async def partial_handoff_detail(handoff_id: int):
    found = dash.handoff_detail_partial(handoff_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown handoff")
    return found


@app.post("/partials/links", response_class=HTMLResponse)
async def partial_link(request: Request):
    import os
    body = await _payload(request)
    customer_id = body.get("customer_id", "")
    channel = body.get("channel", "console")
    call_id = body.get("call_id") or None
    try:
        call_id = int(call_id) if call_id else None
    except (TypeError, ValueError):
        call_id = None
    link = tools.create_payment_link(customer_id, body.get("kind", "pay_now"), channel,
                                     body.get("base_url") or None, int(body.get("ttl", 10)),
                                     call_id=call_id)
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
