"""HTMX partials for web/dashboard.html. Phones masked, values escaped."""
import html
import json as jsonlib

from app import channels, tools
from app.db import get_conn, mask_phone


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def queue_partial(mode="expected_value"):
    queue = tools.masked_queue(mode)
    body = "".join(
        f"<tr class='border-t border-neutral-100'>"
        f"<td class='py-1.5 pr-3 font-mono'>{esc(c['customer_id'])}</td>"
        f"<td class='py-1.5 pr-3'>{esc(c.get('name', ''))}</td>"
        f"<td class='py-1.5 pr-3 font-mono'>{c['p_pay']}</td>"
        f"<td class='py-1.5 pr-3'>{esc(c['tier'])}</td>"
        f"<td class='py-1.5 pr-3 font-mono'>{c['priority']}</td>"
        f"<td class='py-1.5 pr-3 font-mono'>{esc(c.get('phone_masked', ''))}</td>"
        f"<td class='py-1.5'><button hx-post='/partials/queue/start'"
        f" hx-vals='{jsonlib.dumps({'customer_id': c['customer_id']})}'"
        f" hx-target='#console-msg' hx-swap='innerHTML'"
        f" class='rounded-full bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground'>Start</button></td></tr>"
        for c in queue["eligible"]
    ) or "<tr><td class='py-2 text-neutral-500'>queue empty</td></tr>"
    return (f"<table class='w-full text-left text-sm'><thead><tr class='text-neutral-500'>"
            f"<th class='pr-3 font-medium'>ID</th><th class='pr-3 font-medium'>Name</th>"
            f"<th class='pr-3 font-medium'>p_pay</th><th class='pr-3 font-medium'>Tier</th>"
            f"<th class='pr-3 font-medium'>Priority</th><th class='pr-3 font-medium'>Phone</th><th></th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


def customers_partial(status="all"):
    conn = get_conn()
    try:
        query = ("select customer_id, name, phone, amount_due, payment_status, attempts_total from customers")
        params: tuple = ()
        if status != "all":
            query += " where payment_status = ?"
            params = (status,)
        body = "".join(
            f"<tr class='border-t border-neutral-100'>"
            f"<td class='py-1.5 pr-3 font-mono'>{esc(r['customer_id'])}</td>"
            f"<td class='py-1.5 pr-3'>{esc(r['name'])}</td>"
            f"<td class='py-1.5 pr-3 font-mono'>{esc(mask_phone(r['phone']))}</td>"
            f"<td class='py-1.5 pr-3 font-mono'>{r['amount_due']}</td>"
            f"<td class='py-1.5'>{esc(r['payment_status'])}</td></tr>"
            for r in conn.execute(query + " order by customer_id", params).fetchall()
        ) or "<tr><td class='py-2 text-neutral-500'>none</td></tr>"
        return f"<table class='w-full text-left text-sm'><tbody>{body}</tbody></table>"
    finally:
        conn.close()


def calls_partial():
    conn = get_conn()
    try:
        body = "".join(
            f"<tr class='border-t border-neutral-100'>"
            f"<td class='py-1.5 pr-3 font-mono'>#{r['call_id']}</td>"
            f"<td class='py-1.5 pr-3 font-mono'>{esc(r['customer_id'])}</td>"
            f"<td class='py-1.5 pr-3'>{esc(r['tier'] or '?')}</td>"
            f"<td class='py-1.5 pr-3'>{'yes' if r['verified'] else 'no'}</td>"
            f"<td class='py-1.5 pr-3'>{esc(r['outcome'] or 'open')}</td>"
            f"<td class='py-1.5'><button hx-post='/partials/calls/cut' hx-vals='{jsonlib.dumps({'call_id': r['call_id']})}'"
            f" hx-target='#console-msg' hx-swap='innerHTML'"
            f" class='rounded-full border border-neutral-200 px-3 py-1 text-xs'>Cut</button> "
            f"<button hx-post='/partials/calls/join' hx-vals='{jsonlib.dumps({'call_id': r['call_id']})}'"
            f" hx-target='#console-msg' hx-swap='innerHTML'"
            f" class='rounded-full border border-neutral-200 px-3 py-1 text-xs'>Join</button></td></tr>"
            for r in conn.execute(
                "select call_id, customer_id, tier, verified, outcome from calls"
                " order by call_id desc limit 30").fetchall()
        ) or "<tr><td class='py-2 text-neutral-500'>no calls yet</td></tr>"
        return f"<table class='w-full text-left text-sm'><tbody>{body}</tbody></table>"
    finally:
        conn.close()


def handoffs_partial(only_open=True):
    conn = get_conn()
    try:
        query = "select handoff_id, customer_id, reason, status, created_at from handoffs"
        if only_open:
            query += " where status = 'open'"
        body = "".join(
            f"<tr class='border-t border-neutral-100'>"
            f"<td class='py-1.5 pr-3 font-mono'>#{r['handoff_id']}</td>"
            f"<td class='py-1.5 pr-3 font-mono'>{esc(r['customer_id'])}</td>"
            f"<td class='py-1.5 pr-3'>{esc(r['reason'])}</td>"
            f"<td class='py-1.5 pr-3'>{esc(r['status'])}</td>"
            f"<td class='py-1.5'><button hx-post='/partials/handoffs/resolve'"
            f" hx-vals='{jsonlib.dumps({'handoff_id': r['handoff_id']})}'"
            f" hx-target='#console-msg' hx-swap='innerHTML'"
            f" class='rounded-full border border-neutral-200 px-3 py-1 text-xs'>Resolve</button></td></tr>"
            for r in conn.execute(query + " order by handoff_id desc").fetchall()
        ) or "<tr><td class='py-2 text-neutral-500'>none</td></tr>"
        return f"<table class='w-full text-left text-sm'><tbody>{body}</tbody></table>"
    finally:
        conn.close()


def audit_partial(limit=50):
    conn = get_conn()
    try:
        body = "".join(
            f"<tr class='border-t border-neutral-100 font-mono text-xs'>"
            f"<td class='py-1 pr-3'>{esc(r['ts'][11:19] if r['ts'] else '')}</td>"
            f"<td class='py-1 pr-3'>{esc(r['actor'])}/{esc(r['tool'])}</td>"
            f"<td class='py-1 pr-3'>{esc(r['args_masked'] or '')}</td>"
            f"<td class='py-1'>{esc(r['status'])}</td></tr>"
            for r in conn.execute("select ts, actor, tool, args_masked, status from audit_log"
                                  " order by id desc limit ?", (limit,)).fetchall()
        ) or "<tr><td class='py-2 text-neutral-500'>empty</td></tr>"
        return f"<table class='w-full text-left'><tbody>{body}</tbody></table>"
    finally:
        conn.close()


def events_partial():
    conn = get_conn()
    try:
        calls = [f"#{r['call_id']} {r['customer_id']} → {r['outcome'] or 'open'}"
                 for r in conn.execute("select call_id, customer_id, outcome from calls"
                                       " order by call_id desc limit 8").fetchall()]
        notes = [f"{r['ts'][11:19]} {r['actor']}/{r['tool']} {r['status']}" if r['ts'] else ""
                 for r in conn.execute("select ts, actor, tool, status from audit_log"
                                       " order by id desc limit 8").fetchall()]
        items = "".join(f"<li class='font-mono text-xs text-neutral-600'>{esc(line)}</li>"
                        for line in calls + notes)
        return f"<ul class='space-y-1'>{items or '<li class=text-neutral-400>quiet</li>'}</ul>"
    finally:
        conn.close()


def message(text, good=True):
    color = "border-green-200 bg-green-50 text-green-700" if good else "border-red-200 bg-red-50 text-red-700"
    return f"<p class='rounded-lg border {color} px-3 py-2 text-sm'>{esc(text)}</p>"
