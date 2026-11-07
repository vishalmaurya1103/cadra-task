"""Customer-facing reply templates. Facts and amounts come from the decision."""
from __future__ import annotations

from typing import Any, Dict, List

from .data import norm_email


def _money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _items_text(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "the affected item(s)"
    parts = []
    for item in items:
        qty = int(item.get("quantity") or 1)
        parts.append(f"{qty} x {item.get('product_name', 'item')}")
    return ", ".join(parts)


def _subject(subject: str) -> str:
    text = str(subject or "your request").strip()
    while text.lower().startswith(("re:", "fwd:", "fw:")):
        text = text.split(":", 1)[-1].strip()
    return f"Re: {text}"


def build_reply(
    ticket: Dict[str, Any],
    decision: Dict[str, Any],
    index: Dict[str, Any],
) -> Dict[str, str]:
    sender = norm_email(ticket.get("from") or ticket.get("from_"))
    order_ids = decision.get("_order_ids") or []
    if not order_ids:
        resolved_order = (decision.get("resolved") or {}).get("order_id")
        order_ids = [resolved_order] if resolved_order else []
    order_id = ", ".join(order_ids) if order_ids else "your order"
    unmatched_orders = [
        str(entry.get("order_id"))
        for entry in (decision.get("resolved_orders") or [])
        if not entry.get("matched") and entry.get("order_id")
    ]
    requested_amount = decision.get("_requested_amount")
    outcome = decision.get("outcome")
    reasons = decision.get("reason_codes") or []
    amount = decision.get("amount_usd")
    items = decision.get("_items") or []
    shipment = decision.get("_shipment") or {}
    delivered = decision.get("_delivered_date")
    days = decision.get("_days_since_delivery")
    prior_total = decision.get("_prior_refund_total") or 0.0
    role = decision.get("approver_role")
    status = shipment.get("status")
    carrier = shipment.get("carrier") or "the carrier"
    tracking = shipment.get("tracking_no") or ""
    last_event = shipment.get("last_event_at") or ""

    if outcome == "ESCALATE" and "POLICY_OVERRIDE_ATTEMPT" in reasons:
        body = (
            f"Hello,\n\nWe received your message about order {order_id}. It contains instructions "
            "that conflict with our support policy, so we have passed it to our Trust & Safety "
            "team for review. No refund has been approved. The team will follow up with you "
            "directly.\n\nRegards,\nLarkfield Home Customer Care"
        )
    elif outcome == "ESCALATE" and "LOST_IN_TRANSIT" in reasons:
        body = (
            f"Hello,\n\nWe're sorry your order {order_id} has had no carrier movement for more "
            "than 10 days. We have treated it as lost in transit and escalated it to Carrier "
            f"Claims ({carrier}, tracking {tracking or 'n/a'}). No refund has been proposed at "
            "this stage; the claims team will follow up.\n\nRegards,\nLarkfield Home Customer Care"
        )
    elif outcome == "UNABLE_TO_VERIFY":
        if status == "DELIVERED":
            body = (
                f"Hello,\n\nOur carrier records show order {order_id} was delivered on "
                f"{delivered or 'the recorded delivery date'}, but you have told us it did not "
                "arrive. We cannot resolve that conflict from the data available, so we are not "
                "proposing a refund yet. Our team will review the delivery evidence and follow up."
                "\n\nRegards,\nLarkfield Home Customer Care"
            )
        else:
            body = (
                f"Hello,\n\nWe're checking with the carrier about order {order_id}. We cannot obtain "
                "a reliable carrier status right now, so we cannot verify the delivery position or "
                "propose a refund yet. We will update you once the carrier responds.\n\n"
                "Regards,\nLarkfield Home Customer Care"
            )
    elif outcome == "INVALID_REQUEST" and "ORDER_NOT_FOUND" in reasons:
        body = (
            "Hello,\n\nWe could not find the order you mentioned associated with this email "
            "address, so we cannot action this request. If you have another order number or "
            "used a different email address, please reply with those details.\n\n"
            "Regards,\nLarkfield Home Customer Care"
        )
    elif outcome == "INVALID_REQUEST" and "CUSTOMER_MISMATCH" in reasons:
        body = (
            f"Hello,\n\nWe found order {order_id}, but it is not registered to the email address "
            "on this message, so we cannot action this request from this sender. Please contact "
            "us from the email address on the order.\n\nRegards,\nLarkfield Home Customer Care"
        )
    elif outcome == "REJECT" and "OUT_OF_WINDOW" in reasons:
        body = (
            f"Hello,\n\nWe reviewed order {order_id}. The delivery date on record is {delivered} "
            f"({days} days before your message). Our refund policy allows 30 days from delivery, "
            "or 45 days for damaged or faulty items with a photo. Your request falls outside that "
            "window, so we are unable to approve a refund.\n\n"
            "Regards,\nLarkfield Home Customer Care"
        )
    elif outcome == "REJECT" and "ALREADY_REFUNDED" in reasons:
        body = (
            f"Hello,\n\nOur records show that the affected item(s) on order {order_id} have "
            f"already been refunded ({_money(prior_total)} issued). Our policy does not allow an "
            "issued refund to be repaid, so there is no further amount to refund on this request."
            "\n\nRegards,\nLarkfield Home Customer Care"
        )
    elif outcome == "REJECT":
        body = (
            f"Hello,\n\nWe reviewed order {order_id}. We are unable to approve this request under "
            "our refund policy. Please reply if you have additional information we should consider."
            "\n\nRegards,\nLarkfield Home Customer Care"
        )
    elif outcome == "PEND":
        notes = ""
        if "PARTIAL_LINES" in reasons:
            notes += " The proposal covers the affected item(s) only, not the whole order."
        if prior_total:
            notes += f" We have excluded {_money(prior_total)} that was already refunded on this order."
        if decision.get("asks_shipping"):
            notes += " Shipping charges are not included in an order-line refund."
        if requested_amount and amount and float(requested_amount) > float(amount) + 0.01:
            notes += (
                f" You asked for {_money(requested_amount)}; the order-line value is "
                f"{_money(amount)}, so the proposal is limited to that value."
            )
        if unmatched_orders:
            notes += f" We could not match order(s) {', '.join(unmatched_orders)} to this email address."
        body = (
            f"Hello,\n\nWe reviewed order {order_id}. We have proposed a refund of {_money(amount)} "
            f"for {_items_text(items)}.{notes} Because the proposed amount is above $150, it needs "
            f"approval from a {role or 'Support Lead'} before anything is processed. We will update "
            "you once that review is complete.\n\nRegards,\nLarkfield Home Customer Care"
        )
    elif outcome in ("PROCEED", "PROCEED_MODIFIED") and amount and float(amount) > 0:
        explanation = ""
        if "PARTIAL_LINES" in reasons:
            explanation = " We can only refund the affected item(s), not the whole order."
        if prior_total:
            explanation += (
                f" We have excluded {_money(prior_total)} that was already refunded on this order."
            )
        if decision.get("asks_shipping"):
            explanation += " Shipping charges are not included in an order-line refund."
        if requested_amount and amount and float(requested_amount) > float(amount) + 0.01:
            explanation += (
                f" You asked for {_money(requested_amount)}; the order-line value is "
                f"{_money(amount)}, so the refund is limited to that value."
            )
        if unmatched_orders:
            explanation += (
                f" We could not match order(s) {', '.join(unmatched_orders)} to this email address."
            )
        body = (
            f"Hello,\n\nWe reviewed order {order_id} and can refund {_money(amount)} for "
            f"{_items_text(items)}.{explanation} The refund will be proposed for processing; you "
            "do not need to do anything else.\n\nRegards,\nLarkfield Home Customer Care"
        )
    else:
        if status == "IN_TRANSIT":
            body = (
                f"Hello,\n\nOrder {order_id} is currently in transit with {carrier} "
                f"(tracking {tracking or 'n/a'}). The last carrier update was {last_event or 'not available'}, "
                "which is within our normal delivery window. No action is needed at this time; "
                "please contact us again if tracking does not move for more than 10 days.\n\n"
                "Regards,\nLarkfield Home Customer Care"
            )
        elif status == "DELIVERED":
            body = (
                f"Hello,\n\nOrder {order_id} was delivered on {delivered or 'the recorded delivery date'}. "
                "No further action is needed. If you are asking about a different issue, please reply "
                "with the details.\n\nRegards,\nLarkfield Home Customer Care"
            )
        else:
            body = (
                f"Hello,\n\nWe reviewed order {order_id}. No further action is needed at this time. "
                "Please reply if you need help with anything else.\n\n"
                "Regards,\nLarkfield Home Customer Care"
            )

    return {
        "to": str(ticket.get("from") or ticket.get("from_") or "").strip(),
        "subject": _subject(ticket.get("subject", "")),
        "body": body,
    }
