"""Orchestrate extraction, policy, reply, trace, persistence and decisions."""
from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .data import load_index
from .extract import extract
from .policy import AUTHORITY_LIMIT, decide
from .reply import build_reply
from .trace import build_trace
from .store import Store, get_store


def _line_amount(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("amount") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _apply_active_refunds(
    decision: Dict[str, Any], store: Optional[Store]
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Suppress lines that already have a PROPOSED/APPROVED REFUND in the ledger."""
    if store is None:
        return decision, None
    order_id = (decision.get("resolved") or {}).get("order_id")
    items = decision.get("_items") or []
    if not order_id or not items:
        return decision, None

    active = store.active_refund_lines(order_id)
    if not active:
        return decision, None

    covered = [item for item in items if str(item.get("line_no")) in active]
    if not covered:
        return decision, None

    remaining = [item for item in items if str(item.get("line_no")) not in active]
    if not remaining:
        # Nothing new to propose: point at the run that already holds the refund.
        run_id = active[str(covered[0].get("line_no"))]
        decision["duplicate_of"] = run_id
        decision["_suppress_refund"] = True
        return decision, run_id

    # Some lines are new: propose only those and re-check authority on the new amount.
    amount = round(sum(_line_amount(item) for item in remaining), 2)
    decision["_items"] = remaining
    decision["amount_usd"] = amount if amount > 0 else None
    reasons = [
        code
        for code in (decision.get("reason_codes") or [])
        if code != "APPROVAL_REQUIRED"
    ]
    if amount > AUTHORITY_LIMIT:
        decision["outcome"] = "PEND"
        decision["human_approval_required"] = True
        decision["approver_role"] = "SUPPORT_LEAD"
        reasons.append("APPROVAL_REQUIRED")
    else:
        decision["human_approval_required"] = False
        decision["approver_role"] = None
        if decision.get("outcome") in ("PEND", "PROCEED_MODIFIED"):
            decision["outcome"] = "PROCEED_MODIFIED"
    decision["reason_codes"] = list(dict.fromkeys(reasons))
    decision["duplicate_of"] = None
    return decision, None


def _ledger_action(run: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    outcome = run.get("outcome")
    amount = run.get("amount_usd")
    if outcome == "ESCALATE":
        return "ESCALATION", None
    if amount and float(amount) > 0 and outcome in ("PROCEED", "PROCEED_MODIFIED", "PEND"):
        return "REFUND", float(amount)
    return "REPLY", None


def process_ticket(
    ticket: Dict[str, Any],
    index: Optional[Dict[str, Any]] = None,
    store: Optional[Store] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    started = time.perf_counter()
    index = index if index is not None else load_index()
    store = store if store is not None else (get_store() if persist else None)

    ex = extract(ticket, index)
    decision = decide(ticket, ex, index)
    decision["asks_shipping"] = bool(ex.get("asks_shipping"))
    if persist and store is not None:
        decision, _ = _apply_active_refunds(decision, store)
    reply = build_reply(ticket, decision, index)

    tools = ["parse_ticket"]
    if ex.get("usage"):
        tools.append("llm_extract")
    tools.extend(
        [
            "lookup_orders",
            "lookup_customers",
            "lookup_shipments",
            "lookup_order_items",
            "lookup_refunds",
        ]
    )
    trace = build_trace(ex, tools)

    now = dt.datetime.now(dt.timezone.utc)
    run_id = "run_" + uuid.uuid4().hex[:16]
    run: Dict[str, Any] = {
        "ticket_id": str(ticket.get("ticket_id") or ""),
        "run_id": run_id,
        "ticket_type": decision.get("ticket_type") or ex.get("ticket_type") or "OTHER",
        "resolved": decision.get("resolved") or {"order_id": None, "customer_id": None},
        "resolved_orders": decision.get("resolved_orders"),
        "outcome": decision.get("outcome"),
        "reason_codes": decision.get("reason_codes") or [],
        "checks": decision.get("checks") or [],
        "human_approval_required": bool(decision.get("human_approval_required")),
        "approver_role": decision.get("approver_role"),
        "draft_reply": reply,
        "trace": trace,
        "amount_usd": decision.get("amount_usd"),
        "run_status": "PENDING_APPROVAL"
        if decision.get("human_approval_required")
        else "COMPLETE",
        "duplicate_of": decision.get("duplicate_of"),
        "decision": None,
        "latency_ms": None,
    }
    run["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)

    if persist and store is not None:
        store.save_run(run, "", now.isoformat())
        if not decision.get("_suppress_refund"):
            action, amount = _ledger_action(run)
            if action:
                order_id = (run.get("resolved") or {}).get("order_id")
                lines = []
                if action == "REFUND":
                    lines = [
                        {
                            "order_id": item.get("order_id") or order_id,
                            "line_no": str(item.get("line_no")),
                            "amount": _line_amount(item),
                        }
                        for item in (decision.get("_items") or [])
                    ]
                store.add_ledger(
                    run_id=run_id,
                    ticket_id=run["ticket_id"],
                    action=action,
                    amount_usd=amount,
                    status="PROPOSED",
                    recorded_at=now.isoformat(),
                    order_id=order_id if action == "REFUND" else None,
                    lines=lines,
                )

    return run


def apply_decision(
    run_id: str,
    decision: str,
    approver: str,
    store: Optional[Store] = None,
) -> Optional[Dict[str, Any]]:
    store = store if store is not None else get_store()
    run = store.get_run(run_id)
    if run is None:
        return None

    existing = store.get_decision(run_id)
    if existing:
        # Idempotent: an action is recorded once whatever number of decisions arrive.
        return run

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    inserted = store.record_decision(run_id, decision, approver, now)
    if inserted:
        status = "APPROVED" if decision == "approve" else "REJECTED"
        store.set_ledger_status(run_id, status)
        run["decision"] = {
            "decision": decision,
            "approver": approver,
            "decided_at": now,
        }
        run["run_status"] = status
        run["human_approval_required"] = False
        store.update_run(run)
    return run
