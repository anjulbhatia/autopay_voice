# Architecture

```mermaid
flowchart TB
    subgraph merchant["Merchant side"]
        console["/console (HTMX)\nqueue · on-call rail · customers · observe"]
        human(["Human in loop\ntakes handoffs"])
    end
    subgraph backend["Backend (FastAPI + SQLite)"]
        api["api.py\npages · partials · webhooks"]
        dash["dash.py\nview-models (data only)"]
        tools["tools.py\nsingle shared logic"]
        rank["ranking.py\np_pay · tiers"]
        agentm["agent.py\nprompts · budgets"]
        db[("SQLite\ncustomers · calls · links · handoffs · audit")]
    end
    subgraph voice["Voice"]
        vapi["Vapi\nweb call default"]
    end
    subgraph customer["Customer side"]
        page["/pay/{token}\nsteps · 10-min link"]
    end

    console -->|"pick mode / filter / manual"| api
    api --> dash
    api --> tools
    tools --> rank
    tools --> agentm
    agentm -->|"system prompt + server tools"| vapi
    vapi -->|"/vapi/tool · /vapi/events"| tools
    tools --> db
    tools -->|"6-char link, BaseURL/pay/*"| page
    page -->|"/pay/result paid/failed"| tools
    tools -->|"open handoff"| human
    human --> console
    mcp["MCP server\nthin wrapper"] --> tools
    console --> db
    page --> db
```


## Components

- `app/db.py` — schema + idempotent seed. Db path from `DATABASE_URL`,
  default `data/autopay_voice.db`. Tables: `customers`, `calls`,
  `payment_links`, `handoffs`, `audit_log`.
- `app/ranking.py` — transparent heuristic, see `ranking.md`.
- `app/tools.py` — every state change lives here. FastAPI and MCP both
  call it; nothing is duplicated. Voice tools bind `call_id`, never a
  customer id from the model.
- `app/agent.py` — prompt assembly + call decisions (tier budgets, tone,
  verification, anti-hallucination).
- `app/provider.py` — Vapi wrapper on `vapi-server-sdk` (transient assistant,
  function tools, web/phone start, webhook parsing). See `voice-provider.md`.
- `app/api.py` — pages (`/console`, `/pay/{token}`), HTMX partials
  (`/partials/*`), Vapi webhooks (`/vapi/tool`, `/vapi/events`).
- `app/dash.py` — console view-models, data only (no HTML).
  Markup lives in `web/partials/*.html` (Jinja components, htmx-swapped);
  `app/api.py` renders them. Phones masked, values escaped by Jinja.
- `app/channels.py` — link delivery adapters (console default, mock
  whatsapp/sms documented).
- `app/cli.py` — `autopay` launcher: single serve path for api +
  pay page + console, plus `test` and `mcp`.
- `web/console.html` — merchant console shell: KPI strips, dial bar,
  queue, on-call rail, customers, observe. Mobile: on-call first,
  bottom nav bar.
- `web/assets/console.js` — nav, ordered dial queue (localStorage),
  active-call refresh, modal, settings.
- `web/pay.html` — customer payment page; `validations.js` (pure checks)
  + `app.js` (steps, animations, backend notify).

## Console partials

| Endpoint | Panel |
|---|---|
| `GET /partials/queue?mode=&q=&tier=` | ranked queue, checkbox per row |
| `GET /partials/active-call` | latest open call + link + handoff forms |
| `POST /partials/queue/start` | open a call for a customer |
| `POST /partials/calls/outcome` · `/cut` · `/join` | close / cut / join live call |
| `POST /partials/links` | mint + send link (binds `call_id` when on-call) |
| `POST /partials/handoffs/create` · `/resolve` | open / resolve handoff |
| `GET /partials/customers?q=&status=` + `/customers/{id}` | table + full record |
| `GET /partials/calls/{id}` | transcript + linked handoff + call audit |
| `GET /partials/calls?q=&outcome=` · `/handoffs?open=&q=` · `/audit?q=` | observe filters |

Forms post urlencoded (htmx) or JSON (tests); the server accepts both.

## Link lifecycle

`create_payment_link()` mints 6 unambiguous chars, `expires_at = now+10min`,
`payment_status → link_sent`. Page countdown is server-synced (`expires_in`).
`POST /pay/result` with `paid` burns the link (`used_at`) and marks
`recovered`; `failed` only audits so the link stays retryable. Unknown
tokens 404 everywhere.

## Base URL

Pass the public origin explicitly (`create_payment_link(..., base_url=...)`)
so links render as `BaseURL/pay/[token]`. Local default is a relative path.
The console stores it per-browser (Settings) and fills link forms with it.
