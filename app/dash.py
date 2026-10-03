import html
import json as jsonlib

from app import channels, tools
from app.db import get_conn, mask_phone

tier_pill = {
    "short": "bg-green-100 text-green-800",
    "standard": "bg-amber-100 text-amber-800",
    "extended": "bg-blue-100 text-blue-800",
}

status_pill = {
    "failed": "bg-red-100 text-red-800",
    "link_sent": "bg-amber-100 text-amber-800",
    "recovered": "bg-green-100 text-green-800",
    "handoff": "bg-blue-100 text-blue-800",
    "opted_out": "bg-neutral-200 text-neutral-500",
}

outcome_pill = {
    "open": "bg-primary text-primary-foreground",
    "link_sent": "bg-amber-100 text-amber-800",
    "retry_scheduled": "bg-blue-100 text-blue-800",
    "handoff": "bg-blue-100 text-blue-800",
    "no_answer": "bg-neutral-200 text-neutral-600",
    "wrong_person": "bg-neutral-200 text-neutral-600",
    "refused": "bg-red-100 text-red-800",
    "opted_out": "bg-neutral-200 text-neutral-500",
    "failed": "bg-red-100 text-red-800",
}

btn_primary = ("rounded-full bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground "
               "hover:bg-primary-foreground hover:text-primary transition-colors disabled:opacity-40")
btn_ghost = ("rounded-full border border-neutral-200 bg-white px-3 py-1 text-xs font-medium text-neutral-600 "
             "hover:border-neutral-300 hover:text-neutral-900 transition-colors")
btn_danger = ("rounded-full border border-red-200 bg-white px-3 py-1 text-xs font-medium text-red-600 "
              "hover:bg-red-50 transition-colors")
inp = "rounded-lg border border-neutral-200 bg-neutral-50 px-2 py-1.5 text-xs w-full"
lbl = "text-[10px] font-semibold uppercase tracking-wider text-neutral-400"


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def score_bar(prob):
    pct = max(4, min(100, int(float(prob) * 100)))
    return (f"<span class='inline-flex items-center gap-1.5'>"
            f"<span class='inline-block h-1.5 w-14 overflow-hidden rounded-full bg-neutral-100'>"
            f"<span class='block h-1.5 rounded-full bg-primary-foreground' style='width:{pct}%'></span></span>"
            f"<span class='font-mono text-xs'>{prob}</span></span>")


def empty_row(text):
    return (f"<tr><td class='py-8 text-center text-sm text-neutral-400'>"
            f"<span class='mx-auto mb-1 block h-8 w-8 rounded-full bg-neutral-100'></span>{esc(text)}</td></tr>")


def _match(hay, needle):
    return not needle or needle.lower() in (hay or "").lower()


def queue_partial(mode="expected_value", q="", tier="all"):
    queue = tools.masked_queue(mode)
    q = (q or "").strip().lower()
    rows = [c for c in queue["eligible"]
            if (tier in ("all", "", None) or c["tier"] == tier)
            and (not q or q in c["customer_id"].lower() or q in (c.get("name") or "").lower())]
    body = "".join(
        f"<tr class='border-t border-neutral-100 transition-colors hover:bg-[#faf8fd]' data-cid='{esc(c['customer_id'])}'>"
        f"<td class='py-2 pl-1 pr-1'><input type='checkbox' class='qpick h-3.5 w-3.5 accent-primary-foreground' "
        f"data-cid='{esc(c['customer_id'])}' aria-label='add {esc(c['customer_id'])} to dial queue' /></td>"
        f"<td class='py-2 pr-2 font-mono text-[12px] font-medium'>{esc(c['customer_id'])}</td>"
        f"<td class='py-2 pr-2 font-medium text-[13px]'>{esc(c.get('name', ''))}</td>"
        f"<td class='py-2 pr-2'>{score_bar(c['p_pay'])}</td>"
        f"<td class='py-2 pr-2'><span class='rounded-full px-2 py-0.5 text-[11px] font-semibold "
        f"{tier_pill.get(c['tier'], 'bg-neutral-100 text-neutral-600')}'>{esc(c['tier'])}</span></td>"
        f"<td class='py-2 pr-2 font-mono tabular-nums text-[13px]'>₹{c['priority']:,.0f}</td>"
        f"<td class='py-2 pr-2 font-mono text-xs text-neutral-500'>{esc(c.get('phone_masked', ''))}</td>"
        f"<td class='py-2 text-right'><button hx-post='/partials/queue/start'"
        f" hx-vals='{jsonlib.dumps({'customer_id': c['customer_id']})}'"
        f" hx-target='#console-msg' hx-swap='innerHTML'"
        f" class='{btn_primary}'>Start call</button></td></tr>"
        for c in rows
    ) or empty_row("queue empty — everyone is capped, cooling down, or opted out")
    return (f"<table class='w-full text-left text-sm'><thead class='sticky top-0 bg-white'><tr class='text-[10px] uppercase tracking-wider text-neutral-400'>"
            f"<th class='pb-1.5 pl-1 pr-1 font-semibold w-6'><input type='checkbox' id='q-all' class='h-3.5 w-3.5 accent-primary-foreground' title='select all' /></th>"
            f"<th class='pb-1.5 pr-2 font-semibold'>ID</th><th class='pb-1.5 pr-2 font-semibold'>Name</th>"
            f"<th class='pb-1.5 pr-2 font-semibold'>P(pay)</th><th class='pb-1.5 pr-2 font-semibold'>Tier</th>"
            f"<th class='pb-1.5 pr-2 font-semibold'>Exp. value</th><th class='pb-1.5 pr-2 font-semibold'>Phone</th>"
            f"<th class='pb-1.5'></th></tr></thead><tbody>{body}</tbody></table>")


def customers_partial(status="all", q=""):
    conn = get_conn()
    try:
        query = ("select customer_id, name, phone, amount_due, payment_status, attempts_total from customers")
        params: tuple = ()
        if status != "all":
            query += " where payment_status = ?"
            params = (status,)
        needle = (q or "").strip().lower()
        rows = [r for r in conn.execute(query + " order by customer_id", params).fetchall()
                if not needle or needle in r["customer_id"].lower() or needle in (r["name"] or "").lower()]
        body = "".join(
            f"<tr class='border-t border-neutral-100 transition-colors hover:bg-[#faf8fd] cursor-pointer' "
            f"hx-get='/partials/customers/{esc(r['customer_id'])}' hx-target='#modal-body' hx-swap='innerHTML' onclick='openModal()'>"
            f"<td class='py-2 pr-2 font-mono text-[12px] font-medium'>{esc(r['customer_id'])}</td>"
            f"<td class='py-2 pr-2 font-medium text-[13px]'>{esc(r['name'])}</td>"
            f"<td class='py-2 pr-2 font-mono text-xs text-neutral-500'>{esc(mask_phone(r['phone']))}</td>"
            f"<td class='py-2 pr-2 font-mono tabular-nums text-[13px]'>₹{r['amount_due']:,.2f}</td>"
            f"<td class='py-2 pr-2 text-center font-mono text-xs'>{r['attempts_total']}</td>"
            f"<td class='py-2'><span class='rounded-full px-2 py-0.5 text-[11px] font-semibold "
            f"{status_pill.get(r['payment_status'], 'bg-neutral-100 text-neutral-600')}'>{esc(r['payment_status'])}</span></td></tr>"
            for r in rows
        ) or empty_row("no customers in this state")
        return (f"<table class='w-full text-left text-sm'><thead class='sticky top-0 bg-white'><tr class='text-[10px] uppercase tracking-wider text-neutral-400'>"
                f"<th class='pb-1.5 pr-2 font-semibold'>ID</th><th class='pb-1.5 pr-2 font-semibold'>Name</th>"
                f"<th class='pb-1.5 pr-2 font-semibold'>Phone</th><th class='pb-1.5 pr-2 font-semibold'>Due</th>"
                f"<th class='pb-1.5 pr-2 font-semibold'>Tries</th>"
                f"<th class='pb-1.5 font-semibold'>Status</th></tr></thead><tbody>{body}</tbody></table>")
    finally:
        conn.close()


def customer_detail_partial(customer_id):
    conn = get_conn()
    try:
        c = conn.execute("select * from customers where customer_id = ?", (customer_id,)).fetchone()
        if c is None:
            return None
        c = dict(c)
        calls = conn.execute(
            "select call_id, tier, verified, outcome, started_at from calls where customer_id = ?"
            " order by call_id desc limit 10", (customer_id,)).fetchall()
        links = conn.execute(
            "select token, kind, channel, created_at, expires_at, used_at from payment_links"
            " where customer_id = ? order by created_at desc limit 5", (customer_id,)).fetchall()
        hands = conn.execute(
            "select handoff_id, reason, status, created_at from handoffs where customer_id = ?"
            " order by handoff_id desc limit 5", (customer_id,)).fetchall()
        calls_html = "".join(
            f"<li class='flex items-center justify-between gap-2 py-1 text-xs'>"
            f"<span class='font-mono'>#{r['call_id']} · {esc(r['tier'] or '?')} · {'v' if r['verified'] else 'unv'}</span>"
            f"<span class='rounded-full px-2 py-px text-[10px] font-semibold {outcome_pill.get(r['outcome'] or 'open', 'bg-neutral-100 text-neutral-600')}'>"
            f"{esc(r['outcome'] or 'open')}</span></li>" for r in calls) or "<li class='text-xs text-neutral-400'>no calls</li>"
        links_html = "".join(
            f"<li class='py-1 font-mono text-xs text-neutral-600'>/{esc(r['token'])} · {esc(r['kind'])} · {esc(r['channel'])}"
            f" · {'used' if r['used_at'] else 'live'}</li>" for r in links) or "<li class='text-xs text-neutral-400'>no links</li>"
        hands_html = "".join(
            f"<li class='py-1 text-xs text-neutral-600'>#{r['handoff_id']} {esc(r['reason'])} · {esc(r['status'])}</li>"
            for r in hands) or "<li class='text-xs text-neutral-400'>no handoffs</li>"
        return (
            f"<p class='text-base font-bold text-neutral-900'>{esc(c['name'])} "
            f"<span class='font-mono text-xs font-medium text-neutral-400'>{esc(c['customer_id'])}</span></p>"
            f"<p class='mt-0.5 font-mono text-xs text-neutral-500'>{esc(mask_phone(c['phone']))} · ₹{c['amount_due']:,.2f} due {esc(c['due_date'] or '')}</p>"
            f"<div class='mt-2 flex flex-wrap gap-1.5'>"
            f"<span class='rounded-full px-2 py-0.5 text-[11px] font-semibold {status_pill.get(c['payment_status'], 'bg-neutral-100 text-neutral-600')}'>{esc(c['payment_status'])}</span>"
            f"<span class='rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-600'>{esc(c['failure_reason'] or '')}</span>"
            f"<span class='rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-600'>tries {c['attempts_total']} · defaults {c['default_history']}</span>"
            f"</div>"
            f"<div class='mt-3 grid grid-cols-2 gap-2 text-left'>"
            f"<div class='rounded-lg bg-neutral-50 p-2'><p class='{lbl}'>Security Q</p><p class='mt-0.5 text-xs'>{esc(c['security_question'] or '—')}</p></div>"
            f"<div class='rounded-lg bg-neutral-50 p-2'><p class='{lbl}'>Past notes</p><p class='mt-0.5 text-xs'>{esc(c['past_call_notes'] or '—')}</p></div>"
            f"</div>"
            f"<div class='mt-2 grid gap-2'>"
            f"<div><p class='{lbl}'>Calls</p><ul class='mt-0.5 divide-y divide-neutral-100'>{calls_html}</ul></div>"
            f"<div><p class='{lbl}'>Links</p><ul class='mt-0.5'>{links_html}</ul></div>"
            f"<div><p class='{lbl}'>Handoffs</p><ul class='mt-0.5'>{hands_html}</ul></div>"
            f"</div>"
            f"<div class='mt-3 flex gap-2'>"
            f"<button hx-post='/partials/queue/start' hx-vals='{jsonlib.dumps({'customer_id': c['customer_id']})}' "
            f"hx-target='#console-msg' hx-swap='innerHTML' onclick='closeModal()' class='{btn_primary}'>Start call</button>"
            f"<button onclick='closeModal()' class='{btn_ghost}'>Close</button></div>")
    finally:
        conn.close()


def action_buttons(call_id):
    vals = jsonlib.dumps({"call_id": call_id})
    return (f"<button hx-get='/partials/calls/{call_id}'"
            f" hx-target='#modal-body' hx-swap='innerHTML' onclick='openModal()'"
            f" class='{btn_ghost}'>View</button> "
            f"<button hx-post='/partials/calls/cut' hx-vals='{vals}'"
            f" hx-target='#console-msg' hx-swap='innerHTML'"
            f" class='{btn_danger}'>Cut</button> "
            f"<button hx-post='/partials/calls/join' hx-vals='{vals}'"
            f" hx-target='#console-msg' hx-swap='innerHTML'"
            f" class='{btn_ghost}'>Join</button>")


def verified_dot(verified):
    dot = "bg-green-500" if verified else "bg-neutral-300"
    word = "yes" if verified else "no"
    return f"<span class='inline-flex items-center gap-1.5'><span class='h-1.5 w-1.5 rounded-full {dot}'></span>{word}</span>"


def calls_partial(q="", outcome="all"):
    conn = get_conn()
    try:
        needle = (q or "").strip().lower()
        all_rows = conn.execute(
            "select call_id, customer_id, tier, verified, outcome from calls"
            " order by call_id desc limit 100").fetchall()
        rows = [r for r in all_rows
                if (outcome in ("all", "", None) or (r["outcome"] or "open") == outcome)
                and (not needle or needle in str(r["call_id"]) or needle in (r["customer_id"] or "").lower())]
        body = "".join(
            f"<tr class='border-t border-neutral-100 transition-colors hover:bg-[#faf8fd] cursor-pointer' "
            f"hx-get='/partials/calls/{r['call_id']}' hx-target='#modal-body' hx-swap='innerHTML' onclick='openModal()'>"
            f"<td class='py-2 pr-2 font-mono text-[12px] font-medium'>#{r['call_id']}</td>"
            f"<td class='py-2 pr-2 font-mono text-xs'>{esc(r['customer_id'])}</td>"
            f"<td class='py-2 pr-2'><span class='rounded-full px-2 py-0.5 text-[11px] font-semibold "
            f"{tier_pill.get(r['tier'] or '', 'bg-neutral-100 text-neutral-600')}'>{esc(r['tier'] or '?')}</span></td>"
            f"<td class='py-2 pr-2 text-xs'>{verified_dot(r['verified'])}</td>"
            f"<td class='py-2 pr-2'><span class='rounded-full px-2 py-0.5 text-[11px] font-semibold "
            f"{outcome_pill.get(r['outcome'] or 'open', 'bg-neutral-100 text-neutral-600')}'>{esc(r['outcome'] or 'open')}</span></td>"
            f"<td class='py-2 text-right whitespace-nowrap' onclick='event.stopPropagation()'>{action_buttons(r['call_id'])}</td></tr>"
            for r in rows
        ) or empty_row("no calls yet — start one from the queue")
        return (f"<table class='w-full text-left text-sm'><thead class='sticky top-0 bg-white'><tr class='text-[10px] uppercase tracking-wider text-neutral-400'>"
                f"<th class='pb-1.5 pr-2 font-semibold'>Call</th><th class='pb-1.5 pr-2 font-semibold'>Customer</th>"
                f"<th class='pb-1.5 pr-2 font-semibold'>Tier</th><th class='pb-1.5 pr-2 font-semibold'>Verified</th>"
                f"<th class='pb-1.5 pr-2 font-semibold'>Outcome</th><th class='pb-1.5'></th></tr></thead>"
                f"<tbody>{body}</tbody></table>")
    finally:
        conn.close()


def active_call_partial():
    """Singular on-call card: latest open call + link + handoff forms bound to it."""
    conn = get_conn()
    try:
        row = conn.execute(
            "select calls.*, customers.name, customers.phone, customers.amount_due from calls"
            " join customers on customers.customer_id = calls.customer_id"
            " where calls.outcome is null order by call_id desc limit 1").fetchone()
        if row is None:
            return ("<div class='py-6 text-center text-sm text-neutral-400'>"
                    "<span class='mx-auto mb-2 block h-9 w-9 rounded-full bg-neutral-100'></span>"
                    "no live call — pick queue + Start call</div>")
        r = dict(row)
        cid = r["call_id"]
        vals = jsonlib.dumps({"call_id": cid})
        reasons = ""
        if r.get("score_reasons"):
            try:
                reasons = "; ".join(jsonlib.loads(r["score_reasons"]))
            except ValueError:
                reasons = ""
        return (
            f"<div class='flex items-center justify-between gap-2'>"
            f"<p class='text-sm font-bold text-neutral-900'>#{cid} · {esc(r['name'])} "
            f"<span class='font-mono text-xs font-medium text-neutral-400'>{esc(r['customer_id'])}</span></p>"
            f"<span class='rounded-full px-2 py-0.5 text-[11px] font-semibold {tier_pill.get(r['tier'] or '', 'bg-neutral-100 text-neutral-600')}'>{esc(r['tier'] or '?')}</span></div>"
            f"<p class='mt-0.5 font-mono text-xs text-neutral-500'>{esc(mask_phone(r['phone']))} · ₹{r['amount_due']:,.2f} · p={r['p_pay']}</p>"
            f"<p class='mt-1 text-[11px] text-neutral-500'>verified {verified_dot(r['verified'])} · {esc(reasons)}</p>"
            f"<div class='mt-2 flex flex-wrap gap-1.5'>"
            f"<button hx-post='/partials/calls/cut' hx-vals='{vals}' hx-target='#console-msg' hx-swap='innerHTML' class='{btn_danger}'>Cut</button>"
            f"<button hx-post='/partials/calls/join' hx-vals='{vals}' hx-target='#console-msg' hx-swap='innerHTML' class='{btn_ghost}'>Join → handoff</button>"
            f"<button hx-get='/partials/calls/{cid}' hx-target='#modal-body' hx-swap='innerHTML' onclick='openModal()' class='{btn_ghost}'>Inspect</button>"
            f"</div>"
            f"<form hx-post='/partials/calls/outcome' hx-target='#console-msg' hx-swap='innerHTML' class='mt-2 flex gap-1.5'>"
            f"<input type='hidden' name='call_id' value='{cid}' />"
            f"<select name='outcome' class='{inp}'>"
            f"<option value='link_sent'>link_sent</option><option value='retry_scheduled'>retry</option>"
            f"<option value='no_answer'>no_answer</option><option value='wrong_person'>wrong_person</option>"
            f"<option value='refused'>refused</option><option value='opted_out'>opted_out</option>"
            f"<option value='failed'>failed</option></select>"
            f"<button class='{btn_primary} shrink-0'>Log</button></form>"
            f"<div class='mt-2 rounded-lg bg-neutral-50 p-2'>"
            f"<p class='{lbl}'>Link · {esc(r['customer_id'])}</p>"
            f"<form hx-post='/partials/links' hx-target='#link-result' hx-swap='innerHTML' class='mt-1 flex gap-1.5'>"
            f"<input type='hidden' name='customer_id' value='{esc(r['customer_id'])}' />"
            f"<input type='hidden' name='call_id' value='{cid}' />"
            f"<select name='kind' class='{inp}'><option>pay_now</option><option>update_mandate</option></select>"
            f"<select name='channel' class='{inp}'><option>console</option><option>whatsapp</option><option>sms</option></select>"
            f"<input name='ttl' type='hidden' value='10' /><input name='base_url' type='hidden' value='' />"
            f"<button class='{btn_primary} shrink-0'>Send</button></form>"
            f"<div id='link-result' class='mt-1'></div></div>"
            f"<div class='mt-2 rounded-lg bg-neutral-50 p-2'>"
            f"<p class='{lbl}'>Handoff · call #{cid}</p>"
            f"<form hx-post='/partials/handoffs/create' hx-target='#console-msg' hx-swap='innerHTML' class='mt-1 grid gap-1.5'>"
            f"<input type='hidden' name='call_id' value='{cid}' />"
            f"<select name='reason' class='{inp}'>"
            f"<option value='asked_for_human'>asked_for_human</option><option value='dispute'>dispute</option>"
            f"<option value='hardship'>hardship</option><option value='human_joined'>human_joined</option>"
            f"<option value='other'>other</option></select>"
            f"<input name='notes' placeholder='notes for human…' class='{inp}' />"
            f"<button class='{btn_ghost} w-full'>Open handoff</button></form></div>")
    finally:
        conn.close()


def handoffs_partial(only_open=True, q=""):
    conn = get_conn()
    try:
        query = "select handoff_id, customer_id, call_id, reason, status, created_at from handoffs"
        if only_open:
            query += " where status = 'open'"
        needle = (q or "").strip().lower()
        rows = [r for r in conn.execute(query + " order by handoff_id desc").fetchall()
                if not needle or needle in (r["customer_id"] or "").lower() or needle in (r["reason"] or "").lower()]
        body = "".join(
            f"<tr class='border-t border-neutral-100 transition-colors hover:bg-[#faf8fd] cursor-pointer' "
            f"hx-get='/partials/handoffs/{r['handoff_id']}' hx-target='#modal-body' hx-swap='innerHTML' onclick='openModal()'>"
            f"<td class='py-2 pr-2 font-mono text-[12px] font-medium'>#{r['handoff_id']}</td>"
            f"<td class='py-2 pr-2 font-mono text-xs'>{esc(r['customer_id'])}</td>"
            f"<td class='py-2 pr-2 text-[13px]'>{esc(r['reason'])}</td>"
            f"<td class='py-2 pr-2'><span class='rounded-full px-2 py-0.5 text-[11px] font-semibold "
            f"{'bg-amber-100 text-amber-800' if r['status'] == 'open' else 'bg-neutral-200 text-neutral-500'}'>{esc(r['status'])}</span></td>"
            f"<td class='py-2 text-right whitespace-nowrap' onclick='event.stopPropagation()'>"
            f"<button hx-get='/partials/handoffs/{r['handoff_id']}'"
            f" hx-target='#modal-body' hx-swap='innerHTML' onclick='openModal()'"
            f" class='{btn_ghost}'>View</button> "
            f"<button hx-post='/partials/handoffs/resolve'"
            f" hx-vals='{jsonlib.dumps({'handoff_id': r['handoff_id']})}'"
            f" hx-target='#console-msg' hx-swap='innerHTML'"
            f" class='{btn_ghost}'>Resolve</button></td></tr>"
            for r in rows
        ) or empty_row("no handoffs — quiet floor")
        return (f"<table class='w-full text-left text-sm'><thead class='sticky top-0 bg-white'><tr class='text-[10px] uppercase tracking-wider text-neutral-400'>"
                f"<th class='pb-1.5 pr-2 font-semibold'>ID</th><th class='pb-1.5 pr-2 font-semibold'>Customer</th>"
                f"<th class='pb-1.5 pr-2 font-semibold'>Reason</th><th class='pb-1.5 pr-2 font-semibold'>Status</th>"
                f"<th class='pb-1.5'></th></tr></thead><tbody>{body}</tbody></table>")
    finally:
        conn.close()


def audit_partial(limit=50, q="", call_id=None):
    conn = get_conn()
    try:
        needle = (q or "").strip().lower()
        if call_id is not None:
            rows = conn.execute("select ts, actor, tool, args_masked, status, call_id from audit_log"
                                " where call_id = ? order by id desc limit ?", (call_id, limit)).fetchall()
        else:
            rows = conn.execute("select ts, actor, tool, args_masked, status, call_id from audit_log"
                                " order by id desc limit ?", (limit,)).fetchall()
            if needle:
                rows = [r for r in rows if needle in (r["tool"] or "").lower()
                        or needle in (r["actor"] or "").lower()
                        or needle in (r["args_masked"] or "").lower()]
        body = "".join(
            f"<tr class='border-t border-neutral-100 font-mono text-xs'>"
            f"<td class='py-1.5 pr-2 text-neutral-400'>{esc(r['ts'][11:19] if r['ts'] else '')}</td>"
            f"<td class='py-1.5 pr-2'><span class='rounded bg-neutral-100 px-1.5 py-0.5'>{esc(r['actor'])}</span> "
            f"<span class='font-semibold'>{esc(r['tool'])}</span>"
            f"{(' · #' + str(r['call_id'])) if r['call_id'] else ''}</td>"
            f"<td class='py-1.5 pr-2 text-neutral-500 break-all'>{esc(r['args_masked'] or '')}</td>"
            f"<td class='py-1.5 text-right'>{esc(r['status'])}</td></tr>"
            for r in rows
        ) or empty_row("no audit entries yet")
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
        items = "".join(f"<li class='flex gap-2 font-mono text-xs text-neutral-600'>"
                        f"<span class='mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary-foreground'></span>"
                        f"{esc(line)}</li>"
                        for line in calls + notes)
        return f"<ul class='space-y-1.5'>{items or '<li class=text-neutral-400>quiet</li>'}</ul>"
    finally:
        conn.close()


def call_detail_partial(call_id):
    import json as jsonlib2
    conn = get_conn()
    try:
        call = conn.execute("select calls.*, customers.name from calls"
                            " join customers on customers.customer_id = calls.customer_id"
                            " where call_id = ?", (call_id,)).fetchone()
        if call is None:
            return None
        call = dict(call)
        reasons = ""
        if call["score_reasons"]:
            try:
                reasons = "; ".join(jsonlib2.loads(call["score_reasons"]))
            except ValueError:
                reasons = ""
        transcript = call.get("transcript") or ""
        words = len(transcript.split()) if transcript else 0
        judge = ""
        if call.get("judge_json"):
            try:
                verdict = jsonlib2.loads(call["judge_json"])
                flags = verdict.get("flags") or verdict.get("violations") or []
                judge = f"judge: {esc(jsonlib2.dumps(verdict)[:300])}" if verdict else ""
                if flags:
                    judge = f"<p class='mt-1 text-xs font-semibold text-red-600'>flags: {esc(str(flags))}</p>" + judge
            except ValueError:
                judge = ""
        hand = conn.execute("select handoff_id, reason, status, notes from handoffs"
                            " where call_id = ? order by handoff_id desc limit 1", (call_id,)).fetchone()
        hand_html = ("<p class='text-xs text-neutral-400'>no handoff for this call</p>" if hand is None else
                     f"<p class='text-xs'>#{hand['handoff_id']} · {esc(hand['reason'])} · "
                     f"<span class='font-semibold'>{esc(hand['status'])}</span></p>"
                     f"<p class='mt-1 rounded bg-neutral-50 p-2 text-xs'>{esc(hand['notes'] or '—')}</p>")
        audit = conn.execute("select ts, actor, tool, args_masked, status from audit_log"
                             " where call_id = ? order by id desc limit 20", (call_id,)).fetchall()
        audit_html = "".join(
            f"<li class='py-0.5 font-mono text-[11px] text-neutral-600'>{esc(a['ts'][11:19] if a['ts'] else '')} "
            f"{esc(a['actor'])}/{esc(a['tool'])} {esc(a['status'])} <span class='text-neutral-400'>{esc(a['args_masked'] or '')}</span></li>"
            for a in audit) or "<li class='text-xs text-neutral-400'>no audit for this call</li>"
        transcript_html = (f"<p class='mt-1 rounded-lg bg-neutral-50 p-3 text-xs whitespace-pre-wrap'>{esc(transcript)}</p>"
                           if transcript else "<p class='mt-1 text-xs text-neutral-400'>no transcript yet</p>")
        return (f"<p class='text-base font-bold text-neutral-900'>Call #{call['call_id']} · {esc(call.get('name') or '')}</p>"
                f"<p class='mt-0.5 font-mono text-xs text-neutral-500'>{esc(call['customer_id'])} · {words} words</p>"
                f"<div class='mt-2 grid grid-cols-3 gap-1.5 text-center'>"
                f"<div class='rounded-lg bg-neutral-50 p-2'><p class='text-[10px] text-neutral-400'>tier</p>"
                f"<p class='text-sm font-bold'>{esc(call['tier'] or '?')}</p></div>"
                f"<div class='rounded-lg bg-neutral-50 p-2'><p class='text-[10px] text-neutral-400'>verified</p>"
                f"<p class='text-sm font-bold'>{'yes' if call['verified'] else 'no'}</p></div>"
                f"<div class='rounded-lg bg-neutral-50 p-2'><p class='text-[10px] text-neutral-400'>outcome</p>"
                f"<p class='text-sm font-bold'>{esc(call['outcome'] or 'open')}</p></div></div>"
                f"<p class='mt-1.5 text-[11px] text-neutral-500'>{esc(reasons)}</p>"
                f"<h4 class='mt-3 text-[10px] font-semibold uppercase tracking-wider text-neutral-400'>Transcript</h4>"
                f"{transcript_html}{judge}"
                f"<h4 class='mt-3 text-[10px] font-semibold uppercase tracking-wider text-neutral-400'>Handoff</h4>"
                f"<div class='mt-1'>{hand_html}</div>"
                f"<h4 class='mt-3 text-[10px] font-semibold uppercase tracking-wider text-neutral-400'>Audit trail</h4>"
                f"<ul class='mt-1 max-h-32 overflow-auto'>{audit_html}</ul>")
    finally:
        conn.close()


def handoff_detail_partial(handoff_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "select handoffs.*, customers.name from handoffs"
            " join customers on customers.customer_id = handoffs.customer_id"
            " where handoff_id = ?", (handoff_id,)).fetchone()
        if row is None:
            return None
        audits = conn.execute("select ts, actor, tool, args_masked, status from audit_log"
                              " where call_id = ? order by id desc limit 10", (row["call_id"],)).fetchall()
        audit_html = "".join(
            f"<li class='py-0.5 font-mono text-[11px] text-neutral-600'>{esc(a['ts'][11:19] if a['ts'] else '')} "
            f"{esc(a['actor'])}/{esc(a['tool'])} {esc(a['status'])}</li>" for a in audits) \
            or "<li class='text-xs text-neutral-400'>no linked audit</li>"
        return (f"<p class='text-base font-bold text-neutral-900'>Handoff #{row['handoff_id']}</p>"
                f"<p class='mt-0.5 text-sm text-neutral-500'>{esc(row['name'])} · "
                f"<span class='font-mono'>{esc(row['customer_id'])}</span> · call {esc(row['call_id'])}</p>"
                f"<p class='mt-2 inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold "
                f"{'bg-amber-100 text-amber-800' if row['status'] == 'open' else 'bg-neutral-200 text-neutral-500'}'>"
                f"{esc(row['status'])} · {esc(row['reason'])}</p>"
                f"<p class='mt-2 rounded-lg bg-neutral-50 p-3 text-sm'>{esc(row['notes'] or '—')}</p>"
                f"<h4 class='mt-3 text-[10px] font-semibold uppercase tracking-wider text-neutral-400'>Call audit</h4>"
                f"<ul class='mt-1'>{audit_html}</ul>"
                f"<div class='mt-3 flex gap-2'><button hx-post='/partials/handoffs/resolve' "
                f"hx-vals='{jsonlib.dumps({'handoff_id': row['handoff_id']})}' hx-target='#console-msg' "
                f"hx-swap='innerHTML' onclick='closeModal()' class='{btn_ghost}'>Resolve</button>"
                f"<button onclick='closeModal()' class='{btn_ghost}'>Close</button></div>")
    finally:
        conn.close()


def message(text, good=True):
    color = ("border-green-500 bg-green-50 text-green-800" if good
             else "border-red-500 bg-red-50 text-red-700")
    return (f"<p class='rounded-xl border-l-4 {color} bg-opacity-60 px-3 py-2 text-sm font-medium'>"
            f"{esc(text)}</p>")
