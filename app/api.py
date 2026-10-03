"""FastAPI app: payment page + static assets, both served from web/."""
import json
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.db import get_conn, utcnow

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
    conn = get_conn()
    link = conn.execute(
        "select token, customer_id, expires_at, used_at from payment_links where token = ?",
        (body.token,),
    ).fetchone()
    if link is None:
        conn.close()
        raise HTTPException(status_code=404, detail="unknown token")
    now = utcnow()
    if link["used_at"] is not None:
        conn.close()
        raise HTTPException(status_code=409, detail="link already used")
    if link["expires_at"] <= now:
        conn.close()
        raise HTTPException(status_code=410, detail="link expired")
    status = "recovered" if body.outcome == "paid" else "failed"
    if body.outcome == "paid":
        conn.execute("update payment_links set used_at = ? where token = ?", (now, body.token))
        conn.execute("update customers set payment_status = 'recovered' where customer_id = ?",
                     (link["customer_id"],))
    conn.execute(
        "insert into audit_log (ts, actor, tool, args_masked, status) values (?,?,?,?,?)",
        (now, "pay-page", "pay_result",
         json.dumps({"token": body.token[:4] + "…", "outcome": body.outcome, "method": body.method}),
         "ok"),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "outcome": body.outcome, "payment_status": status}


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
