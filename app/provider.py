"""Vapi wrapper. Web-call mode is the default; real phone calls need an
explicit developer-controlled number plus confirm=True. Needs VAPI_API_KEY.

Vapi's API shape changes — confirm payloads against their current docs
before relying on fields beyond the assistant system prompt + server tools.
"""
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


def start_phone_call(system_prompt, base_url, to_number, key=None, confirm=False):
    """Outbound phone. Refuses unless confirm=True with an explicit number —
    only call numbers you control or have permission for."""
    if not confirm or not to_number:
        raise vapi_error("phone calls need confirm=True and an explicit to_number you control")
    response = httpx.post(f"{api_base}/call", headers={"Authorization": f"Bearer {key or api_key()}"},
                          json={"type": "outboundPhoneCall",
                                "customer": {"number": to_number},
                                "assistant": build_assistant(system_prompt, base_url)},
                          timeout=30)
    response.raise_for_status()
    return response.json()
