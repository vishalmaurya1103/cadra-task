# Larkfield Customer Care agent service

An HTTP service that reads a support email and returns the desk's decision. It proposes
actions only: it never refunds, sends mail or changes data. See `NOTES.md` for the locked
policy decisions and `APPROACH.md` / `ARTEFACT.md` for the written deliverables.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in LLM_API_KEY; never commit .env
```

Create the reproducible data index (read-only; source CSVs are never edited):

```bash
python -m src.prepare_data
```

This writes `build/data_index.json` and `build/data_report.json`. The service also builds the
index on first start if the file is absent.

## Run

```bash
uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

Render: build `pip install -r requirements.txt`, start the command above, health check
`/health`. Set `LLM_API_KEY`, `LLM_BASE_URL` and `LLM_MODEL` as host environment variables,
not in the repository. `PORT` is read from the environment.

## Endpoints

- `GET /health` -> `{"status":"ok"}`
- `POST /tickets` -> run for one ticket
- `GET /runs/{run_id}` -> current run, including one waiting for a person
- `POST /runs/{run_id}/decision` -> `{"decision":"approve"|"reject","approver":"..."}`
- `GET /ledger` -> recorded proposals (`REFUND`, `REPLY`, `ESCALATION`)

Decisions are idempotent: the first decision updates the proposal once; later decisions
return the same run. Duplicate detection is ledger-based, not email-text-based: if an order
line already has a `REFUND` that is `PROPOSED` or `APPROVED`, a new request for that line
gets its own `run_id` plus `duplicate_of` and creates no second ledger action.

## Added run fields (allowed by the contract)

- `amount_usd`: proposed refund amount, null when not a refund.
- `run_status`: `PENDING_APPROVAL`, `COMPLETE`, `APPROVED` or `REJECTED`.
- `resolved_orders`: every referenced order with `matched` true/false (multi-order emails).
- `duplicate_of`: `run_id` that already holds a `PROPOSED`/`APPROVED` `REFUND` for the
  same order line, otherwise null.
- `decision`: `{decision, approver, decided_at}` after a person decides, otherwise null.
- `latency_ms`: server-side processing latency.
- `trace.llm_used` / `trace.llm_error`: whether the optional extraction model succeeded.

## Model and cost

One OpenAI-compatible extraction call per ticket when `LLM_API_KEY` is set. The deterministic
policy engine decides; the model only helps parse the email. If the call fails, regex
extraction is used and the run records 0 tokens plus the error. Cost is computed from a
per-model OpenAI list-price table in `src/trace.py` using the response's token counts.

## Evaluation

```bash
python -m eval.run
pytest -q
```

- Scored set: 15 labelled tickets + 13 new negative cases (`eval/new_negative_cases.json`).
- `tests/test_regressions.py` covers ledger-based duplicate suppression (original + `Fwd:`
  copy + `>`-quoted copy = one refund) and the late-status-question `PROCEED` path,
  including a deliberately misleading LLM response.
- Pass rule: `outcome`, `human_approval_required`, `approver_role` exact; proposed amount
  within $0.01 when expected; `reason_codes` is a superset of expected; `resolved_order_ids`
  when supplied.
- `inbox/public_tickets.json` is smoke-checked only and is excluded from the pass rate.
- Two timestamped result files are committed in `eval/`:
  - `results_20261107T124551Z.json` is the real first run on the then-current 23-case set:
    22/23. N08 failed because a carrier-delivered order claiming non-delivery was wrongly
    rejected on the window; the delivery-status-vs-claim guard fixed it.
  - `results_20261107T160411Z.json` is the expanded 28-case run after the reported
    regression fixes: 28/28. Extra checks cover approve/reject clearing
    `human_approval_required`, ledger-based duplicate suppression, plain copies,
    `>`-quoted forwards, same-line suppression and PEND ledger behaviour.

## Layout

- `src/prepare_data.py`, `src/data.py` - read-only normalization/index and data report
- `src/extract.py` - order/email normalization, intent and product extraction, optional LLM
- `src/policy.py` - RP-3.2 evaluator and checks/evidence
- `src/reply.py` - decision-consistent reply templates
- `src/trace.py` - model calls, token counts and price-table cost
- `src/store.py` - SQLite runs/ledger/decisions
- `src/service.py` - orchestration, idempotency and duplicate handling
- `src/main.py` - FastAPI endpoints
- `eval/` - cases, runner and timestamped results
