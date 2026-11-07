"""Regression tests for duplicate detection and late status questions."""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from src.data import find_data_dir, load_index
from src.service import process_ticket
from src.store import Store


@pytest.fixture(scope="module")
def index() -> Dict[str, Any]:
    return load_index()


@pytest.fixture(scope="module")
def inbox() -> Dict[str, Dict[str, Any]]:
    data_dir = find_data_dir()
    tickets = json.load((data_dir / "tickets" / "support_inbox_export.json").open())
    return {ticket["ticket_id"]: ticket for ticket in tickets}


@pytest.fixture
def misleading_llm(monkeypatch):
    """Exercise the model path with deliberately wrong policy signals.

    The deterministic policy flags must win, otherwise a hallucinating model can
    create a second refund or reject a late status question on the window.
    """

    def fake_llm(subject: str, body: str) -> Dict[str, Any]:
        return {
            "data": {
                "ticket_type": "OTHER",
                "order_ids": ["LF-999999"],
                "products": [],
                "damage_or_fault": True,
                "wrong_or_not_as_described": True,
                "change_of_mind": True,
                "asks_refund": True,
                "asks_return": True,
                "asks_shipping": True,
                "whole_order": True,
                "injection": True,
            },
            "model": "fake-model",
            "input_tokens": 10,
            "output_tokens": 10,
        }

    monkeypatch.setattr("src.config.LLM_API_KEY", "test-key")
    monkeypatch.setattr("src.extract._llm_extract", fake_llm)


def test_same_line_fwd_and_quoted_copies_one_refund(
    index: Dict[str, Any],
    inbox: Dict[str, Dict[str, Any]],
    tmp_path,
    misleading_llm,
) -> None:
    # Duplicate rule is ledger-based: the same order line already has a PROPOSED
    # REFUND, so neither the Fwd: copy nor the > quoted copy may add another.
    store = Store(tmp_path / "state.sqlite")
    original = process_ticket(inbox["T002"], index=index, store=store, persist=True)

    forwarded = process_ticket(inbox["T027"], index=index, store=store, persist=True)

    quoted_ticket = dict(inbox["T002"])
    quoted_ticket["ticket_id"] = "T002Q"
    quoted_ticket["subject"] = "Fwd: Damaged Arc floor lamp"
    quoted_ticket["body"] = (
        "---------- Forwarded message ---------\n"
        "From: Ben <ben.tanaka1@mail.example>\n"
        "Subject: Fwd: Damaged Arc floor lamp\n\n"
        f"> {inbox['T002']['body']}"
    )
    quoted = process_ticket(quoted_ticket, index=index, store=store, persist=True)

    assert original["outcome"] == "PROCEED"
    assert original["amount_usd"] == 129.0
    assert forwarded["duplicate_of"] == original["run_id"]
    assert quoted["duplicate_of"] == original["run_id"]

    ledger = store.get_ledger()
    refunds = [entry for entry in ledger if entry["action"] == "REFUND"]
    assert len(refunds) == 1
    assert refunds[0]["run_id"] == original["run_id"]
    assert refunds[0]["amount_usd"] == 129.0
    assert all(entry["run_id"] != forwarded["run_id"] for entry in ledger)
    assert all(entry["run_id"] != quoted["run_id"] for entry in ledger)


def test_late_status_question_is_proceed_not_out_of_window(
    index: Dict[str, Any],
    misleading_llm,
) -> None:
    ticket = {
        "ticket_id": "LATE-STATUS",
        "received_at": "2026-10-01T09:00:00+00:00",
        "channel": "email",
        "from": "asha.shah0@mail.example",
        "to": "support@larkfield.example",
        "subject": "Order status",
        "body": "What is the status of order LF-100200?",
        "attachments": [],
    }

    run = process_ticket(ticket, index=index, persist=False)

    assert run["ticket_type"] == "ORDER_STATUS"
    assert run["outcome"] == "PROCEED"
    assert "OUT_OF_WINDOW" not in run["reason_codes"]
    assert run["human_approval_required"] is False
