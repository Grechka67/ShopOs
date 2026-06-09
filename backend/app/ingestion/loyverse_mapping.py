"""Pure Loyverse-receipt field mapping — no DB, so it is unit-testable.

The poll in loyverse.py does the HTTP + SQL (employee/shift lookups need the
database); the field-level decisions that *don't* need the DB live here, where
they can be tested against captured receipt JSON in isolation — the same split
as reconciliation/matching.py and anomaly/scoring.py.

Field names follow Loyverse's Receipts API and are unverified against a real
store account — validate them against one real receipt before going live.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

VOIDED = "voided"
REFUNDED = "refunded"
ACTIVE = "active"


def classify_receipt(receipt: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Return (void_status, refund_of_id) for a Loyverse receipt.

    Loyverse marks a void with `cancelled_at`; a refund is a separate
    REFUND-type receipt pointing back to the original via `refund_for`.
    A cancelled receipt is treated as a void even if it is a refund.
    """
    if receipt.get("cancelled_at"):
        return VOIDED, None
    if str(receipt.get("receipt_type", "")).upper() == "REFUND":
        refund_for = receipt.get("refund_for")
        return REFUNDED, str(refund_for) if refund_for else None
    return ACTIVE, None


def receipt_discount(receipt: dict[str, Any]) -> Decimal:
    """Total receipt-level discount, as a non-null Decimal."""
    return Decimal(str(receipt.get("total_discount", 0) or 0))


def parse_payments(receipt: dict[str, Any]) -> tuple[Decimal, Decimal, str]:
    """Return (cash_amount, transfer_amount, method) from a Loyverse receipt.

    Loyverse payment objects use the key 'name' (e.g. 'Cash', 'Transfer').
    Stores should name their PromptPay/bank-transfer type with 'transfer'
    in the name for this detection to work.
    """
    payments = receipt.get("payments", [])
    cash = sum(
        Decimal(str(p.get("money_amount", 0)))
        for p in payments if p.get("name", "").lower() == "cash"
    )
    transfer = sum(
        Decimal(str(p.get("money_amount", 0)))
        for p in payments if "transfer" in p.get("name", "").lower()
    )
    if cash and not transfer:
        method = "cash"
    elif transfer and not cash:
        method = "transfer"
    else:
        method = "mixed"
    return cash, transfer, method


def payment_method(receipt: dict[str, Any]) -> str:
    """Convenience wrapper returning only the method string."""
    _, _, method = parse_payments(receipt)
    return method
