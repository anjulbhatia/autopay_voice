# autopay_voice

Voice-agent demo that recovers failed autopay payments for a fictional
merchant — Vapi voice loop, ranked outreach queue, single-use payment
links, human handoffs, and a merchant console. Full spec: [`AGENT.md`](AGENT.md).

All data in `data/` is synthetic (fake sequential phones
`+91-90000-00001…`, salted-hash security answers, zero real credentials).

## Quickstart

```sh
uv sync
cp .env.example .env   # fill VAPI_API_KEY for real calls
uv run autopay              # api + pay page + merchant console on :8000 (seeds db first run)
uv run autopay test         # tests
```

| URL | What |
|---|---|
| `http://127.0.0.1:8000/console` | merchant console (HTMX): queue, live calls, links, handoffs, audit |
| `http://127.0.0.1:8000/pay/<token>` | customer payment page (token from a generated link) |

Calls start from the console only — queue checkbox order, dial bar, or
per-row Start. See [installation](docs/installation.md) for the console
tour, voice wiring, and public-link setup.

## How it works

1. **Rank** — every customer scores `p_pay` with human reasons; tiers
   (`short`/`standard`/`extended`) set call length and empathy, never
   pressure. See [ranking](docs/ranking.md).
2. **Call** — merchant opens a call, Vapi runs the assembled prompt;
   identity verifies (max 2 tries) before any amount or link is shared.
3. **Recover or hand off** — agreement mints a 6-char single-use link
   (10-min expiry) to the number on file; distress/confusion/request
   opens a handoff with the live call's actuals attached.
4. **Audit** — every tool call lands in `audit_log`; the post-call judge
   scans transcripts against `agent/guardrails.md`. See
   [call workflow](docs/call-workflow.md).

```
browser (/console, /pay/*) ──► FastAPI (api.py renders web/partials via dash.py view-models)
                                    │  ▲
Vapi (/vapi/tool, /vapi/events) ────┘  │  shared logic (tools.py, ranking.py)
                                       ▼
                              SQLite (customers · calls · links · handoffs · audit)
```

Full diagram and component table: [architecture](docs/architecture.md).
Docs: [installation](docs/installation.md) · [architecture](docs/architecture.md) ·
[ranking](docs/ranking.md) · [call workflow](docs/call-workflow.md).

## Notes

- Single-file SQLite at `data/autopay_voice.db` (gitignored); re-seeding is idempotent.
  Set `DATABASE_URL` (see `.env.example`) to override the path.
- Messaging is a mock adapter (console/whatsapp/sms render + log); no real
  sends, no real money movement.
- Single language, one demo merchant, no auth beyond local-only.

## Production hardening (not done here)

Real consent + DND checks, authenticated merchant access, non-enumerable
link tokens, rate-limited webhooks with signature verification, trained
ranking model, real channel providers with templates, multi-language
prompts, and PII retention policy.
