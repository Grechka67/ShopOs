from decimal import Decimal

from app.ingestion.loyverse_mapping import (
    ACTIVE,
    REFUNDED,
    VOIDED,
    classify_receipt,
    parse_payments,
    receipt_discount,
)

# Real receipt shapes captured from a live Loyverse account (2026-06-09).
# Payment field is "name", NOT "payment_type_name" — confirmed against the API.
REAL_CASH_RECEIPT = {
    "receipt_number": "1-0001", "receipt_type": "SALE", "cancelled_at": None,
    "refund_for": None, "total_money": 120.0, "total_discount": 0.0,
    "employee_id": "aaaaaaaa-0000-0000-0000-000000000001",
    "payments": [{"name": "Cash", "type": "CASH", "money_amount": 120.0}],
}
REAL_CARD_RECEIPT = {
    "receipt_number": "1-0002", "receipt_type": "SALE", "cancelled_at": None,
    "refund_for": None, "total_money": 10.0, "total_discount": 0.0,
    "employee_id": "aaaaaaaa-0000-0000-0000-000000000001",
    "payments": [{"name": "Card", "type": "NONINTEGRATEDCARD", "money_amount": 10.0}],
}


def test_a_normal_sale_is_active():
    assert classify_receipt({"receipt_type": "SALE"}) == (ACTIVE, None)


def test_a_cancelled_receipt_is_voided():
    assert classify_receipt({"cancelled_at": "2026-06-06T10:00:00Z"}) == (VOIDED, None)


def test_cancellation_wins_over_refund_type():
    # A cancelled REFUND is still a void, not a refund.
    r = {"cancelled_at": "2026-06-06T10:00:00Z", "receipt_type": "REFUND"}
    assert classify_receipt(r) == (VOIDED, None)


def test_a_refund_links_back_to_the_original():
    r = {"receipt_type": "REFUND", "refund_for": "1-1042"}
    assert classify_receipt(r) == (REFUNDED, "1-1042")


def test_a_refund_without_a_reference_still_classifies():
    assert classify_receipt({"receipt_type": "REFUND"}) == (REFUNDED, None)


def test_discount_is_extracted_as_decimal():
    assert receipt_discount({"total_discount": "15.50"}) == Decimal("15.50")


def test_missing_discount_is_zero():
    assert receipt_discount({}) == Decimal("0")


def test_null_discount_is_zero():
    assert receipt_discount({"total_discount": None}) == Decimal("0")


# --- parse_payments (verified against real Loyverse API response 2026-06-09) ---

def test_real_cash_receipt_classifies_as_cash():
    cash, transfer, method = parse_payments(REAL_CASH_RECEIPT)
    assert cash == Decimal("120.0")
    assert transfer == Decimal("0")
    assert method == "cash"


def test_real_card_receipt_classifies_as_mixed():
    # Card (NONINTEGRATEDCARD) is neither "cash" nor "transfer" by name —
    # falls through to "mixed" until the store names a payment type "Transfer".
    cash, transfer, method = parse_payments(REAL_CARD_RECEIPT)
    assert cash == Decimal("0")
    assert transfer == Decimal("0")
    assert method == "mixed"


def test_transfer_payment_classifies_correctly():
    # Store names their PromptPay type "Transfer" — this is the expected setup.
    r = {"payments": [{"name": "Transfer", "money_amount": 155.0}]}
    cash, transfer, method = parse_payments(r)
    assert transfer == Decimal("155.0")
    assert method == "transfer"


def test_payment_type_name_field_does_not_work():
    # Regression: old code used "payment_type_name" which doesn't exist in
    # the real API — this would silently return method="mixed" for all receipts.
    r = {"payments": [{"payment_type_name": "Cash", "money_amount": 50.0}]}
    _, _, method = parse_payments(r)
    assert method == "mixed"  # proves the old field name was wrong
