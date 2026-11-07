# Artefact

## What preparing the data found and did

- Read-only preparation (`python -m src.prepare_data`) found 53 customers, 49 order rows (47 unique), 57 order lines, 47 shipments and 3 issued refunds.
- Deduplicated order rows `LF-100216` and `LF-100239` by `order_id`.
- Normalized 3 `Order Total` formats (plain 16, `$` 17, `USD` 16) and 13 `DD/MM/YYYY` order dates.
- Shipment nulls: `delivered_at` 13, `last_event_at` 3. Status mix: 34 DELIVERED, 10 IN_TRANSIT, 3 UNAVAILABLE.
- Anomalies: `last_event_at` before `shipped_at` for LF-100210/11/229/239; order date after shipment for LF-100221/240; 23 duplicate customer names; 8 dirty ticket senders; 5 order-reference styles.
- Prior refunds: RF-900 LF-100217 $185, RF-901 LF-100224 $45, RF-902 LF-100243 $45.

## Which policy is used

RP-3.2 v3.2 only. FAQ v2 is tone-only and superseded; WH-PICK-1.4 is internal and never used for customer decisions. Edge rules are in `NOTES.md`.

## Evaluation before and after one change

- First real run `eval/results_20261107T124551Z.json`: 22/23 (95.65%). N08 failed: carrier says DELIVERED but the customer claims non-delivery; the engine returned `REJECT` on the window instead of `UNABLE_TO_VERIFY`.
- Change: added the delivery-status-vs-claim guard in `src/policy.py` (a delivered scan contradicting a pure non-delivery claim cannot establish receipt).
- Expanded run `eval/results_20261107T160411Z.json`: 28/28 (100%) after the reported regression fixes. New negatives cover 13 cases and more than 3 kinds. Extra checks cover approve/reject clearing `human_approval_required`, ledger-based duplicate suppression (T027/T028, plain copy, `>`-quoted copy), same-line suppression and PEND ledger behaviour. Three copies of the same lamp produce one refund because the line already has a PROPOSED REFUND.

## Cost and latency (3 lines)

- Latency: wall-clock around extract + decide + reply in `process_ticket`, stored as `latency_ms`.
- Cost: OpenAI `usage` token counts x the per-1M list-price table in `src/trace.py`.
- Median over 28 offline cases: 0.1 ms latency and $0.00 cost (no LLM key, 0 tokens); deployed `gpt-4o-mini` runs use real token counts.

## What is not built

- No refund execution, email sending or carrier API integration; the service only proposes.
- No human-review UI; decisions arrive through `POST /runs/{run_id}/decision`.
- LLM extraction is optional and was not exercised with a live key in this environment.
