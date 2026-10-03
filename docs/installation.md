# Installation

Prereqs: Python 3.13+, `uv`, and (for public links) `cloudflared`.

```sh
uv sync
cp .env.example .env   # then fill VAPI_API_KEY for real calls
```

| Command | What it does |
|---|---|
| `uv run autopay` | seed db if missing, serve backend on `:8000` |
| `uv run autopay --dash` | backend + merchant dashboard |
| `uv run autopay test` | pytest |
| `uv run autopay help` | full help |

Voice wiring: `agent/vapi_config.json` holds the assistant + 6 server tools
pointing at `BASE_URL/vapi/tool` — replace `BASE_URL` with the deployed
origin. Webhooks: `POST /vapi/tool` (in-call dispatch, always 200 with
result/error), `POST /vapi/events` (end-of-call transcript + guardrail scan).

Seed explicitly: `uv run python -m app.db` (idempotent).
Override the db path: `DATABASE_URL="sqlite:///data/autopay_voice.db"`.

## Public payment links (demo)

```sh
cloudflared tunnel --url http://127.0.0.1:8000
```

Share `https://<tunnel>/pay/<6-char-token>`. Tokens are minted by
`create_payment_link()` and die after 10 minutes or first paid use.
Quick tunnels are account-less with no uptime guarantee — demo only.
6-char tokens are enumerable; never use them for real money.
