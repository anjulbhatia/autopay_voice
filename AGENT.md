# AGENT.md: autopay_voice

Read this file fully before writing any code. It tells you what this project is, what is in and out of scope, how it is laid out, and how to start.

## 1. What this project is

`autopay_voice` is a working demo of a voice agent that tries to recover **failed autopay payments** for a **fictional merchant**. It is a submission for a Forward Deployed Engineer take-home task at Razorpay (task: "Build a voice agent for autopay recovery"). It also serves as a portfolio piece showing agent tool-calling, MCP, guardrails, and evals.

The deliverable is a repository with setup and run instructions, a working end-to-end demonstration, and a short note on assumptions and limitations.

### Hard rules from the brief
- Use a voice-agent provider (Vapi) and **exactly 10 fictional customer records**.
- Only call a number the developer controls or has explicit permission to call. Default to the provider's **web-call mode** if a real phone call is not available.
- **Never commit** real customer data, passwords, API keys, or any credentials. Use `.env` and commit only `.env.example`.
- Everything in `data/` is synthetic. Label it as synthetic in the README.

## 2. Scope

### In scope (build these)
1. **Seed and storage.** Load `data/customers.json` into SQLite on startup (idempotent).
2. **Ranking.** Score and order customers for outreach (see section 5). Every score stores human-readable reasons.
3. **Campaign control.** Merchant can start the top of the queue (auto) or pick a customer (manual).
4. **Voice calls via Vapi.** FastAPI webhooks handle tool calls and end-of-call events.
5. **Prompt assembly.** `agent/prompt.md` (persona and tiers), `agent/policies.md`, `agent/guardrails.md` are combined per call.
6. **Payment link flow.** If the customer agrees to pay, send a single-use expiring link through a channel adapter (mock by default).
7. **Human handoff queue** for calls that need a person.
8. **Merchant console** (HTMX, served from FastAPI) and a **customer payment page** (plain HTML from FastAPI).
9. **Tests and evals**, including a text-based caller simulator with adversarial personas.
10. **MCP server** as a thin, read-mostly wrapper over the same functions. Build this last.

### Out of scope (do not build)
- Real WhatsApp or SMS sending (needs business accounts, templates, consent). Use the mock adapter and document the limitation.
- Real money movement. Razorpay test mode only, and only if the docs support the flow; otherwise a fake payment page.
- ML-trained ranking. Use the transparent heuristic. Mention a trained model as future work.
- Authentication for the dashboard beyond a simple local-only note.
- Multi-language voice, multi-tenant support, analytics beyond the basics.

If a task is not listed in scope, ask before building it.

## 3. Architecture

```
autopay_voice/
  AGENT.md
  README.md
  .env.example
  pyproject.toml            # managed with uv (`autopay` entrypoint -> app.cli:main)
  data/customers.json       # 10 fictional records
  app/
    agent.py                # prompt assembly + call budgets + guardrail judge
    api.py                  # FastAPI: pages, HTMX partials, /vapi/tool, /vapi/events, /pay/{token}
    channels.py             # whatsapp | sms | console adapters (console is default)
    cli.py                  # `autopay` launcher: serve | test | mcp (stub) | help
    config.py               # env settings + business limits (caps, hours, TTLs)
    dash.py                 # HTMX HTML partials (phones masked)
    db.py                   # schema + idempotent seed
    models.py               # Literal enums + seed validation
    provider.py             # Vapi REST wrapper (web-call default, phone opt-in)
    ranking.py              # scoring, eligibility, tiers, reasons
    tools.py                # functions the voice agent can call (+ shared campaign logic)
    utils.py                # html escape, phone mask, reasons parse
  agent/
    prompt.md               # persona, tone, tier blocks
    policies.md             # what the agent may and may not do
    guardrails.md           # privacy, safety, refusal rules
    vapi_config.json        # assistant and tool definitions
  web/
    console.html + assets/console.js                  # merchant console shell
    pay.html + assets/app.js + assets/validations.js  # customer payment page
  scripts/
    generate_customers.py   # synthetic seed generator
  tests/
```

SQLite tables: `customers`, `calls`, `payment_links`, `handoffs`, `audit_log`.

## 4. Main loop

1. On start: create tables, seed from `data/customers.json`.
2. Rank eligible customers (section 5).
3. Merchant starts auto mode or selects a customer.
4. Render the prompt for that customer's tier and start the Vapi call.
5. Vapi calls `/vapi/tool` during the call and `/vapi/events` at the end. Persist everything.
6. If the customer agrees to pay and is verified, send a link. If the call needs a human, create a handoff.

## 5. Ranking rules

Inputs per customer: `last_call_at`, `last_message_at`, `amount_due`, `default_history`, `attempts`, `failure_reason`, `do_not_call`.

- A customer is **not eligible** if `do_not_call`, attempts are at the daily or total cap, or a call happened in the last 24 hours.
- `p_pay` is a logistic function of simple features (prior defaults lower it, insufficient-balance failures raise it, a very recent message lowers it).
- `priority = p_pay * amount_due`. The dashboard offers toggles: expected value, easiest first, highest amount first.
- `tier`: `short` when `p_pay >= 0.7`, `standard` when `>= 0.4`, otherwise `extended`.
- **Tier controls call length and empathy, never pressure.** Low-propensity customers get more patience and earlier human handoff, not firmer wording.
- Do not use sensitive personal attributes as features. Always store and display the reasons for a score.

## 6. Guardrails: enforce in code, not only in prompts

These are requirements, not suggestions. Write tests for each.

1. **Per-call binding.** Tools take no customer ID from the model. The server maps call ID to customer. Other customers' data must never appear in the model's context.
2. **Verify before disclose.** `verify_identity` must succeed before amount or account details are returned and before any payment link is sent. Until then, those tools return a refusal.
3. **Wrong person, voicemail, or unverified caller:** the agent says only a generic message, with no amounts and no account details.
4. **Hard limits in code:** allowed calling hours, daily and total attempt caps, do-not-call, immediate stop on opt-out.
5. **Links** go only to the number on file, are single-use, and expire.
6. **No threats, no false urgency, no legal claims.** The agent identifies itself, states the purpose, and offers a human on request. It must answer honestly if asked whether it is an AI.
7. **Post-call audit:** an LLM judge scores each transcript against `guardrails.md` and flags violations in the dashboard.
8. Log every tool call in `audit_log` with call ID, tool, arguments (masked), and result status.

## 7. Agent tools (voice agent can call these)

- `verify_identity(answer)`
- `get_failed_payment()` (gated)
- `send_payment_link(channel)` (gated, uses number on file)
- `schedule_retry(when)`
- `request_human_handoff(reason)`
- `log_outcome(result, notes)`

Failure-reason strategy: insufficient balance, offer a payment link or a retry date; expired mandate, send a mandate-update link; bank decline, suggest another method or a retry on a different day. Keep this logic in code and described in `policies.md`.

## 8. MCP server

Thin wrapper, no duplicated logic. Read-only tools: `get_ranked_queue`, `get_campaign_stats`, `get_call_outcome`, `list_handoffs`, with contact details masked. One write tool, `queue_call`, which requires an explicit `confirm=true`.

## 9. Evals and tests

- **Unit tests:** ranking eligibility and tiers, call-to-customer binding, verification gate (link refused when unverified), calling-hour and attempt caps, opt-out.
- **Simulator personas:** cooperative, confused, angry, constantly interrupting, Hinglish speaker, asks "are you a bot?", asks about another customer, claims to be a relative, attempts prompt injection, wrong number.
- **Metrics:** recovery rate, correct handoffs, guardrail violations (target: zero), average call length per tier. Compare against a naive baseline (blind retry, no personalization). State clearly that results are on synthetic data.
- `uv run autopay test` must pass offline without a real call. Simulator (`sim/`) plus `simulate`/`demo` commands are planned, not built yet.

## 10. Conventions

- Python 3.12, FastAPI, SQLite, pytest, ruff. Manage dependencies with `uv`.
- Type hints on public functions. Keep modules small. Put business logic in `tools.py` and `ranking.py` so FastAPI and MCP share it.
- Seed randomness with a fixed seed so runs are repeatable.
- No secrets in code or logs. Mask phone numbers in logs and UI by default.
- Vapi and Razorpay details change. **Check their current docs** before relying on any API shape, pricing, or telephony rule rather than assuming. Vapi's free numbers have been reported as US-only, so start with web-call mode and treat a real phone call as optional.
- Commit small, with clear messages.

## 11. Commands (`uv run autopay` — no Makefile)

```
uv sync                 # install deps
uv run autopay          # seed db if missing, serve api + pay page + merchant console on :8000
uv run autopay test     # pytest
uv run autopay mcp      # stub: prints planned-last notice (server not built yet)
uv run autopay help     # full help
uv run python -m app.db # seed explicitly (idempotent)
```

## 12. Suggested order of work

1. `db.py`, seed, `ranking.py` and its tests.
2. `tools.py` with the verification gate and its tests.
3. FastAPI webhooks and Vapi wiring in web-call mode.
4. `prompt.md`, `policies.md`, `guardrails.md`, and `agent.py` rendering.
5. `web/pay.html` + HTMX merchant console (`web/console.html`, `app/dash.py`).
6. Simulator and evals (planned, not built yet).
7. MCP server (planned last).
8. README: setup, run steps, assumptions, limitations, and what to harden for production.

## 13. Definition of done

- A reviewer can clone, run `uv sync` then `uv run autopay`, and see the ranked queue, live-call rail, links, handoffs, and audit in the console without any credentials.
- With a Vapi key, a web call completes end to end and the outcome appears in the dashboard.
- All guardrail tests pass. No real data or credentials exist in the repo.
- The README lists assumptions, limitations (synthetic data, mock messaging, single language, no real consent or DND registry), and the production hardening steps.

## 14. When unsure

Prefer the simpler option, say what you assumed, and keep going. If a decision affects scope, guardrails, or anything involving real phone numbers or credentials, stop and ask the developer.