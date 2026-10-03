# Call Workflow

## Startup

1. `ingest_customers_data()` — schema + idempotent seed of `data/customers.json`.
2. `rank_queue(mode)` — scored, reasoned queue.
3. Merchant picks: easiest-first, highest-first, or a specific caller
   (dashboard queue; manual pick allowed).

## The call

4. `start_call()` snapshots tier/p_pay/reasons, counts the attempt.
5. Provider opens a Vapi web call with the assembled prompt
   (`agent.py`: persona + policies + guardrails + tier + customer context).
6. Agent verifies identity first (max 2 tries). Unverified callers,
   wrong persons, and voicemail get the generic message only — never
   amounts, dates, or links.
7. Verified: `get_failed_payment()` → failure playbook
   (balance → link or retry date; mandate → update-mandate link;
   decline → retry another day). Agreement → `send_payment_link()`
   to the number on file. Confusion/distress/request → handoff.
8. `log_outcome()` closes the call; opt-out flips `do_not_call`.

## After

- Pay page notifies `POST /pay/result` (best-effort); paid burns the
  link and marks `recovered`, failed only audits.
- Post-call judge scores the transcript against `guardrails.md`;
  violations surface on the dashboard.
- Every tool call lands in `audit_log` with masked args.
