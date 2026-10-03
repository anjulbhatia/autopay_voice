# autopay_voice

Voice-agent demo that recovers failed autopay payments for a fictional merchant.
Full spec: [`AGENT.md`](AGENT.md).

## Quickstart

```sh
uv sync
uv run autopay              # backend on :8000 (seeds the db first run)
uv run autopay --dash       # backend + merchant dashboard
uv run autopay test         # tests
```

Open `http://127.0.0.1:8000/pay/<token>` for the customer payment page.

Docs: [installation](docs/installation.md) · [architecture](docs/architecture.md) ·
[ranking](docs/ranking.md) · [call workflow](docs/call-workflow.md).

## Notes

- All data in `data/` is synthetic — fake sequential phones (`+91-90000-00001…`),
  salted-hash security answers, zero real credentials.
- Single-file SQLite at `data/autopay_voice.db` (gitignored); re-seeding is idempotent.
  Set `DATABASE_URL` (see `.env.example`) to override the path.
