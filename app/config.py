"""Single home for env settings + business limits.

Runtime enum tuples derive from app.models Literals (typing.get_args), so
models.py stays the only place that lists allowed values.
"""
import os
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


def base_url() -> str:
    """Public origin for pay links (cloudflared tunnel); empty = relative /pay path."""
    return os.environ.get("BASE_URL", "")
