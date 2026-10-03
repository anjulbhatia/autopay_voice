# Ranking

Transparent heuristic — no ML, every score ships human reasons
(`score_reasons` JSON on the call row, shown on the dashboard).

## Eligibility (all must pass)

- `do_not_call` is false
- `attempts_total` below total cap (5)
- fewer than daily cap (2) calls today (counted from `calls` rows, UTC)
- no call in the last 24h

## Score

`p_pay = sigmoid(0.2 + failure_boost − 0.4·defaults − 0.15·attempts − 0.6·recent_message)`

| Feature | Effect |
|---|---|
| `insufficient_balance` | +0.9 (often retries fine) |
| `mandate_expired` | −0.2 (needs renewal first) |
| `bank_decline` | −0.5 (bank said no) |
| each prior default | −0.4 |
| each prior attempt | −0.15 |
| message within 24h | −0.6 (already engaged) |

No personal attributes are features. `priority = p_pay × amount_due`.

## Tiers and modes

- `short` (p ≥ 0.7): under 90s, get to the point.
- `standard` (p ≥ 0.4): 2–3 min, explain + two options.
- `extended` (p < 0.4): 3–5 min, extra patience, human offer by minute 2.
- Tier changes length and empathy only — never pressure.

Queue modes: `expected_value` (priority), `easiest` (p_pay), `highest` (amount).
