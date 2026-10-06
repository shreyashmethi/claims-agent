"""Signed approval tokens. A write tool executes only with a token bound to (claim, action, amount)."""
from __future__ import annotations

import hashlib
import hmac

from app.config import APPROVAL_SECRET


def _msg(approval_id: str, claim_id: str, action: str, amount: float) -> bytes:
    return f"{approval_id}|{claim_id}|{action}|{round(float(amount), 2):.2f}".encode()


def sign(approval_id: str, claim_id: str, action: str, amount: float) -> str:
    sig = hmac.new(APPROVAL_SECRET, _msg(approval_id, claim_id, action, amount), hashlib.sha256).hexdigest()
    return f"{approval_id}.{sig[:32]}"


def verify(token: str | None, claim_id: str, action: str, amount: float) -> bool:
    if not token or "." not in token:
        return False
    approval_id = token.split(".", 1)[0]
    return hmac.compare_digest(token, sign(approval_id, claim_id, action, amount))
