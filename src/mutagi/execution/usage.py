"""Stop thresholds for measured tokens and estimated costs of managed sessions."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from mutagi.execution.policy import positive


def validate_limits(value):
    if not isinstance(value, dict) or set(value) != {"tokens", "cost", "rates"}:
        raise ValueError("usage limits require tokens, cost, and rates")

    if type(value["tokens"]) is not int or value["tokens"] <= 0:
        raise ValueError("the token threshold must be a positive integer")

    positive(value["cost"], "cost")
    rates = value["rates"]
    if not isinstance(rates, dict) or set(rates) != {
        "input",
        "cached_input",
        "output",
        "unit",
        "source",
        "model",
    }:
        raise ValueError(
            "the rate card requires model, unit, source, and three rates per million tokens"
        )

    for key in ("input", "cached_input", "output"):
        positive(rates[key], key)

    if any(
        not isinstance(rates[k], str) or not rates[k].strip()
        for k in ("unit", "source", "model")
    ):
        raise ValueError("specify the rate source and unit")


def measured(total, rates):
    keys = (
        "inputTokens",
        "cachedInputTokens",
        "outputTokens",
        "reasoningOutputTokens",
        "totalTokens",
    )
    if any(type(total[k]) is not int or total[k] < 0 for k in keys):
        raise ValueError("usage counts must be nonnegative integers")

    if (
        total["cachedInputTokens"] > total["inputTokens"]
        or total["reasoningOutputTokens"] > total["outputTokens"]
        or total["totalTokens"] != total["inputTokens"] + total["outputTokens"]
    ):
        raise ValueError("usage counts are inconsistent")

    if total.get("cacheWriteInputTokens", 0):
        raise ValueError("this rate card does not support cache-write billing")

    cost = (
        Decimal(total["inputTokens"] - total["cachedInputTokens"])
        * Decimal(str(rates["input"]))
        + Decimal(total["cachedInputTokens"]) * Decimal(str(rates["cached_input"]))
        + Decimal(total["outputTokens"]) * Decimal(str(rates["output"]))
    ) / Decimal(1_000_000)
    return {"tokens": total["totalTokens"], "cost": str(cost), "counts": total}


def remaining(repo, series):
    limits = series["plan"]["usage_limits"]
    tokens = 0
    cost = Decimal(0)
    unknown = []
    for path in (Path(repo) / ".mutagi/runs").glob("*/state.json"):
        state = json.loads(path.read_text())
        if state.get("series_id") != series["id"]:
            continue

        usage_path = path.parent / "usage.json"
        if usage_path.exists():
            usage = json.loads(usage_path.read_text())
            tokens += usage["tokens"]
            cost += Decimal(usage["cost"])
            if not usage["complete"]:
                unknown.append(state["id"])
        elif (
            state["phase"] not in ("preparing", "ready")
            and state.get("reason") != "preparation_failed"
        ):
            unknown.append(state["id"])

    return {
        "tokens": limits["tokens"] - tokens,
        "cost": str(Decimal(str(limits["cost"])) - cost),
        "used_tokens": tokens,
        "estimated_cost": str(cost),
        "unit": limits["rates"]["unit"],
        "unknown_sessions": unknown,
    }
