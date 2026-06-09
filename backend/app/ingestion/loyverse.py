"""Loyverse POS poll — pulls receipts since last cursor every N seconds."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import logging
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.db import session_scope
from app.ingestion.loyverse_mapping import classify_receipt, parse_payments, receipt_discount
from app.models import Employee, Event, PosTransaction, Shift

log = logging.getLogger("ot.ingest.loyverse")
LOYVERSE_BASE = "https://api.loyverse.com/v1.0"
BKK = ZoneInfo("Asia/Bangkok")


def poll_loyverse_receipts() -> None:
    s = get_settings()
    if not s.loyverse_api_token:
        log.debug("LOYVERSE_API_TOKEN not set — skipping poll")
        return

    since = datetime.now(BKK) - timedelta(seconds=s.loyverse_poll_interval_seconds * 3)
    headers = {"Authorization": f"Bearer {s.loyverse_api_token}"}
    params = {"updated_at_min": since.isoformat()}

    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(f"{LOYVERSE_BASE}/receipts", headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("Loyverse poll failed: %s", e)
        return

    receipts = data.get("receipts", [])
    if not receipts:
        return

    inserted = 0
    with session_scope() as session:
        for r in receipts:
            idem = sha256(f"loyverse|{r.get('receipt_number') or r.get('id')}".encode()).hexdigest()
            existing = session.query(Event).filter_by(idempotency_key=idem).first()
            if existing:
                continue
            event = Event(
                source="loyverse",
                event_type="receipt.upsert",
                payload=r,
                received_at=datetime.now(BKK),
                source_timestamp=datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                    if r.get("created_at") else None,
                source_id=str(r.get("receipt_number") or r.get("id")),
                idempotency_key=idem,
            )
            session.add(event)
            session.flush()

            cash_amount, transfer_amount, method = parse_payments(r)

            ts = event.source_timestamp or datetime.now(BKK)

            # Map Loyverse's employee UUID to our internal employee, then find the
            # shift that employee was on when the receipt was rung up. Without these,
            # cash reconciliation (groups by shift_id) and the per-employee anomaly
            # rules have nothing to work with.
            employee_id = None
            shift_id = None
            if r.get("employee_id"):
                emp = session.query(Employee).filter_by(
                    loyverse_employee_id=str(r["employee_id"])
                ).first()
                if emp:
                    employee_id = emp.id
                    shift = (
                        session.query(Shift)
                        .filter(Shift.employee_ids.any(emp.id))
                        .filter(Shift.scheduled_start <= ts)
                        .filter(Shift.scheduled_end >= ts)
                        .first()
                    )
                    if shift:
                        shift_id = shift.id

            void_status, refund_of_id = classify_receipt(r)
            discount_amount = receipt_discount(r)

            pos_tx = PosTransaction(
                receipt_id=str(r.get("receipt_number") or r["id"]),
                timestamp=ts,
                total=Decimal(str(r.get("total_money", 0))),
                cash_amount=cash_amount,
                transfer_amount=transfer_amount,
                payment_method=method,
                employee_id=employee_id,
                shift_id=shift_id,
                void_status=void_status,
                refund_of_id=refund_of_id,
                discount_amount=discount_amount,
                line_items=r.get("line_items", []),
                raw_event_id=event.id,
            )
            session.merge(pos_tx)
            inserted += 1

    if inserted:
        log.info("Ingested %d Loyverse receipts", inserted)
