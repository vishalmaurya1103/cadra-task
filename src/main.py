"""FastAPI service for the Larkfield support desk."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException

from . import config
from .data import load_index
from .schemas import Decision, Ticket
from .service import apply_decision, process_ticket
from .store import get_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build/load the read-only data index once, then reuse it.
    app.state.index = load_index()
    app.state.store = get_store()
    yield


app = FastAPI(title="Larkfield Customer Care", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/tickets")
def create_ticket(ticket: Ticket) -> Dict[str, Any]:
    payload = ticket.model_dump(by_alias=True)
    return process_ticket(
        payload,
        index=app.state.index,
        store=app.state.store,
        persist=True,
    )


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    run = app.state.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.post("/runs/{run_id}/decision")
def decide(run_id: str, decision: Decision) -> Dict[str, Any]:
    run = apply_decision(
        run_id,
        decision=decision.decision,
        approver=decision.approver,
        store=app.state.store,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/ledger")
def ledger() -> List[Dict[str, Any]]:
    return app.state.store.get_ledger()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=config.PORT)
