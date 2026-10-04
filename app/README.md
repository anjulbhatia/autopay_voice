# app/ — module notes

Backend package. Every module keeps a 1–2 line docstring; the notes below
are the longer context that used to live in file headers.

- `api.py` — FastAPI hub. Pages (`/console`, `/pay/{token}`), HTMX partials
  (`/partials/*` rendered from `web/partials/` via `dash` view-models),
  Vapi webhooks (`/vapi/tool` results envelope, `/vapi/events` report).
- `dash.py` — console view-models, data only. Queries DB, masks phones,
  pre-formats labels/pills/currency. Jinja autoescape handles escaping;
  `hx-vals` JSON uses `|safe` on id-only payloads.
- `tools.py` — every state change lives here (calls, verify gate, links,
  retry, handoffs, outcomes, audit). FastAPI and (planned) MCP share it.
  Voice tools bind `call_id`, never a customer id from the model.
- `provider.py` — Vapi on `vapi-server-sdk`, no raw REST. Transient assistant
  per call, 6 inline function tools → `BASE_URL/vapi/tool`. Full contract:
  `docs/voice-provider.md` (shapes verified 2026-10-04, re-check Vapi docs
  before changing payloads). `start_*` accept `client=` for offline tests.
- `agent.py` — framework-agnostic voice policy: prompt assembly (pre-verify
  context carries no amounts/dates/phones/notes), tier budgets + empathy,
  calling-hours gate (IST), generic message, heuristic transcript judge.
- `ranking.py` — transparent heuristic, no ML. `p_pay` logistic over failure
  boost, defaults, attempts, recent-message; `priority = p_pay × amount_due`;
  tiers short/standard/extended; eligibility (dnc, caps, 24h cooldown).
  Details: `docs/ranking.md`.
- `db.py` — SQLite schema + idempotent seed (`INSERT OR IGNORE` validated by
  `models.CustomerSeed`). Path from `DATABASE_URL`, default
  `data/autopay_voice.db`. Tables: customers, calls, payment_links,
  handoffs, audit_log.
- `models.py` — Literal enums (single source; `config` derives tuples via
  `typing.get_args`) + `CustomerSeed` validation.
- `config.py` — env settings + business limits (caps, hours, TTLs, token
  alphabet, verify attempts).
- `channels.py` — link message templates + mock send (always `mock-sent`;
  real delivery needs business accounts, templates, consent). Callers must
  pass an already-masked `to_label` — raw numbers never reach logs here.
- `cli.py` — `autopay` launcher: serve (seed-if-missing + uvicorn),
  test (pytest), mcp (stub, planned last), help.
- `utils.py` — `esc`, `mask_phone`, `parse_reasons` (never raises).
