"""Orchestrate extraction, policy, reply, trace, persistence and decisions."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from .data import load_index, norm_email
from .extract import extract
from .policy import decide
from .reply import build_reply
from .trace import build_trace
from .store import Store, get_store


def _fingerprint(ticket: Dict[str, Any], ex: Dict[str, Any], index: Dict[str, Any]) -> str:
    sender = norm_email(ticket.get("from") or ticket.get("from_"))
    order = None
    for candidate in ex.get("order_ids", []):
        if candidate in index.get("orders", {}):
            order = candidate
            break
    if order is None and ex.get("order_ids"):
        order = ex["order_ids"][0]
    body = re.sub(r"\s+", " ", str(ticket.get("body") or "").strip().lower())
    intent = str(ex.get("ticket_type") or "")
    raw = f"{sender}|{order}|{body}|{intent}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        "duplicate_of": None,
        "decision": None,
        "latency_ms": None,
    }
    run["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)

    if persist and store is not None:
        fingerprint = _fingerprint(ticket, ex, index)
        original = store.find_by_fingerprint(fingerprint)
        if original:
            # Forwarded duplicate: own run, duplicate_of, no second ledger action.
            run["duplicate_of"] = original.get("run_id")
            run["run_status"] = original.get("run_status", "COMPLETE")
            store.save_run(run, fingerprint, now.isoformat())
        else:
            store.save_run(run, fingerprint, now.isoformat())
            action, amount = _ledger_action(run)
            if action:
                store.add_ledger(
                    run_id=run_id,
                    ticket_id=run["ticket_id"],
                    action=action,
                    amount_usd=amount,
                    status="PROPOSED",
                    recorded_at=now.isoformat(),
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
        store.update_run(run)
    return run
