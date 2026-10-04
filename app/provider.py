"""Vapi voice provider on ``vapi-server-sdk``. Contract: docs/voice-provider.md."""
import json
import os

from app import agent as agent_rules

tool_names = ["verify_identity", "get_failed_payment", "send_payment_link",
              "schedule_retry", "request_human_handoff", "log_outcome"]

#: Server webhook events we subscribe to on every call.
server_messages = ["tool-calls", "end-of-call-report"]

tool_specs = [
    {"name": "verify_identity",
     "description": "Check the caller's security answer. Call first, max twice.",
     "parameters": {"type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"]}},
    {"name": "get_failed_payment",
     "description": "Failed payment details. Refuses until verify_identity succeeds.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "send_payment_link",
     "description": "Single-use expiring link to the number on file. Verified callers only.",
     "parameters": {"type": "object",
                    "properties": {"channel": {"type": "string",
                                               "enum": ["console", "whatsapp", "sms"]}}}},
    {"name": "schedule_retry",
     "description": "Reminder on a customer-accepted date.",
     "parameters": {"type": "object",
                    "properties": {"when": {"type": "string"}},
                    "required": ["when"]}},
    {"name": "request_human_handoff",
     "description": "Queue a human takeover with a reason.",
     "parameters": {"type": "object",
                    "properties": {"reason": {"type": "string"},
                                   "notes": {"type": "string"}},
                    "required": ["reason"]}},
    {"name": "log_outcome",
     "description": "Close the call with an outcome. Always call this last.",
     "parameters": {"type": "object",
                    "properties": {"result": {"type": "string"},
                                   "notes": {"type": "string"}},
                    "required": ["result"]}},
]


class vapi_error(Exception):
    pass


def api_key():
    key = os.environ.get("VAPI_API_KEY", "").strip().strip('"')
    if not key or key == "YOUR_VAPI_API_KEY":
        raise vapi_error("set a real VAPI_API_KEY (see .env.example)")
    return key


def vapi_phone_id():
    """Vapi phoneNumberId UUID from the dashboard. Required for outbound dial."""
    return os.environ.get("VAPI_PHONE_NUMBER_ID", "").strip()


def server_url(base_url):
    """Single webhook origin for tool calls: BaseURL/vapi/tool."""
    return base_url.rstrip("/") + "/vapi/tool"


def build_assistant(system_prompt, base_url, model="gpt-4o-mini"):
    """Transient assistant payload: prompt assembled by app/agent.py, our six
    function tools with per-tool server urls. Passed as ``assistant=`` to
    ``client.calls.create`` — never stored server-side."""
    url = server_url(base_url)
    return {
        "name": "autopay-recovery (synthetic demo)",
        "first_message": agent_rules.first_message(),
        "server": {"url": url},
        "serverMessages": list(server_messages),
        "model": {"provider": "openai", "model": model,
                  "messages": [{"role": "system", "content": system_prompt}],
                  "tools": [{"type": "function",
                             "function": {"name": spec["name"],
                                          "description": spec["description"],
                                          "parameters": spec["parameters"]},
                             "server": {"url": url}} for spec in tool_specs]},
    }


def _sdk(key=None):
    from vapi import Vapi
    return Vapi(token=key or api_key())


def _plain(call):
    """SDK Call model -> plain dict. Passes through dicts (tests, fakes)."""
    if isinstance(call, dict):
        return call
    dump = getattr(call, "model_dump", None)
    if callable(dump):
        return dump()
    return dict(call)


def _sdk_error(exc):
    raise vapi_error(f"vapi call failed: {exc}")


def start_web_call(system_prompt, base_url, key=None, client=None):
    """Start a browser/client call. Returns the provider call as a plain dict."""
    sdk = client if client is not None else _sdk(key)
    try:
        call = sdk.calls.create(assistant=build_assistant(system_prompt, base_url))
    except Exception as exc:
        _sdk_error(exc)
    return _plain(call)


def test_destination(explicit=""):
    """Hardcoded test destination. TEST_CALL_TO_NUMBER env wins when set,
    else falls back to the explicitly given number (per-call input)."""
    env = os.environ.get("TEST_CALL_TO_NUMBER", "").strip()
    if env:
        return env
    return (explicit or "").strip()


def start_phone_call(system_prompt, base_url, to_number=None, key=None, confirm=False,
                     client=None):
    """Outbound phone. Refuses unless confirm=True with a destination —
    only call numbers you control or have permission for.

    Destination: TEST_CALL_TO_NUMBER env wins when set, else to_number arg.
    Dials from VAPI_PHONE_NUMBER_ID (the SDK takes a phoneNumberId, not a
    raw caller number).
    """
    dest = test_destination(to_number)
    if not confirm or not dest:
        raise vapi_error("phone calls need confirm=True and a destination you control "
                         "(TEST_CALL_TO_NUMBER or explicit to_number)")
    phone_id = vapi_phone_id()
    if not phone_id:
        raise vapi_error("phone calls need VAPI_PHONE_NUMBER_ID (UUID from the Vapi dashboard)")
    sdk = client if client is not None else _sdk(key)
    try:
        call = sdk.calls.create(assistant=build_assistant(system_prompt, base_url),
                                phone_number_id=phone_id,
                                customer={"number": dest})
    except Exception as exc:
        _sdk_error(exc)
    return _plain(call)


def tool_calls_of(body):
    """All tool-call items in a webhook body. Canonical per current docs:
    ``message.toolCallList[]``; legacy ``toolCalls``/``tool_calls`` shapes
    still accepted so older events keep working."""
    message = body.get("message", body) if isinstance(body, dict) else {}
    for key in ("toolCallList", "toolCalls", "tool_calls"):
        found = message.get(key)
        if found:
            return list(found) if isinstance(found, list) else [found]
    for key in ("toolCall", "functionCall", "function_call"):
        single = message.get(key)
        if single:
            return [single]
    return []


def parse_tool_call(body):
    """First tool-call item (kept for callers that handle one call)."""
    items = tool_calls_of(body)
    return items[0] if items else {}


def tool_call_id_of(item):
    """Provider tool-call id -> echoed back as ``toolCallId`` in results."""
    return (item or {}).get("id", "")


def tool_name_args(item):
    """(name, args) from a tool-call item. Arguments may arrive as object
    or JSON string; unparseable strings become {} (never raise mid-call)."""
    item = item or {}
    fn = item.get("function") or {}
    name = item.get("name") or fn.get("name", "")
    args = item.get("arguments") or fn.get("arguments") or item.get("parameters") \
        or fn.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except ValueError:
            args = {}
    return name, args


def tool_result(call_id, payload):
    """Success envelope. ``result`` must be a flat string per docs, so dicts
    are JSON-serialized (the model reads the JSON back)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"toolCallId": call_id, "result": text}


def tool_error(call_id, message):
    """Failure envelope. Still HTTP 200 — Vapi speaks the error message
    instead of reporting 'no result returned'."""
    return {"toolCallId": call_id, "error": str(message)}


def call_id_of(body):
    """Provider call id -> our internal call row (bound server-side)."""
    message = body.get("message", {}) if isinstance(body.get("message"), dict) else {}
    return (body.get("call", {}) or {}).get("id") or body.get("callId") or (message.get("call", {}) or {}).get("id")


def report_transcript(message):
    """Transcript from an end-of-call-report. Current docs put it at
    ``artifact.transcript``; older payloads carry top-level fields."""
    artifact = message.get("artifact") or {}
    return (artifact.get("transcript") or message.get("transcript")
            or message.get("summary") or "")
