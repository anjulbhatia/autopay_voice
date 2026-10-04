# Voice Provider (Vapi)

All Vapi traffic goes through `app/provider.py`, built on the official
`vapi-server-sdk` (`pip install vapi_server_sdk`, already in `pyproject.toml`).
No raw REST. Shapes below verified against `docs.vapi.ai` on 2026-10-04 —
re-check the docs before changing any payload, Vapi iterates fast.

## Calls

```python
from vapi import Vapi
client = Vapi(token=VAPI_API_KEY)
```

- **Web call** (default, no phone number): `client.calls.create(assistant=...)`.
- **Outbound phone**: `client.calls.create(assistant=..., phone_number_id=...,
  customer={"number": ...})`. Requires `confirm=True` plus a destination you
  control (`TEST_CALL_TO_NUMBER` env wins, else per-call input).
  `VAPI_PHONE_NUMBER_ID` preferred; raw `VAPI_PHONE_NUMBER` is a best-effort
  fallback. Free Vapi numbers are US-only per Vapi docs — web-call first.

Every call uses a **transient assistant** built per customer by
`provider.build_assistant()` from `app/agent.py assemble_prompt()` output:
name + `model: {provider: openai, model, messages, tools}` + `server: {url}` +
`serverMessages: ["tool-calls", "end-of-call-report"]`.
Nothing is stored server-side; `agent/vapi_config.json` mirrors the same
shape as a reference for dashboard/CLI import only.

## Tools

Six inline **function tools** (`model.tools[]`), one `server.url` each
pointing at `BASE_URL/vapi/tool` (webhook precedence per docs:
tool url → assistant url → number url → org url):

`verify_identity`, `get_failed_payment`, `send_payment_link`,
`schedule_retry`, `request_human_handoff`, `log_outcome`.

## Webhooks

- `POST /vapi/tool` — request: `message.type == "tool-calls"` with
  `message.toolCallList[] = {id, function: {name, arguments}}`.
  Response, always HTTP 200:
  `{"results": [{"toolCallId": id, "result": "<flat string>"}, ...]}`.
  Failures use `"error"` instead of `"result"` (Vapi speaks it; a non-200
  would surface as "no result returned"). Dict results are JSON-serialized —
  `result` must be a flat string. Multiple tool calls in one message each
  get their own entry, matched by `toolCallId`.
- `POST /vapi/events` — `message.type == "end-of-call-report"` with
  `endedReason` and transcript at `artifact.transcript`. Anything else is
  ignored (`{"ok": true, "ignored": true}`).

`provider.call_id_of()` binds the provider call id to our internal `calls`
row; the model never supplies a customer id. Unknown calls get an error
entry, never data.

## Offline

`start_web_call` / `start_phone_call` accept `client=` for injection —
tests pass a fake capturing `calls.create(**kwargs)`, so the suite never
touches the network. Live validation (SDK DTO coercion of the transient
assistant dict) happens on the first real call with a key.
