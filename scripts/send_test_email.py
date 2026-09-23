"""Send one test email with the configured sender (EMAIL_MODE from the environment).

Usage: .venv/bin/python scripts/send_test_email.py you@example.com
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moving_agent.emailer import sender_from_env
from moving_agent.models import EmailDraft

to = sys.argv[1]
draft = EmailDraft(offer_id="test", to=to, subject="Moving agent test email",
                   body="If you can read this, the moving agent can send email.")
result = sender_from_env().send(draft, sender=to)
print("OK" if result.ok else "FAILED", "-", result.detail)
