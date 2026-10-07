"""Env settings + business limits. Allowed values derive from app.models."""
from typing import get_args

from app.models import CallOutcome, LinkChannel, LinkKind

# Outreach caps (AGENT.md section 5 + guardrail G4)
DAILY_CAP = 2
TOTAL_CAP = 5
COOLDOWN_HOURS = 24

# Calling hours, customer-local wall clock (IST; seed numbers are +91)
CALL_OPEN_HOUR = 9
CALL_CLOSE_HOUR = 21

# Payment links
TOKEN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/O/1/l/I
TOKEN_LENGTH = 6
DEFAULT_TTL_MINUTES = 10
MIN_LINK_TTL = 1
MAX_LINK_TTL = 1440

# Identity verification
MAX_VERIFY_ATTEMPTS = 2

# Allowed values (from models.py Literals — do not re-list here)
VALID_LINK_KINDS = get_args(LinkKind)
VALID_LINK_CHANNELS = get_args(LinkChannel)
VALID_CALL_OUTCOMES = get_args(CallOutcome)

# Local ports: merchant console + voice webhooks on 8000, customer pay
# page on 8800. Same SQLite underneath; no tunnel, no BASE_URL env.
CONSOLE_PORT = 8000
PAY_PORT = 8800


def console_base() -> str:
    """Origin serving /console, /partials/*, /vapi/*."""
    return f"http://127.0.0.1:{CONSOLE_PORT}"


def pay_base() -> str:
    """Origin serving /pay/* links."""
    return f"http://127.0.0.1:{PAY_PORT}"
