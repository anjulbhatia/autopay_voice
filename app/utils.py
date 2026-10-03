import html
import json


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def mask_phone(phone: str | None) -> str:
    if not phone or len(phone) < 4:
        return "****"
    return f"****-**{phone[-4:]}"


def parse_reasons(raw) -> list:
    """Score-reasons JSON column -> list of strings. Never raises."""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    return [str(i) for i in items] if isinstance(items, list) else []
