import json
import os
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.db import get_conn, utcnow
from app import agent as agent_rules
from app import channels, dash, provider, tools
from app.config import (CONSOLE_PORT, MAX_LINK_TTL, MIN_LINK_TTL, PAY_PORT,
                        VALID_LINK_CHANNELS, VALID_LINK_KINDS, console_base)
from app.utils import mask_phone

web_dir = Path(__file__).resolve().parent.parent / "web"

# Console server (:8000): merchant console, HTMX partials, Vapi webhooks.
app = FastAPI(title="autopay_voice console (synthetic demo)")

# Pay server (:8800): customer payment page only. Same SQLite underneath.
pay_app = FastAPI(title="autopay_voice pay (synthetic demo)")

app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")
pay_app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="pay-assets")
templates = Jinja2Templates(directory=web_dir)


@pay_app.get("/")
async def pay_root():
    return RedirectResponse(url=console_base() + "/console")


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


def _partial(request: Request, name: str, context: dict):
    """Render a Jinja component from web/partials/. Data comes from app/dash."""
    return templates.TemplateResponse(request=request, name=f"partials/{name}", context=context)


def _msg(request: Request, text: str, good: bool = True):
    return _partial(request, "message.html", {"text": text, "good": good})


class pay_result_in(BaseModel):
    token: str
    outcome: Literal["paid", "failed"]
    method: Optional[Literal["card", "upi"]] = None
    ref: Optional[str] = None


@pay_app.post("/pay/result")
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


@app.post("/vapi/tool")
async def vapi_tool(request: Request):
    """In-call tool dispatch. Binds provider call id -> internal call;
    the model never supplies a customer id. Responds in Vapi's results
    envelope (``{"results": [{"toolCallId", "result"|"error"}]}``), always
    HTTP 200, so the voice loop keeps talking instead of dropping."""
    body = await request.json()
    items = provider.tool_calls_of(body)
    if not items:
        return {"results": []}
    conn = get_conn()
    try:
        vapi_id = provider.call_id_of(body)
        row = conn.execute("select call_id from calls where vapi_call_id = ?", (vapi_id,)).fetchone() \
            if vapi_id else None
        results = []
        for item in items:
            tid = provider.tool_call_id_of(item)
            name, args = provider.tool_name_args(item)
            if row is None:
                results.append(provider.tool_error(
                    tid, "unknown call; only the generic message may be spoken"))
                continue
            call_id = row["call_id"]
            try:
                if name == "verify_identity":
                    payload = tools.verify_identity(call_id, args.get("answer"), conn)
                elif name == "get_failed_payment":
                    payload = tools.get_failed_payment(call_id, conn)
                elif name == "send_payment_link":
                    payload = tools.send_payment_link(call_id, args.get("channel", "console"), conn)
                elif name == "schedule_retry":
                    payload = tools.schedule_retry(call_id, args.get("when"), conn)
                elif name == "request_human_handoff":
                    payload = tools.request_human_handoff(call_id, args.get("reason", "asked_for_human"),
                                                          args.get("notes", ""), conn)
                elif name == "log_outcome":
                    payload = tools.log_outcome(call_id, args.get("result", "failed"),
                                                args.get("notes", ""), conn)
                else:
                    results.append(provider.tool_error(tid, f"unknown tool {name!r}"))
                    continue
            except ValueError as exc:
                results.append(provider.tool_error(tid, str(exc)))
                continue
            results.append(provider.tool_result(tid, payload))
        return {"results": results}
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
        vapi_id = provider.call_id_of(body)
        row = conn.execute("select call_id, outcome from calls where vapi_call_id = ?",
                           (vapi_id,)).fetchone() if vapi_id else None
        if row is None:
            return {"ok": False, "error": "unknown call"}
        transcript = provider.report_transcript(message)
        verdict = agent_rules.judge_scan(transcript)
        ended = (message.get("endedReason") or "").lower()
        if "no-answer" in ended or "did-not-answer" in ended or "did_not_answer" in ended \
                or "voicemail" in ended:
            mapped = "no_answer"
        elif row["outcome"] is None:
            mapped = "failed"  # never leave a finished call open
        else:
            mapped = None
        conn.execute("update calls set transcript = ?, judge_json = ?, ended_at = ?,"
                     " outcome = coalesce(outcome, ?) where call_id = ?",
                     (transcript, json.dumps(verdict), utcnow(), mapped, row["call_id"]))
        conn.commit()
        return {"ok": True, "judge": verdict}
    finally:
        conn.close()


@pay_app.get("/pay/test")
async def pay_test(customer_id: str = "CUST001"):
    """Test-only backdoor: mint a fresh link and redirect to the pay page.

    Gated by ALLOW_TEST_ROUTES=1, 404 otherwise. Never enabled in prod.
    Exists so QA/dev can open a live pay page without driving voice."""
    if os.environ.get("ALLOW_TEST_ROUTES", "") != "1":
        raise HTTPException(status_code=404, detail="not found")
    conn = get_conn()
    try:
        person = conn.execute("select customer_id from customers where customer_id = ?",
                              (customer_id,)).fetchone()
    finally:
        conn.close()
    if person is None:
        raise HTTPException(status_code=404, detail="unknown customer")
    link = tools.create_payment_link(customer_id, "pay_now", "console", 10,
                                     None, reuse_live=False)
    return RedirectResponse(url=link["url"], status_code=303)


@pay_app.get("/pay/{token}", response_class=HTMLResponse)
async def pay_page(request: Request, token: str):
    # Real link lookup: unknown -> 404, used/expired -> dismissed render
    # (no amount/phone on dead links). currency drives Jinja symbol (INR -> ₹).
    from app.tools import get_link_context
    found = get_link_context(token)
    if found["status"] == "unknown":
        raise HTTPException(status_code=404, detail="unknown token")
    if found["status"] != "ok":
        code = 409 if found["status"] == "used" else 410
        word = "already used" if found["status"] == "used" else "expired"
        return HTMLResponse(
            status_code=code,
            content=(f"<!doctype html><html><head><meta charset='UTF-8'>"
                     f"<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
                     f"<title>Link {word} | AutoPay</title>"
                     f"<script src='https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4'></script>"
                     f"</head><body style='font-family:sans-serif;background:#fafafa'>"
                     f"<main style='max-width:440px;margin:12vh auto;text-align:center;"
                     f"background:#fff;border:1px solid #e5e5e5;border-radius:16px;padding:32px 24px'>"
                     f"<p style='display:inline-block;font-size:11px;font-weight:700;color:#2b0a49;"
                     f"background:#e4c5ff;border-radius:999px;padding:4px 12px'>AUTOPAY DEMO</p>"
                     f"<h1 style='margin:12px 0 8px'>Link {word}</h1>"
                     f"<p style='color:#525252;font-size:14px'>This payment link is {word}. "
                     f"Please request a fresh link from the merchant.</p>"
                     f"<a href='/console' style='display:inline-block;margin-top:16px;background:#e4c5ff;"
                     f"color:#2b0a49;font-weight:600;border-radius:999px;padding:8px 20px;"
                     f"text-decoration:none'>Back to Merchant Console</a></main></body></html>"),
        )
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

@app.get("/")
async def root_page():
    return RedirectResponse(url="/console")

@app.get("/console", response_class=HTMLResponse)
async def console_page(request: Request):
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
    queue = campaign_tools.masked_queue()
    queue_count = len(queue["eligible"])
    eligible_ids = {c["customer_id"] for c in queue["eligible"]}
    reason_map = {c["customer_id"]: c.get("reason", "") for c in queue.get("ineligible", [])}
    customers = [{
        "customer_id": c["customer_id"],
        "name": c["name"],
        "eligible": c["customer_id"] in eligible_ids,
        "reason": reason_map.get(c["customer_id"], ""),
    } for c in customers]
    customers.sort(key=lambda c: (not c["eligible"], c["customer_id"]))
    dest_hint = mask_phone(provider.test_destination("")) if provider.test_destination("") else ""
    return templates.TemplateResponse(
        request=request, name="console.html",
        context={"customers": customers, "funnel": funnel,
                 "recovery_rate": round(100 * funnel.get("recovered", 0) / total),
                 "call_count": calls, "open_handoffs": open_handoffs,
                 "queue_count": queue_count, "failed_count": funnel.get("failed", 0),
                 "at_risk": f"{at_risk:,.0f}", "open_calls": open_calls,
                 "dest_hint": dest_hint})


@app.get("/partials/queue", response_class=HTMLResponse)
async def partial_queue(request: Request, mode: str = "expected_value", q: str = "", tier: str = "all"):
    return _partial(request, "queue.html", dash.queue_data(
        mode if mode in ("expected_value", "easiest", "highest") else "expected_value",
        q=q, tier=tier))


@app.get("/partials/active-call", response_class=HTMLResponse)
async def partial_active_call(request: Request):
    return _partial(request, "active_call.html", {"call": dash.active_call_data()})


@app.post("/partials/queue/start", response_class=HTMLResponse)
async def partial_start(request: Request):
    """Single Start Call button. mode=web opens the internal call row and,
    when VAPI_API_KEY is set, a provider web call (URL returned for join);
    mode=phone additionally dials via Vapi (confirm required)."""
    from app import agent as agent_pkg
    body = await _payload(request)
    mode = (body.get("mode") or "web").strip().lower()
    if mode not in ("web", "phone"):
        mode = "web"
    try:
        started = tools.start_call(body.get("customer_id", ""), mode="web", source="dashboard")
    except ValueError as exc:
        return _msg(request, str(exc), good=False)
    conn = get_conn()
    try:
        row = conn.execute("select * from customers where customer_id = ?",
                           (started["customer_id"],)).fetchone()
        prompt = agent_pkg.assemble_prompt(row, started["tier"], False, started["reasons"])
    finally:
        conn.close()

    def _bind_vapi(call_id, vapi_id):
        if not vapi_id:
            return
        conn = get_conn()
        try:
            conn.execute("update calls set vapi_call_id = ? where call_id = ?", (vapi_id, call_id))
            conn.commit()
        finally:
            conn.close()

    if mode == "phone":
        if str(body.get("confirm", "")).lower() not in ("true", "1", "on", "yes"):
            return _msg(request, "phone mode needs the confirm checkbox (only call numbers you control)",
                        good=False)
        try:
            placed = provider.start_phone_call(prompt, console_base(),
                                               body.get("to_number", ""),
                                               confirm=True)
            _bind_vapi(started["call_id"], placed.get("id", ""))
            dest = provider.test_destination(body.get("to_number", ""))
            return _msg(request, f"call {started['call_id']} dialing {mask_phone(dest)}")
        except Exception as exc:  # provider errors stay in-app, row stays open
            return _msg(request, f"call {started['call_id']} open but dial failed: {exc}", good=False)
    try:
        placed = provider.start_web_call(prompt, console_base())
    except Exception:  # no key / offline: internal row only, same as before
        return _msg(request, f"call {started['call_id']} open ({started['tier']}, p={started['p_pay']})")
    _bind_vapi(started["call_id"], placed.get("id", ""))
    join_url = placed.get("webCallUrl") or placed.get("web_call_url") or ""
    suffix = f" — join {join_url}" if join_url else ""
    return _msg(request, f"call {started['call_id']} open ({started['tier']}, p={started['p_pay']}){suffix}")


@app.post("/partials/calls/stop-active", response_class=HTMLResponse)
async def partial_stop_active(request: Request):
    """Stop Call button: closes the latest open call as no_answer."""
    conn = get_conn()
    try:
        row = conn.execute("select call_id from calls where outcome is null"
                           " order by call_id desc limit 1").fetchone()
    finally:
        conn.close()
    if row is None:
        return _msg(request, "no live call to stop")
    try:
        tools.log_outcome(int(row["call_id"]), "no_answer", "stopped from console")
    except (TypeError, ValueError) as exc:
        return _msg(request, str(exc), good=False)
    return _msg(request, f"call {row['call_id']} stopped")


@app.get("/partials/customers", response_class=HTMLResponse)
async def partial_customers(request: Request, status: str = "all", q: str = ""):
    return _partial(request, "customers.html", dash.customers_data(status, q))


@app.get("/partials/customers/{customer_id}", response_class=HTMLResponse)
async def partial_customer_detail(request: Request, customer_id: str):
    found = dash.customer_detail_data(customer_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown customer")
    return _partial(request, "customer_detail.html", found)


@app.get("/partials/calls", response_class=HTMLResponse)
async def partial_calls(request: Request, q: str = "", outcome: str = "all"):
    return _partial(request, "calls.html", dash.calls_data(q=q, outcome=outcome))


@app.post("/partials/calls/outcome", response_class=HTMLResponse)
async def partial_outcome(request: Request):
    body = await _payload(request)
    try:
        tools.log_outcome(int(body.get("call_id", 0)), body.get("outcome", "failed"),
                          str(body.get("notes", ""))[:200])
    except (TypeError, ValueError) as exc:
        return _msg(request, str(exc), good=False)
    return _msg(request, f"call {body.get('call_id')} → {body.get('outcome')}")


@app.post("/partials/calls/cut", response_class=HTMLResponse)
async def partial_cut(request: Request):
    body = await _payload(request)
    try:
        tools.log_outcome(int(body.get("call_id", 0)), "no_answer", "cut from console")
    except (TypeError, ValueError) as exc:
        return _msg(request, str(exc), good=False)
    return _msg(request, f"call {body.get('call_id')} cut")


@app.post("/partials/calls/join", response_class=HTMLResponse)
async def partial_join(request: Request):
    body = await _payload(request)
    try:
        result = tools.request_human_handoff(int(body.get("call_id", 0)), "human_joined", "merchant joined")
    except (TypeError, ValueError) as exc:
        return _msg(request, str(exc), good=False)
    return _msg(request, f"joined — handoff {result['handoff_id']} open")


@app.get("/partials/handoffs", response_class=HTMLResponse)
async def partial_handoffs(request: Request, open: int = 1, q: str = ""):
    return _partial(request, "handoffs.html", dash.handoffs_data(bool(open), q))


@app.post("/partials/handoffs/create", response_class=HTMLResponse)
async def partial_handoff_create(request: Request):
    body = await _payload(request)
    try:
        result = tools.request_human_handoff(int(body.get("call_id", 0)),
                                             body.get("reason", "asked_for_human"),
                                             str(body.get("notes", ""))[:500])
    except (TypeError, ValueError) as exc:
        return _msg(request, str(exc), good=False)
    return _msg(request, f"handoff {result['handoff_id']} open for call {body.get('call_id')}")


@app.post("/partials/handoffs/resolve", response_class=HTMLResponse)
async def partial_resolve(request: Request):
    body = await _payload(request)
    try:
        handoff_id = int(body.get("handoff_id", 0))
    except (TypeError, ValueError) as exc:
        return _msg(request, str(exc), good=False)
    conn = get_conn()
    try:
        conn.execute("update handoffs set status = 'done', resolved_at = ? where handoff_id = ?",
                     (utcnow(), handoff_id))
        tools.log_audit(conn, "merchant", "resolve_handoff",
                        f"handoff_id={body.get('handoff_id')}", "ok")
        conn.commit()
    finally:
        conn.close()
    return _msg(request, f"handoff {body.get('handoff_id')} resolved")


@app.get("/partials/audit", response_class=HTMLResponse)
async def partial_audit(request: Request, limit: int = 50, q: str = "", call_id: Optional[int] = None):
    return _partial(request, "audit.html", dash.audit_data(max(1, min(limit, 200)), q=q, call_id=call_id))


@app.get("/partials/events", response_class=HTMLResponse)
async def partial_events(request: Request):
    return _partial(request, "events.html", dash.events_data())


@app.get("/partials/calls/{call_id}", response_class=HTMLResponse)
async def partial_call_detail(request: Request, call_id: int):
    found = dash.call_detail_data(call_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown call")
    return _partial(request, "call_detail.html", found)


@app.get("/partials/handoffs/{handoff_id}", response_class=HTMLResponse)
async def partial_handoff_detail(request: Request, handoff_id: int):
    found = dash.handoff_detail_data(handoff_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown handoff")
    return _partial(request, "handoff_detail.html", found)


@app.post("/partials/links", response_class=HTMLResponse)
async def partial_link(request: Request):
    body = await _payload(request)
    customer_id = (body.get("customer_id") or "").strip()
    channel = body.get("channel", "console")
    call_id = body.get("call_id") or None
    try:
        call_id = int(call_id) if call_id else None
    except (TypeError, ValueError):
        call_id = None
    try:
        ttl = int(body.get("ttl", 10))
    except (TypeError, ValueError):
        return _msg(request, "bad ttl (1-1440 required)", good=False)
    if channel not in VALID_LINK_CHANNELS:
        return _msg(request, f"unknown channel {channel!r}", good=False)
    if body.get("kind", "pay_now") not in VALID_LINK_KINDS:
        return _msg(request, f"unknown kind {body.get('kind')!r}", good=False)
    if not MIN_LINK_TTL <= ttl <= MAX_LINK_TTL:
        return _msg(request, "bad ttl (1-1440 required)", good=False)
    conn = get_conn()
    try:
        person = conn.execute("select name, phone, amount_due from customers where customer_id = ?",
                              (customer_id,)).fetchone()
    finally:
        conn.close()
    if person is None:
        raise HTTPException(status_code=404, detail="unknown customer")
    try:
        link = tools.create_payment_link(customer_id, body.get("kind", "pay_now"), channel,
                                         ttl, call_id=call_id)
    except ValueError as exc:
        return _msg(request, str(exc), good=False)
    text = channels.render(channel, person["name"], person["amount_due"], link["url"])
    receipt = channels.send(channel, mask_phone(person["phone"]), text)
    return _partial(request, "link_result.html", {
        "url": link["url"], "expires_at": link["expires_at"],
        "channel": receipt["channel"], "status": receipt["status"]})
