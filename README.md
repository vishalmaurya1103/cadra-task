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
return the same run. Forwarded duplicates get their own `run_id` plus `duplicate_of`, and
create no second ledger action.

## Added run fields (allowed by the contract)

- `amount_usd`: proposed refund amount, null when not a refund.
- `run_status`: `PENDING_APPROVAL`, `COMPLETE`, `APPROVED` or `REJECTED`.
- `duplicate_of`: original `run_id` for a forwarded duplicate, otherwise null.
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
```

- Scored set: 15 labelled tickets + new negative cases (`eval/new_negative_cases.json`).
- Pass rule: `outcome`, `human_approval_required`, `approver_role` exact; proposed amount
  within $0.01 when expected; `reason_codes` is a superset of expected.
- `inbox/public_tickets.json` is smoke-checked only and is excluded from the pass rate.
- Two timestamped result files are committed in `eval/`. The first is the real first run
  (22/23; N08 failed because a carriage-delivered order claiming non-delivery was wrongly
  rejected on the window). The change that fixed it was the delivery-status-vs-claim guard;
  the second run is 23/23.

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
