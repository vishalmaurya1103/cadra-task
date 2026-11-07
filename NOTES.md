# Locked decisions and build notes

Authoritative policy: `policies/refund_policy_RP-3.2.md` (v3.2, effective 2026-07-01).
It replaces all earlier customer-care guidance. `help_centre_faq_v2.md` is used for tone
only. `warehouse_picking_WH-PICK-1.4.md` is internal and never used for customer decisions.

## Locked policy rules

- Returns / refunds: 30 days from the carrier `delivered_at` date.
- Damaged or faulty items: 45 days only when an image attachment is present.
  `DAMAGE_EXCEPTION` is emitted only for days 31-45.
- "Not what I hoped" / change of mind is treated as not-as-described and is eligible
  inside the 30-day window, at order-line value.
- Undelivered shipments do not use a window anchor. `UNAVAILABLE` => `UNABLE_TO_VERIFY`
  (`CARRIER_STATUS_UNAVAILABLE`); `IN_TRANSIT` => status reply unless lost.
- A carrier `DELIVERED` scan contradicts a pure non-delivery claim (no damage, wrong-item
  or return request): `UNABLE_TO_VERIFY`, no reason code, because receipt cannot be
  established from the data room.
- Lost in transit: calendar-day difference from `last_event_at` (fallback `shipped_at`)
  to `received_at` is greater than 10. Never clamp a `last_event_at` that precedes
  `shipped_at`; the export contains those anomalies and they are intentional.
- Affected quantity never exceeds ordered quantity. Per-line net =
  `max(0, affected_qty * unit_price - sum(ISSUED refunds for that line))`.
- A fully refunded sole line is `REJECT` + `ALREADY_REFUNDED`. Other eligible lines still
  proceed.
- Shipping fees are never refunded. The refund amount is order-line value only.
- "Whole order" or any request wider than the affected lines => affected lines only,
  `PARTIAL_LINES`, `PROCEED_MODIFIED`.
- Authority is tested on the new proposed amount only: > $150 => `PEND`,
  `APPROVAL_REQUIRED`, `SUPPORT_LEAD`. <= $150 => no approval.
- Policy-override / injection is checked before everything else: `ESCALATE`,
  `POLICY_OVERRIDE_ATTEMPT`, `TRUST_SAFETY`, no refund.
- Order must exist and match the sender's normalized email. Not found =>
  `ORDER_NOT_FOUND`; exists but other customer => `CUSTOMER_MISMATCH`; both are
  `INVALID_REQUEST` with `resolved` null.
- Order rows are deduplicated by `order_id` (first row wins). `LF-100216` and
  `LF-100239` are duplicated in the export.
- Ambiguous `08/10/2026` is parsed as DD/MM/YYYY (dominant format). `LF-100221` has an
  order date after its shipment date and is flagged as a data anomaly; it is not
  decision-critical because decisions use `delivered_at`.
- An image attachment counts as a photo by `content_type` or filename extension, even
  when `content_base64` is empty.

## Requested changes incorporated

1. No staged "baseline". The evaluation records the real first result; the recorded
   change is whatever actually fails and is then fixed.
2. `inbox/public_tickets.json` is a smoke check only and is excluded from the pass rate.
3. A `PROCEED`/`PROCEED_MODIFIED` refund writes a `REFUND` ledger proposal with its
   amount and includes the reply. Info-only requests write a `REPLY` ledger proposal.
4. Forwarded duplicates get their own `run_id` and a `duplicate_of` field, but create no
   second ledger action (3.3).
5. LLM access uses the OpenAI Python library with `LLM_API_KEY`, `LLM_BASE_URL`,
   `LLM_MODEL`. The key is loaded from `.env` only. Cost uses a per-model OpenAI
   list-price table.

## Added run fields (documented in README)

- `amount_usd`: proposed refund amount, null when not a refund.
- `run_status`: `PENDING_APPROVAL`, `COMPLETE`, `APPROVED` or `REJECTED`.
- `duplicate_of`: original `run_id` for a forwarded duplicate, otherwise null.
- `decision`: `{decision, approver, decided_at}` once a person decides, otherwise null.
- `latency_ms`: server-side processing latency.

## Evaluation rules

- Pass rule: `outcome`, `human_approval_required`, `approver_role` exact; proposed amount
  within $0.01 when expected; `reason_codes` is a superset of expected codes.
- Scored set: 15 labelled tickets plus new negative cases. Public tickets are smoke only.
- First results file is the real first run. The change between the two committed result
  files is the actual fix for whatever failed, not a pre-planned stage.
