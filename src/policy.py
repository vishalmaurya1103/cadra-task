"""Deterministic policy engine for RP-3.2."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from .data import norm_email, parse_datetime

AUTHORITY_LIMIT = 150.0
LOST_DAYS = 10
PHOTO_WINDOW_DAYS = 45
ORDINARY_WINDOW_DAYS = 30


def _ev(source: str, ref: str, value: Any) -> Dict[str, str]:
    return {"source": source, "ref": str(ref), "value": str(value)}


def _add(
    checks: List[Dict[str, Any]],
    check_id: str,
    result: str,
    clause: str,
    evidence: Optional[List[Dict[str, str]]] = None,
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "result": result,
            "policy_clause": clause,
            "evidence": evidence or [],
        }
    )


def _dedupe(values: List[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _base_result(ticket: Dict[str, Any], ex: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ticket_type": ex.get("ticket_type", "OTHER"),
        "resolved": {"order_id": None, "customer_id": None},
        "outcome": "UNABLE_TO_VERIFY",
        "reason_codes": [],
        "checks": [],
        "human_approval_required": False,
        "approver_role": None,
        "amount_usd": None,
        "_items": [],
        "_shipment": None,
        "_delivered_date": None,
        "_days_since_delivery": None,
        "_prior_refund_total": 0.0,
        "_sender": norm_email(ticket.get("from") or ticket.get("from_")),
    }


def decide(ticket: Dict[str, Any], ex: Dict[str, Any], index: Dict[str, Any]) -> Dict[str, Any]:
    result = _base_result(ticket, ex)
    checks: List[Dict[str, Any]] = result["checks"]
    reasons: List[str] = result["reason_codes"]

    received = parse_datetime(ticket.get("received_at")) or dt.datetime.utcnow()
    orders_index = index.get("orders", {})
    customers_by_email = index.get("customers_by_email", {})

    order_id: Optional[str] = None
    order: Optional[Dict[str, Any]] = None
    for candidate in ex.get("order_ids", []):
        if candidate in orders_index:
            order_id = candidate
            order = orders_index[candidate]
            break

    sender = result["_sender"]
    customer = customers_by_email.get(sender)
    customer_id = customer.get("customer_id") if customer else None
    email_matches = bool(order and customer and order.get("customer_id") == customer_id)

    # 5.2 policy override is checked first; no refund is ever proposed.
    _add(
        checks,
        "policy_override",
        "FAIL" if ex.get("injection") else "PASS",
        "5.2",
        [_ev("ticket", ticket.get("ticket_id", ""), "override attempt")] if ex.get("injection") else [],
    )
    if ex.get("injection"):
        if email_matches:
            result["resolved"] = {"order_id": order_id, "customer_id": customer_id}
        result.update(
            outcome="ESCALATE",
            reason_codes=["POLICY_OVERRIDE_ATTEMPT"],
            human_approval_required=True,
            approver_role="TRUST_SAFETY",
        )
        return result

    # 4.1 identification.
    _add(
        checks,
        "order_identified",
        "PASS" if order else "FAIL",
        "4.1",
        [_ev("data/orders.csv", order_id or "none", "found" if order else "not found")],
    )
    _add(
        checks,
        "customer_match",
        "PASS" if email_matches else "FAIL",
        "4.1",
        [
            _ev(
                "data/customers.csv",
                order_id or "none",
                f"sender={sender}; customer={customer_id or 'not found'}",
            )
        ],
    )
    if order is None:
        result.update(outcome="INVALID_REQUEST", reason_codes=["ORDER_NOT_FOUND"])
        return result
    if not email_matches:
        result.update(outcome="INVALID_REQUEST", reason_codes=["CUSTOMER_MISMATCH"])
        return result

    result["resolved"] = {"order_id": order_id, "customer_id": customer_id}

    shipment = index.get("shipments", {}).get(order_id)
    result["_shipment"] = shipment
    if not shipment:
        _add(checks, "carrier_status", "UNABLE_TO_VERIFY", "4.2")
        result.update(outcome="UNABLE_TO_VERIFY", reason_codes=["CARRIER_STATUS_UNAVAILABLE"])
        return result

    status = shipment.get("status")
    if status == "UNAVAILABLE":
        _add(checks, "carrier_status", "UNABLE_TO_VERIFY", "4.2", [_ev("data/shipments.csv", order_id, status)])
        _add(checks, "delivery_date", "UNABLE_TO_VERIFY", "2.1")
        _add(checks, "return_window", "NOT_APPLICABLE", "2.1,2.3,2.6")
        result.update(outcome="UNABLE_TO_VERIFY", reason_codes=["CARRIER_STATUS_UNAVAILABLE"])
        return result

    _add(checks, "carrier_status", "PASS", "4.2", [_ev("data/shipments.csv", order_id, status)])

    if status == "IN_TRANSIT":
        _add(checks, "delivery_date", "NOT_APPLICABLE", "2.1")
        anchor_raw = shipment.get("last_event_at") or shipment.get("shipped_at")
        anchor = parse_datetime(anchor_raw)
        if anchor is None:
            _add(checks, "lost_in_transit", "UNABLE_TO_VERIFY", "5.1")
            result.update(outcome="UNABLE_TO_VERIFY", reason_codes=["CARRIER_STATUS_UNAVAILABLE"])
            return result
        gap_days = (received.date() - anchor.date()).days
        lost = gap_days > LOST_DAYS
        _add(
            checks,
            "lost_in_transit",
            "PASS" if lost else "FAIL",
            "5.1",
            [_ev("data/shipments.csv", order_id, f"last_event={anchor_raw}; gap_days={gap_days}")],
        )
        _add(checks, "return_window", "NOT_APPLICABLE", "2.1,2.3,2.6")
        if lost:
            result.update(
                outcome="ESCALATE",
                reason_codes=["LOST_IN_TRANSIT"],
                human_approval_required=True,
                approver_role="CARRIER_CLAIMS",
                _days_since_delivery=None,
            )
            return result
        # Moving normally: answer the status question, propose no action.
        result.update(outcome="PROCEED", reason_codes=[], human_approval_required=False, approver_role=None)
        return result

    delivered_raw = shipment.get("delivered_at")
    delivered = parse_datetime(delivered_raw)
    if delivered is None:
        _add(checks, "delivery_date", "UNABLE_TO_VERIFY", "2.1")
        result.update(outcome="UNABLE_TO_VERIFY", reason_codes=["CARRIER_STATUS_UNAVAILABLE"])
        return result
    days = (received.date() - delivered.date()).days
    result["_delivered_date"] = delivered.date().isoformat()
    result["_days_since_delivery"] = days
    _add(
        checks,
        "delivery_date",
        "PASS",
        "2.1",
        [_ev("data/shipments.csv", order_id, f"delivered_at={delivered.date().isoformat()}; days={days}")],
    )

    damage_or_fault = bool(ex.get("damage_or_fault"))
    wrong_or_not_described = bool(ex.get("wrong_or_not_described"))
    photo = bool(ex.get("photo"))
    defect = damage_or_fault or wrong_or_not_described

    # A carrier delivery scan contradicts a pure non-delivery claim; the fact
    # needed to decide receipt cannot be established from the data room.
    if (
        status == "DELIVERED"
        and ex.get("delivery_issue")
        and not damage_or_fault
        and not wrong_or_not_described
        and not ex.get("asks_return")
    ):
        _add(
            checks,
            "delivery_status_vs_claim",
            "FAIL",
            "4.2",
            [_ev("data/shipments.csv", order_id, f"carrier={status}; customer_claims_non_delivery")],
        )
        result.update(outcome="UNABLE_TO_VERIFY", reason_codes=[])
        return result

    if days <= ORDINARY_WINDOW_DAYS:
        in_window = True
        window_clause = "2.1"
    elif days <= PHOTO_WINDOW_DAYS and damage_or_fault and photo:
        in_window = True
        window_clause = "2.6"
        reasons.append("DAMAGE_EXCEPTION")
    else:
        in_window = False
        window_clause = "2.3,2.6"

    _add(
        checks,
        "return_window",
        "PASS" if in_window else "FAIL",
        window_clause,
        [_ev("data/shipments.csv", order_id, f"days_since_delivery={days}; photo={photo}")],
    )
    if not in_window:
        result.update(outcome="REJECT", reason_codes=_dedupe(reasons + ["OUT_OF_WINDOW"]))
        return result

    if damage_or_fault:
        _add(
            checks,
            "damage_evidence",
            "PASS",
            "2.2,2.6",
            [_ev("ticket", ticket.get("ticket_id", ""), f"damage_or_fault; photo={photo}")],
        )
    elif wrong_or_not_described:
        _add(
            checks,
            "damage_evidence",
            "PASS",
            "2.2,2.6",
            [_ev("ticket", ticket.get("ticket_id", ""), "wrong_or_not_as_described")],
        )
    else:
        _add(checks, "damage_evidence", "NOT_APPLICABLE", "2.2,2.6")

    # Information-only requests do not need an action.
    asks_refund = bool(ex.get("asks_refund"))
    asks_return = bool(ex.get("asks_return"))
    if not defect and not asks_refund and not asks_return and not ex.get("asks_shipping"):
        _add(checks, "affected_quantity", "NOT_APPLICABLE", "2.2,2.5")
        _add(checks, "prior_refunds", "NOT_APPLICABLE", "2.5")
        _add(checks, "authority", "NOT_APPLICABLE", "3.1,3.2")
        result.update(outcome="PROCEED", reason_codes=[], human_approval_required=False, approver_role=None)
        return result

    items = index.get("items", {}).get(order_id, [])
    if not items:
        _add(checks, "affected_quantity", "UNABLE_TO_VERIFY", "2.2,2.5")
        result.update(outcome="UNABLE_TO_VERIFY", reason_codes=[])
        return result

    affected: List[Tuple[Dict[str, Any], int]] = []
    by_line = {str(item["line_no"]): item for item in items}
    for product in ex.get("products", []):
        line = by_line.get(str(product.get("line_no")))
        if line is None:
            continue
        ordered_qty = int(line.get("qty") or 1)
        qty = int(product.get("quantity") or ordered_qty)
        qty = max(1, min(qty, ordered_qty))
        if any(existing["line_no"] == line["line_no"] for existing, _ in affected):
            continue
        affected.append((line, qty))

    if not affected and len(items) == 1:
        line = items[0]
        affected = [(line, int(line.get("qty") or 1))]

    if not affected:
        _add(checks, "affected_quantity", "UNABLE_TO_VERIFY", "2.2,2.5")
        result.update(outcome="UNABLE_TO_VERIFY", reason_codes=[])
        return result

    prior_by_line = index.get("refunded_by_line", {}).get(order_id, {})
    amount = 0.0
    prior_total = 0.0
    fully_refunded_line = False
    payable_items: List[Dict[str, Any]] = []
    for line, qty in affected:
        gross = round(qty * float(line.get("unit_price") or 0.0), 2)
        prior = round(float(prior_by_line.get(str(line["line_no"]), 0.0)), 2)
        prior_total += prior
        net = round(max(0.0, gross - min(prior, gross)), 2)
        amount += net
        if prior >= gross > 0:
            fully_refunded_line = True
        if net > 0:
            payable_items.append(
                {
                    "line_no": line["line_no"],
                    "product_name": line["product_name"],
                    "quantity": qty,
                    "unit_price": line["unit_price"],
                }
            )

    amount = round(amount, 2)
    prior_total = round(prior_total, 2)
    result["_items"] = payable_items
    result["_prior_refund_total"] = prior_total
    result["amount_usd"] = amount if amount > 0 else None

    _add(
        checks,
        "affected_quantity",
        "PASS",
        "2.2,2.5",
        [
            _ev(
                "data/order_items.csv",
                order_id,
                "; ".join(f"line {line['line_no']} qty {qty}/{line['qty']}" for line, qty in affected),
            )
        ],
    )
    _add(
        checks,
        "prior_refunds",
        "PASS" if prior_total else "NOT_APPLICABLE",
        "2.5",
        [_ev("data/refunds.csv", order_id, f"issued_total={prior_total:.2f}")]
        if prior_total
        else [],
    )

    if fully_refunded_line:
        reasons.append("ALREADY_REFUNDED")

    if amount <= 0:
        result.update(
            outcome="REJECT",
            reason_codes=_dedupe(reasons + (["ALREADY_REFUNDED"] if prior_total else [])),
            human_approval_required=False,
            approver_role=None,
        )
        return result

    modified = False
    if ex.get("whole_order") and len(affected) < len(items):
        modified = True
        reasons.append("PARTIAL_LINES")
        _add(
            checks,
            "partial_lines",
            "PASS",
            "2.4",
            [_ev("ticket", ticket.get("ticket_id", ""), f"affected={len(affected)}; order_lines={len(items)}")],
        )
    else:
        _add(checks, "partial_lines", "NOT_APPLICABLE", "2.4")

    if ex.get("asks_shipping"):
        modified = True
    if prior_total:
        modified = True
    if any(qty < int(line.get("qty") or 1) for line, qty in affected):
        modified = True

    if amount > AUTHORITY_LIMIT:
        _add(
            checks,
            "authority",
            "FAIL",
            "3.2",
            [_ev("proposal", order_id, f"amount={amount:.2f}; limit={AUTHORITY_LIMIT:.2f}")],
        )
        reasons.append("APPROVAL_REQUIRED")
        result.update(
            outcome="PEND",
            reason_codes=_dedupe(reasons),
            human_approval_required=True,
            approver_role="SUPPORT_LEAD",
        )
    else:
        _add(
            checks,
            "authority",
            "PASS",
            "3.1",
            [_ev("proposal", order_id, f"amount={amount:.2f}; limit={AUTHORITY_LIMIT:.2f}")],
        )
        result.update(
            outcome="PROCEED_MODIFIED" if modified else "PROCEED",
            reason_codes=_dedupe(reasons),
            human_approval_required=False,
            approver_role=None,
        )

    return result
