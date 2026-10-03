# BUGFIX.md — functional bugs found by code audit (2026-10-03)

Tests pass (14/14), so these are spec-vs-behavior gaps, not red tests.
Severity: guardrail/data-integrity first. All data synthetic.

## Fixed in this pass

1. **`GET /pay/{token}` rendered live pay form for used/expired links.**
   `get_link_context()` returned `used`/`expired`, `pay_page` ignored it and
   rendered amount + phone + pay form anyway. `POST /pay/result` correctly
   rejected (409/410), so money safe, but UI invited card entry on dead link.
   Fix: dismissed render (410 expired, 409 used), amount/phone hidden unless live.
   `app/api.py:pay_page`

2. **Voice `send_payment_link` minted but never sent.**
   Created DB row + flipped `payment_status`, but never called
   `channels.render/send` and never touched `last_message_at`. Customer agreed
   on call, got nothing. Console path did send. Fix: voice path renders +
   mock-sends via channel adapter, updates `last_message_at`, audits send.
   `app/tools.py:send_payment_link`

3. **`POST /partials/links` trusted browser input, crashed on unknown customer.**
   `customer_id` unchecked (`person["name"]` → TypeError 500), `kind`/`channel`
   unvalidated, `ttl` raw `int()` (500 on junk, negative allowed), and
   `base_url` taken verbatim from browser localStorage for link display.
   Fix: 404 unknown customer, 400 bad kind/channel/ttl
   (ttl 1–1440) and bad non-https `base_url`; explicit https origin still
   allowed (console settings field), else server `BASE_URL` env.
   `app/api.py:partial_link`, `app/tools.py:create_payment_link`

4. **`log_outcome("unverified")` per `policies.md` crashed SQLite CHECK.**
   Docs told agent to log `unverified`; DB + `CallOutcome` allow only
   `link_sent|retry_scheduled|handoff|no_answer|wrong_person|refused|opted_out|failed`.
   Uncaught `IntegrityError` → 500 to Vapi mid-call. Fix: whitelist in
   `tools.log_outcome` (ValueError on invalid), `vapi/tool` returns
   `{"error": ...}` instead of 500; docs corrected to `refused`.
   `app/tools.py:log_outcome`, `app/api.py:vapi_tool`, `agent/policies.md`

5. **`schedule_retry` accepted any string (`"someday"` stored as `retry_at`).**
   No date validation, no hours/cap note per `policies.md` §5. Fix: require
   timezone-aware ISO-8601 datetime, refuse unparseable (ValueError → tool
   error, no write). `app/tools.py:schedule_retry`

6. **`log_outcome` left funnel stats stale.**
   Only `opted_out` synced to `customers`. `link_sent`/`handoff` via console
   outcome dropdown never updated `payment_status`, so at-risk sum + funnel
   lied. Fix: `link_sent→link_sent`, `handoff→handoff` (never overwrite
   `recovered`). `app/tools.py:log_outcome`

7. **End-of-call left calls `open` forever unless exact no-answer wording.**
   `vapi/events` mapped only no-answer phrases, else `NULL` → `coalesce`
   kept `NULL`. Open-call rail stuck, `open_calls` inflated. Fix: default to
   `failed` when call has no outcome and no no-answer signal.
   `app/api.py:vapi_events`

8. **`expires_in` capped at 600s regardless of `ttl_minutes`.**
   `min(remaining, 600)` → 60-min link showed 10:00 ticker. Fix: report real
   remaining. `app/tools.py:get_link_context`

9. **Doc/code name mismatch: `expired_mandate` vs `mandate_expired`.**
   `policies.md` playbook used `expired_mandate`; data/code/DB use
   `mandate_expired`. Agent matching docs text would never match tool output.
   Fix docs to `mandate_expired`. `agent/policies.md`

10. **`prompt.md` contained unfilled `{amount_due}`/`{payment_due_date}`/
    `{preferred_channel}`/`{when}` placeholders.**
    `assemble_prompt()` never substitutes them (facts arrive via tool results
    + CALL CONTEXT), so agent reads literal braces. Fix: reword to reference
    verified tool results, no brace vars. `agent/prompt.md`

## Known, not fixed (documented)

- **Seed `INSERT OR IGNORE` never updates edited `customers.json`.**
  Regenerating seed leaves stale rows in existing `data/*.db` (gitignored).
  Workaround: delete DB and re-serve. Needs migration or upsert.
- **Console dial dropdown lists all customers incl. do-not-call/capped.**
  `start_call` correctly refuses with message, but list invites the attempt.
  Should show eligible-first or badge suppression reason.
- **One-link-per-agreement not enforced.** Unlimited live links per customer;
  single-use holds on burn, but resend discipline is prompt-only.
- **`pay.html` shows link-holder full phone.** Fine for owner viewing own
  link, but merchant-side views stay masked per G7.
- **MCP server, simulator, evals per AGENT.md §§8–9 not built yet.**
  `autopay mcp` prints planned-last notice. Out of scope for bugfix pass.
