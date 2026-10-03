"""Prompt assembly + call decisions. Pure functions over agent/*.md.

Verification stays mandatory before disclosure (enforced in tools.py).
'Suspicious' here means: exhausted tries, possible third party, or anomaly —
those get the generic message and an early human, never details.
"""
from pathlib import Path

agent_dir = Path(__file__).resolve().parent.parent / "agent"

tier_budget = {"short": 90, "standard": 180, "extended": 300}
tier_handoff_at = {"short": None, "standard": 150, "extended": 120}
tier_empathy = {
    "short": "brisk and warm, one question at a time",
    "standard": "patient, explain once, offer two options",
    "extended": "slow, extra patience, offer a human by minute 2",
}

failure_plain = {
    "insufficient_balance": "there were not enough funds when autopay was tried",
    "mandate_expired": "the autopay permission expired and needs renewal",
    "bank_decline": "the bank declined the autopay attempt",
    "other": "the autopay attempt did not go through",
}


def load_texts():
    return {name: (agent_dir / f"{name}.md").read_text(encoding="utf-8")
            for name in ("prompt", "policies", "guardrails")}


def plain_reason(failure_reason):
    return failure_plain.get(failure_reason or "other", failure_plain["other"])


def assemble_prompt(customer, tier, verified, reasons=()):
    """System prompt for one call. Pre-verify context carries NO amount,
    dates, phone digits, or call notes — the model cannot leak what it
    does not have. Amounts/notes appear only after verification."""
    texts = load_texts()
    name = customer["name"].split()[0]
    context = [
        f"customer first name: {name}",
        f"tier: {tier} ({tier_empathy[tier]})",
        f"failure in plain words: {plain_reason(customer['failure_reason'])}",
    ]
    if verified:
        context += [
            f"amount_due: {customer['amount_due']}",
            f"due_date: {customer['due_date']}",
            f"past call notes: {customer['past_call_notes'] or 'none'}",
        ]
    context.append("ranking reasons: " + ("; ".join(reasons) if reasons else "n/a"))
    return "\n\n".join([
        texts["prompt"], texts["policies"], texts["guardrails"],
        "## CALL CONTEXT (this call only)\n" + "\n".join(f"- {line}" for line in context),
        "## ANTI-HALLUCINATION\n"
        "- Use ONLY facts in CALL CONTEXT and tool results. Never invent amounts,"
        " dates, names, or link contents.\n"
        "- If a fact is missing, say you will check and use request_human_handoff"
        " rather than guessing.",
    ])


def call_plan(tier):
    """Budget + tone for a tier. Low propensity: more time, earlier human."""
    return {"tier": tier, "max_seconds": tier_budget[tier],
            "offer_human_at_seconds": tier_handoff_at[tier],
            "empathy": tier_empathy[tier]}


def suspicious(call):
    """True when the caller looks unverifiable: tries exhausted without
    success, or a wrong-person outcome. Caller gets the generic message."""
    if call is None:
        return True
    if call["outcome"] == "wrong_person":
        return True
    return not call["verified"] and call["verify_attempts"] >= 2
