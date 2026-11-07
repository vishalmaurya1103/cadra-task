"""Evaluation runner.

Pass rule: outcome, human_approval_required and approver_role exact; proposed amount
within $0.01 when expected; reason_codes is a superset of the expected codes.
Public tickets are smoke-checked only and never enter the pass rate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Evaluation is deterministic and offline by default. Pass --use-llm to exercise the
# configured provider from .env instead.
if "--use-llm" not in sys.argv:
    os.environ["LLM_API_KEY"] = ""

from src.data import find_data_dir, load_index
from src.service import apply_decision, process_ticket
from src.store import Store


def _data_dir() -> Path:
    return find_data_dir()


def load_inbox() -> Dict[str, Dict[str, Any]]:
    data_dir = _data_dir()
    return {
        ticket["ticket_id"]: ticket
        for ticket in json.load((data_dir / "tickets" / "support_inbox_export.json").open())
    }


def load_labelled_cases() -> List[Dict[str, Any]]:
    data_dir = _data_dir()
    inbox = load_inbox()
    labels = json.load((data_dir / "tickets" / "eval_seed_labelled.json").open())
    cases: List[Dict[str, Any]] = []
    for label in labels:
        ticket = inbox.get(label["ticket_id"])
        if not ticket:
            continue
        cases.append(
            {
                "source": "labelled",
                "ticket": ticket,
                "expected": {
                    "outcome": label.get("expected_outcome"),
                    "human_approval_required": label.get(
                        "expected_human_approval_required"
                    ),
                    "approver_role": label.get("expected_approver_role"),
                    "amount_usd": label.get("expected_amount_usd"),
                    "reason_codes": label.get("expected_reason_codes") or [],
                },
            }
        )
    return cases


def load_negative_cases() -> List[Dict[str, Any]]:
    path = Path(__file__).resolve().parent / "new_negative_cases.json"
    entries = json.load(path.open())
    return [
        {
            "source": "new_negative",
            "ticket": entry["ticket"],
            "expected": entry["expected"],
        }
        for entry in entries
    ]


def load_smoke_cases() -> List[Dict[str, Any]]:
    data_dir = _data_dir()
    return json.load((data_dir / "inbox" / "public_tickets.json").open())


def score(run: Dict[str, Any], expected: Dict[str, Any]) -> Tuple[bool, List[str]]:
    failures: List[str] = []
    if expected.get("outcome") is not None and run.get("outcome") != expected["outcome"]:
        failures.append(f"outcome {run.get('outcome')} != {expected['outcome']}")
    if expected.get("human_approval_required") is not None and bool(
        run.get("human_approval_required")
    ) != bool(expected["human_approval_required"]):
        failures.append(
            f"approval {run.get('human_approval_required')} != "
            f"{expected['human_approval_required']}"
        )
    if "approver_role" in expected and run.get("approver_role") != expected.get(
        "approver_role"
    ):
        failures.append(f"role {run.get('approver_role')} != {expected.get('approver_role')}")
    if expected.get("amount_usd") is not None:
        got = run.get("amount_usd")
        if got is None or abs(float(got) - float(expected["amount_usd"])) > 0.01:
            failures.append(f"amount {got} != {expected['amount_usd']}")
    reason_codes = run.get("reason_codes") or []
    for code in expected.get("reason_codes") or []:
        if code not in reason_codes:
            failures.append(f"missing reason code {code}")
    if expected.get("resolved_order_ids") is not None:
        got_ids = [
            entry.get("order_id")
            for entry in (run.get("resolved_orders") or [])
            if entry.get("order_id")
        ]
        if sorted(got_ids) != sorted(expected["resolved_order_ids"]):
            failures.append(
                f"resolved orders {got_ids} != {expected['resolved_order_ids']}"
            )
    return not failures, failures


def call_http(url: str, ticket: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps(ticket).encode("utf-8")
    request = urllib.request.Request(
        f"{url.rstrip('/')}/tickets",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def additional_checks(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Checks that need state: decisions and duplicate/ledger behaviour."""
    inbox = load_inbox()
    checks: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "decision.sqlite")
        pending = process_ticket(inbox["T003"], index=index, store=store, persist=True)
        approved = apply_decision(
            pending["run_id"], "approve", "lead@larkfield", store=store
        )
        repeated = apply_decision(
            pending["run_id"], "reject", "other@larkfield", store=store
        )
        ledger = [e for e in store.get_ledger() if e["run_id"] == pending["run_id"]]
        ok = (
            pending.get("human_approval_required") is True
            and approved.get("human_approval_required") is False
            and approved.get("run_status") == "APPROVED"
            and repeated.get("run_status") == "APPROVED"
            and len(ledger) == 1
            and ledger[0]["action"] == "REFUND"
            and ledger[0]["status"] == "APPROVED"
        )
        checks.append(
            {
                "check": "decision_sets_human_approval_false",
                "pass": ok,
                "detail": {
                    "pending_run_status": pending.get("run_status"),
                    "approved_status": approved.get("run_status"),
                    "human_approval_after": approved.get("human_approval_required"),
                    "ledger": ledger,
                },
            }
        )

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "duplicate.sqlite")

        originals = {
            original_id: process_ticket(
                inbox[original_id], index=index, store=store, persist=True
            )
            for original_id in ("T002", "T005")
        }

        # T027/T028 are refund copies of T002/T005.
        pairs = [("T002", "T027"), ("T005", "T028")]
        pair_details: Dict[str, Any] = {}
        pairs_ok = True
        for original_id, copy_id in pairs:
            original = originals[original_id]
            copy_ticket = dict(inbox[copy_id])
            if copy_id == "T027":
                copy_ticket["subject"] = "Fwd: Re: Damaged Arc floor lamp"
                copy_ticket["body"] = (
                    "---------- Forwarded message ---------\n"
                    "From: Ben <ben.tanaka1@mail.example>\n"
                    "Subject: Fwd: Damaged Arc floor lamp\n\n"
                    f"> {inbox['T002']['body']}"
                )
            forwarded = process_ticket(copy_ticket, index=index, store=store, persist=True)
            ledger = store.get_ledger()
            original_ledger = [e for e in ledger if e["run_id"] == original["run_id"]]
            copy_ledger = [e for e in ledger if e["run_id"] == forwarded["run_id"]]
            pair_ok = (
                forwarded.get("duplicate_of") == original.get("run_id")
                and len(original_ledger) == 1
                and len(copy_ledger) == 0
            )
            pairs_ok = pairs_ok and pair_ok
            pair_details[f"{copy_id}->{original_id}"] = {
                "pass": pair_ok,
                "duplicate_of": forwarded.get("duplicate_of"),
                "original_run_id": original.get("run_id"),
                "copy_ledger_entries": len(copy_ledger),
            }
        checks.append(
            {"check": "refund_copies_T027_T028", "pass": pairs_ok, "detail": pair_details}
        )

        # Plain copy plus quoted copy: same sender/order/body, different subject, no Fwd/Re
        # on one of them. Three copies of the same lamp must produce one refund only.
        plain_ticket = dict(inbox["T002"])
        plain_ticket["ticket_id"] = "T002C"
        plain_ticket["subject"] = "Question about my lamp"
        plain_copy = process_ticket(plain_ticket, index=index, store=store, persist=True)

        quoted_lamp_ticket = dict(inbox["T002"])
        quoted_lamp_ticket["ticket_id"] = "T002Q"
        quoted_lamp_ticket["subject"] = "Fwd: Question about my lamp"
        quoted_lamp_ticket["body"] = (
            "---------- Forwarded message ---------\n"
            "From: Ben <ben.tanaka1@mail.example>\n"
            "Subject: Fwd: Damaged Arc floor lamp\n\n"
            f"> {inbox['T002']['body']}"
        )
        quoted_lamp = process_ticket(
            quoted_lamp_ticket, index=index, store=store, persist=True
        )

        ledger = store.get_ledger()
        original_entries = sum(
            1 for e in ledger if e["run_id"] == originals["T002"]["run_id"]
        )
        plain_entries = sum(1 for e in ledger if e["run_id"] == plain_copy["run_id"])
        quoted_lamp_entries = sum(
            1 for e in ledger if e["run_id"] == quoted_lamp["run_id"]
        )
        plain_ok = (
            plain_copy.get("duplicate_of") == originals["T002"]["run_id"]
            and quoted_lamp.get("duplicate_of") == originals["T002"]["run_id"]
            and original_entries == 1
            and plain_entries == 0
            and quoted_lamp_entries == 0
        )
        checks.append(
            {
                "check": "plain_copy_is_duplicate",
                "pass": plain_ok,
                "detail": {
                    "plain_duplicate_of": plain_copy.get("duplicate_of"),
                    "quoted_duplicate_of": quoted_lamp.get("duplicate_of"),
                    "original_run_id": originals["T002"]["run_id"],
                    "original_ledger_entries": original_entries,
                    "plain_ledger_entries": plain_entries,
                    "quoted_ledger_entries": quoted_lamp_entries,
                },
            }
        )

        # Quoted forward: full headers + ">" markers, subject has no Fwd/Re.
        quoted_ticket = dict(inbox["T005"])
        quoted_ticket["ticket_id"] = "T005Q"
        quoted_ticket["subject"] = "Damaged Stoneware mug set (4)"
        quoted_ticket["body"] = (
            "---------- Forwarded message ---------\n"
            "From: Elena Wu <elena.wu4@mail.example>\n"
            "Sent: 2026-10-03\n"
            "To: support@larkfield.example\n"
            "Subject: Fwd: Damaged Stoneware mug set (4)\n\n"
            f"> {inbox['T005']['body']}"
        )
        quoted_copy = process_ticket(quoted_ticket, index=index, store=store, persist=True)
        ledger = store.get_ledger()
        quoted_ok = (
            quoted_copy.get("duplicate_of") == originals["T005"]["run_id"]
            and sum(1 for e in ledger if e["run_id"] == quoted_copy["run_id"]) == 0
        )
        checks.append(
            {
                "check": "quoted_forward_is_duplicate",
                "pass": quoted_ok,
                "detail": {
                    "duplicate_of": quoted_copy.get("duplicate_of"),
                    "original_run_id": originals["T005"]["run_id"],
                    "copy_ledger_entries": sum(
                        1 for e in ledger if e["run_id"] == quoted_copy["run_id"]
                    ),
                },
            }
        )

        # A different request on the same line is still suppressed: the line already has a
        # PROPOSED/APPROVED REFUND. A fresh PEND still gets its own ledger, but its copy
        # does not.
        different_ticket = dict(inbox["T002"])
        different_ticket["ticket_id"] = "T002D"
        different_ticket["subject"] = "Wrong colour"
        different_ticket["body"] = (
            "The Arc floor lamp on order LF-100201 is the wrong colour, please refund."
        )
        different = process_ticket(different_ticket, index=index, store=store, persist=True)
        pending = process_ticket(inbox["T003"], index=index, store=store, persist=True)
        forwarded_pending_ticket = dict(inbox["T003"])
        forwarded_pending_ticket["ticket_id"] = "T003F"
        forwarded_pending_ticket["subject"] = "Fwd: Damaged Oak dining chair"
        forwarded_pending = process_ticket(
            forwarded_pending_ticket, index=index, store=store, persist=True
        )
        ledger = store.get_ledger()
        different_ok = (
            different.get("duplicate_of") == originals["T002"]["run_id"]
            and sum(1 for e in ledger if e["run_id"] == different["run_id"]) == 0
        )
        pend_ok = any(
            e["run_id"] == pending["run_id"] and e["action"] == "REFUND" for e in ledger
        )
        forwarded_pend_ok = (
            forwarded_pending.get("duplicate_of") == pending.get("run_id")
            and sum(1 for e in ledger if e["run_id"] == forwarded_pending["run_id"]) == 0
        )
        checks.append(
            {
                "check": "same_line_suppressed_and_pend_ledger",
                "pass": different_ok and pend_ok and forwarded_pend_ok,
                "detail": {
                    "different_duplicate_of": different.get("duplicate_of"),
                    "different_ledger_entries": sum(
                        1 for e in ledger if e["run_id"] == different["run_id"]
                    ),
                    "pending_ledger_entries": sum(
                        1
                        for e in ledger
                        if e["run_id"] == pending["run_id"] and e["action"] == "REFUND"
                    ),
                    "forwarded_pending_duplicate_of": forwarded_pending.get("duplicate_of"),
                    "forwarded_pending_ledger_entries": sum(
                        1 for e in ledger if e["run_id"] == forwarded_pending["run_id"]
                    ),
                },
            }
        )
    return checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None, help="Score the deployed service instead")
    parser.add_argument("--out", default=None)
    parser.add_argument("--no-smoke", action="store_true")
    parser.add_argument(
        "--llm",
        "--use-llm",
        dest="use_llm",
        action="store_true",
        help="Use the configured LLM instead of the deterministic in-process path.",
    )
    args = parser.parse_args()

    index = load_index() if not args.url else None
    cases = load_labelled_cases() + load_negative_cases()

    results: List[Dict[str, Any]] = []
    passed = 0
    for case in cases:
        ticket = case["ticket"]
        run = (
            call_http(args.url, ticket)
            if args.url
            else process_ticket(ticket, index=index, persist=False)
        )
        ok, failures = score(run, case["expected"])
        passed += int(ok)
        results.append(
            {
                "ticket_id": ticket.get("ticket_id"),
                "source": case["source"],
                "pass": ok,
                "failures": failures,
                "expected": case["expected"],
                "got": {
                    "outcome": run.get("outcome"),
                    "ticket_type": run.get("ticket_type"),
                    "human_approval_required": run.get("human_approval_required"),
                    "approver_role": run.get("approver_role"),
                    "amount_usd": run.get("amount_usd"),
                    "reason_codes": run.get("reason_codes"),
                    "resolved": run.get("resolved"),
                    "resolved_orders": run.get("resolved_orders"),
                },
            }
        )

    smoke = []
    if not args.no_smoke and not args.url:
        for ticket in load_smoke_cases():
            run = process_ticket(ticket, index=index, persist=False)
            smoke.append(
                {
                    "ticket_id": ticket.get("ticket_id"),
                    "outcome": run.get("outcome"),
                    "ticket_type": run.get("ticket_type"),
                    "amount_usd": run.get("amount_usd"),
                    "reason_codes": run.get("reason_codes"),
                }
            )

    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    extra_checks = additional_checks(index) if not args.url else []

    output = {
        "timestamp": timestamp,
        "pass_rule": (
            "outcome/approval/role exact; amount within $0.01 when expected; "
            "reason_codes superset of expected"
        ),
        "scored": {
            "total": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "pass_rate": round(passed / len(cases), 4) if cases else 0.0,
        },
        "additional_checks": extra_checks,
        "cases": results,
        "smoke": smoke,
        "smoke_note": "Public tickets are smoke-checked only and excluded from the pass rate.",
    }

    out_path = Path(args.out) if args.out else Path(__file__).resolve().parent / f"results_{timestamp}.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    print(json.dumps(output["scored"], indent=2))
    for result in results:
        if not result["pass"]:
            print(f"FAIL {result['ticket_id']}: {'; '.join(result['failures'])}")
    for check in extra_checks:
        if not check["pass"]:
            print(f"FAIL {check['check']}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
