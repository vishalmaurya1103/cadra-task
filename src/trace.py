"""Model usage, cost and trace assembly."""
from __future__ import annotations

from typing import Any, Dict, List

from . import config

# OpenAI list prices, USD per 1M tokens: (input, output).
PRICE_TABLE: Dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o4-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
}
DEFAULT_PRICE = (0.15, 0.60)


def price_for(model: str) -> tuple[float, float]:
    name = (model or "").split("/")[-1].lower()
    if name in PRICE_TABLE:
        return PRICE_TABLE[name]
    for key, value in PRICE_TABLE.items():
        if name.startswith(key):
            return value
    return DEFAULT_PRICE


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = price_for(model)
    cost = (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out
    return round(cost, 8)


def build_trace(ex: Dict[str, Any], tools: List[str]) -> Dict[str, Any]:
    usage = ex.get("usage") or {}
    model = usage.get("model") or ex.get("llm_model") or config.LLM_MODEL
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    call = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return {
        "model_calls": [call],
        "tools_called": list(tools),
        "documents_used": [{"document": "RP-3.2", "version": "3.2"}],
        "cost_usd": compute_cost(model, input_tokens, output_tokens),
        "llm_used": bool(ex.get("llm_used")),
        "llm_error": (usage.get("error") if usage else None),
    }
