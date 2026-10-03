# Call Workflow

## Startup

1. `ingest_customers_data()` — schema + idempotent seed of `data/customers.json`.
2. `rank_queue(mode)` — scored, reasoned queue.
3. Merchant picks: easiest-value, easiest-first, highest-first, queue
   checkboxes in dial order, or a specific caller (console queue;
   manual pick allowed).

## The call

4. `start_call()` snapshots tier/p_pay/reasons, counts the attempt.
   Calls open from the console only.
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

## During (console on-call rail)

- The latest open call shows with tier, verification, and score reasons.
- `Cut` ends it (`no_answer`), `Join → handoff` pulls a human in,
  `Log` records any terminal outcome.
- The link sender is pre-bound to the on-call customer (`call_id`
  attached, so the audit trail links them).
- The handoff opener is pre-bound to the live `call_id` with the
  actuals visible — reason + notes, one click.

## After

- Pay page notifies `POST /pay/result` (best-effort); paid burns the
  link and marks `recovered`, failed only audits.
- Post-call judge scores the transcript against `guardrails.md`;
  violations surface in Observe, inside the call record.
- Every tool call lands in `audit_log` with call ID, tool, arguments
  (masked), and result status. Opening a call record shows its
  transcript, linked handoff, and call audit together.
