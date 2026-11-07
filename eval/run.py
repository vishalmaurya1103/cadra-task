"""Evaluation runner.

Pass rule: outcome, human_approval_required and approver_role exact; proposed amount
within $0.01 when expected; reason_codes is a superset of the expected codes.
Public tickets are smoke-checked only and never enter the pass rate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.data import find_data_dir, load_index
from src.service import process_ticket


def _data_dir() -> Path:
    return find_data_dir()


def load_labelled_cases() -> List[Dict[str, Any]]:
    data_dir = _data_dir()
    inbox = {
        ticket["ticket_id"]: ticket
        for ticket in json.load((data_dir / "tickets" / "support_inbox_export.json").open())
    }
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None, help="Score the deployed service instead")
    parser.add_argument("--out", default=None)
    parser.add_argument("--no-smoke", action="store_true")
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
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
