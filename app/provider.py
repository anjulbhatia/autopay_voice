"""Vapi wrapper. Web-call mode is the default; real phone calls need an
explicit developer-controlled number plus confirm=True. Needs VAPI_API_KEY.

Vapi's API shape changes — confirm payloads against their current docs
before relying on fields beyond the assistant system prompt + server tools.
"""
import os

import httpx

api_base = "https://api.vapi.ai"

# Vapi phone number — your taken Vapi number (display/caller id side). Set VAPI_PHONE_NUMBER in env.
vapi_phone_number = os.environ.get("VAPI_PHONE_NUMBER", "").strip()
# Optional Vapi phoneNumberId (UUID from dashboard). Preferred for outbound dial when set.
vapi_phone_number_id = os.environ.get("VAPI_PHONE_NUMBER_ID", "").strip()

tool_names = ["verify_identity", "get_failed_payment", "send_payment_link",
              "schedule_retry", "request_human_handoff", "log_outcome"]


class vapi_error(Exception):
    pass


def api_key():
    key = os.environ.get("VAPI_API_KEY", "").strip().strip('"')
    if not key or key == "YOUR_VAPI_API_KEY":
        raise vapi_error("set a real VAPI_API_KEY (see .env.example)")
    return key


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
    phone_id = os.environ.get("VAPI_PHONE_NUMBER_ID", "").strip() or vapi_phone_number_id
    caller = from_number or vapi_phone_number or os.environ.get("VAPI_PHONE_NUMBER", "").strip()
    if phone_id:
        payload["phoneNumberId"] = phone_id
    elif caller:
        payload["phoneNumber"] = caller
    response = httpx.post(f"{api_base}/call", headers={"Authorization": f"Bearer {key or api_key()}"},
                          json=payload, timeout=30)
    response.raise_for_status()
    return response.json()
