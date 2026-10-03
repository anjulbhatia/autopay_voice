"""Channel adapters. whatsapp/sms/inapp/razorpay_notify are message templates
with a mock send (real delivery needs business accounts, templates, consent).
`to_label` must already be masked — raw numbers never reach logs here.
"""

templates = {
    "whatsapp": ("Hi {name}, your autopay of {amount} failed. "
                 "Pay securely (link dies in {ttl} min): {url}."),
    "sms": ("Autopay {amount} failed. Pay: {url} (expires in {ttl} min)."),
    "inapp": ("Pay your autopay dues securely: {url} (expires in {ttl} min)."),
    "razorpay_notify": ("Autopay alert for {name}: {amount} failed. "
                        "Pay: {url} (expires in {ttl} min)."),
    "console": ("pay link for {name} ({amount}, {ttl} min): {url}"),
}


def render(channel, name, amount, url, ttl_minutes=10):
    """Fill a channel template. Unknown channels fall back to console."""
    template = templates.get(channel, templates["console"])
    return template.format(name=name, amount=amount, url=url, ttl=ttl_minutes)


def send(channel, to_label, message):
    """Mock send. Returns a receipt; status is always mock, never delivered."""
    return {"channel": channel, "to": to_label, "status": "mock-sent",
            "note": "console only in this demo", "preview": message[:120]}
