# Installation

Prereqs: Python 3.13+ and `uv`.

```sh
uv sync
cp .env.example .env   # then fill VAPI_API_KEY for real calls
```

| Command | What it does |
|---|---|
| `uv run autopay` | seed db if missing, serve merchant console on `:8000` + pay page on `:8800` |
| `uv run autopay test` | pytest |
| `uv run autopay help` | full help |

Seed explicitly: `uv run python -m app.db` (idempotent).
Override the db path: `DATABASE_URL="sqlite:///data/autopay_voice.db"`.

## Tour (two servers, three surfaces)

| URL | Surface |
|---|---|
| `http://127.0.0.1:8000/console` | merchant console: queue, live calls, links, handoffs, audit |
| `http://127.0.0.1:8800/pay/<token>` | customer payment page (token from a generated link) |
| `POST 127.0.0.1:8000/vapi/tool` · `POST 127.0.0.1:8000/vapi/events` | voice webhooks (in-call dispatch, end-of-call report) |

Calls start from the console only (`POST /partials/queue/start`);
there is no auto-dialer.

## Console tabs

- **Calls** — KPI strip, dial bar (manual start, search, tier filter,
  sort mode), queue with ordered dial checkboxes, on-call rail with
  the live call, payment-link sender, and handoff opener bound to it.
- **Customers** — KPI strip, search + status filter, click a row for
  the full record (dues, security question, calls, links, handoffs).
- **Observe** — call records (click for transcript + linked handoff +
  call audit), handoffs, audit log; each with its own filter.

Voice wiring: transient per-call assistant built by `app/provider.py`
(`vapi-server-sdk`, 6 inline function tools → `http://127.0.0.1:8000/vapi/tool`) from the
`app/agent.py` prompt — reference shape in `agent/vapi_config.json`.
Webhooks: `POST /vapi/tool` (in-call
dispatch, always 200 with a `results` array) · `POST /vapi/events`
(end-of-call transcript + guardrail scan). Full contract:
[voice provider](voice-provider.md).

## Payment links (local)

Tokens are minted by `create_payment_link()` as
`http://127.0.0.1:8800/pay/<6-char-token>` and die after 10 minutes
or first paid use.
6-char tokens are enumerable; never use them for real money.
