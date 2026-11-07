# Approach

## What we found

`policies/refund_policy_RP-3.2.md` is authoritative; FAQ v2 is superseded and WH-PICK is internal. The export is deliberately messy: duplicate order rows, mixed date and money formats, padded/uppercase senders, five order-id styles, prior refunds, carrier timestamp anomalies, and prompt injection. All 15 labels reconcile with RP-3.2 after normalization. Open edges are locked in `NOTES.md`.

## Architecture

FastAPI service. `src/prepare_data.py` builds a read-only JSON index and a data report; source CSVs are untouched. `src/extract.py` normalizes sender/order/date/money, extracts intent and products, detects image attachments and injection, with one optional OpenAI extraction call. `src/policy.py` is a deterministic ordered evaluator (override -> identity -> carrier -> window -> affected lines -> prior refunds -> partial -> authority) that emits clause-backed checks, evidence, reason codes and the outcome. `src/reply.py` builds decision-consistent replies. `src/trace.py` records model id, tokens and price-table cost. `src/store.py` provides SQLite runs, decisions and an idempotent ledger; `src/main.py` exposes the five endpoints.

## Evaluation

`eval/run.py` scores 15 labels plus 13 new negatives (injection in body/subject, wrong sender, unknown order, carrier unavailable, 10-day boundary, already-refunded, 45-day damage, delivered-vs-non-delivery, multi-order, late status, over-asking). Public tickets are smoke only. The real first run was 22/23: N08 (carrier DELIVERED vs non-delivery claim) was wrongly rejected on the window. The delivery-status-vs-claim guard fixed it; after the seven reported regression fixes the expanded suite is 28/28. Median offline latency is 0.1 ms; cost is $0.00 without a model key, and deployed cost uses tokens x OpenAI list price.
