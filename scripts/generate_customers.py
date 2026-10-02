"""Generate 10 synthetic customers (exactly 4 female). Run: uv run python scripts/generate_customers.py

Seed-file conventions (all synthetic, per brief):
- phone numbers are OBVIOUSLY fake sequential: +91-90000-00001 ... 00010.
  Replace one with your own test number before any real call.
- security answers stored as salted sha256 hash only, never plaintext.
- past_call_notes must only enter the agent prompt AFTER verification.
- timestamps are UTC ISO; convert to local only for calling-hour checks.
"""
import hashlib
import json
import random
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from faker import Faker

Faker.seed(42)
random.seed(42)
fake = Faker("en_IN")

female_positions = {1, 4, 7, 10}  # 4 of 10

now = datetime.now(timezone.utc)
failure_reasons = ["insufficient_balance", "mandate_expired", "bank_decline"]
questions = [
    ("What is your year of birth?", lambda phone: str(random.randint(1965, 2002))),
    ("What is your registered PIN code?", lambda phone: f"{random.randint(110001, 999999)}"),
    ("What are the last 4 digits of your registered mobile number?", lambda phone: phone[-4:]),
]

customers = []
for i in range(1, 11):
    name = fake.name_female() if i in female_positions else fake.name_male()
    phone = f"+91-90000-000{i:02d}"  # obviously fake, sequential
    last_call = now - timedelta(days=random.choice([2, 3, 5, 8, 15, 30, 45]),
                                 hours=random.randint(0, 12))
    last_msg = now - timedelta(hours=random.choice([5, 30, 50, 100, 200]))
    amount = round(random.choice([499, 799, 999, 1499, 1999, 2999, 4999, 9999])
                   + random.random() * 100, 2)
    q, ans_fn = random.choice(questions)
    answer = ans_fn(phone)
    salt = secrets.token_hex(8)
    answer_hash = hashlib.sha256((salt + answer).encode()).hexdigest()
    customers.append({
        "customer_id": f"CUST{i:03d}",
        "name": name,
        "gender": "female" if i in female_positions else "male",
        "phone": phone,
        "amount_due": amount,
        "due_date": (now + timedelta(days=random.randint(-10, 15))).date().isoformat(),
        "failure_reason": random.choice(failure_reasons),
        "payment_status": "failed",
        "default_history": random.randint(0, 3),
        "last_call_at": last_call.isoformat(),
        "last_message_at": last_msg.isoformat(),
        "attempts_total": random.randint(0, 4),
        "past_call_notes": random.choice([
            "No prior contact; first autopay failure.",
            "Prior call: customer asked for retry next week, then missed.",
            "Prior call: insufficient balance, promised to recharge.",
            "Prior call: requested payment link over SMS, link expired unused.",
            "Two prior attempts, went to voicemail once.",
        ]),
        "security_question": q,
        "security_answer_hash": answer_hash,
        "security_salt": salt,
        "do_not_call": 1 if i == 9 else 0,
        "synthetic": True,
    })

out = Path(__file__).resolve().parent.parent / "data" / "customers.json"
out.write_text(json.dumps(customers, indent=2, ensure_ascii=False), encoding="utf-8")
females = sum(1 for c in customers if c["gender"] == "female")
print(f"Wrote {len(customers)} synthetic customers ({females} female) to {out}")
