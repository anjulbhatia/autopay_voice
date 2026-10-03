import os

import httpx

api_base = "https://api.vapi.ai"

tool_names = ["verify_identity", "get_failed_payment", "send_payment_link",
              "schedule_retry", "request_human_handoff", "log_outcome"]


class vapi_error(Exception):
    pass


def api_key():
    key = os.environ.get("VAPI_API_KEY", "").strip().strip('"')
    if not key or key == "YOUR_VAPI_API_KEY":
        raise vapi_error("set a real VAPI_API_KEY (see .env.example)")
    return key


def vapi_caller_number():
    """Your taken Vapi number (caller-id side). Set VAPI_PHONE_NUMBER in env."""
    return os.environ.get("VAPI_PHONE_NUMBER", "").strip()


def vapi_phone_id():
    """Vapi phoneNumberId UUID from the dashboard. Preferred for outbound dial."""
    return os.environ.get("VAPI_PHONE_NUMBER_ID", "").strip()


def build_assistant(system_prompt, base_url, model="gpt-4o-mini"):
    """Assistant payload: prompt assembled by app/agent.py, our six tools
    as server tools hitting BaseURL/vapi/tool."""
    server_url = base_url.rstrip("/") + "/vapi/tool"
    return {
        "name": "autopay-recovery (synthetic demo)",
        "serverUrl": server_url,
        "model": {"provider": "openai", "model": model,
                  "messages": [{"role": "system", "content": system_prompt}]},
        "serverTools": [{"type": "server", "name": name} for name in tool_names],
    }


def start_web_call(system_prompt, base_url, key=None):
    """Start a browser/client call. Returns the provider call object."""
    response = httpx.post(f"{api_base}/call", headers={"Authorization": f"Bearer {key or api_key()}"},
                          json={"type": "webCall", "assistant": build_assistant(system_prompt, base_url)},
                          timeout=30)
    response.raise_for_status()
    return response.json()


def test_destination(explicit=""):
    """Hardcoded test destination. TEST_CALL_TO_NUMBER env wins when set,
    else falls back to the explicitly given number (per-call input)."""
    env = os.environ.get("TEST_CALL_TO_NUMBER", "").strip()
    if env:
        return env
    return (explicit or "").strip()


def start_phone_call(system_prompt, base_url, to_number=None, key=None, confirm=False,
                     from_number=None):
    """Outbound phone. Refuses unless confirm=True with a destination —
    only call numbers you control or have permission for.

    Destination: TEST_CALL_TO_NUMBER env wins when set, else to_number arg.
    Caller id: VAPI_PHONE_NUMBER_ID env preferred, else VAPI_PHONE_NUMBER.
    """
    dest = test_destination(to_number)
    if not confirm or not dest:
        raise vapi_error("phone calls need confirm=True and a destination you control "
                         "(TEST_CALL_TO_NUMBER or explicit to_number)")
    payload = {"type": "outboundPhoneCall",
               "customer": {"number": dest},
               "assistant": build_assistant(system_prompt, base_url)}
    phone_id = vapi_phone_id()
    caller = from_number or vapi_caller_number()
    if phone_id:
        payload["phoneNumberId"] = phone_id
    elif caller:
        payload["phoneNumber"] = caller
    response = httpx.post(f"{api_base}/call", headers={"Authorization": f"Bearer {key or api_key()}"},
                          json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_tool_call(body):
    """Tolerant tool-call parser. Canonical shape: message.toolCalls[0]
    {name/function.name, arguments}. Confirm against current Vapi docs."""
    message = body.get("message", body) if isinstance(body, dict) else {}
    for key in ("toolCalls", "tool_calls"):
        found = message.get(key)
        if found:
            return found[0]
    for key in ("toolCall", "functionCall", "function_call"):
        single = message.get(key)
        if single:
            return single
    return {}


def call_id_of(body):
    """Provider call id -> our internal call row (bound server-side)."""
    message = body.get("message", {}) if isinstance(body.get("message"), dict) else {}
    return (body.get("call", {}) or {}).get("id") or body.get("callId") or (message.get("call", {}) or {}).get("id")
