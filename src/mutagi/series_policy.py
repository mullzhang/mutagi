"""Validate bounded operational proposals without granting new permissions."""

from __future__ import annotations

import math


def positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


def validate_adaptation(value, reserve):
    fields = {
        "max_tasks",
        "min_seconds",
        "max_round_seconds",
        "max_experiment_seconds",
        "max_integration_seconds",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("adaptation fields do not match the documented schema")

    if type(value["max_tasks"]) is not int or value["max_tasks"] < 1:
        raise ValueError("max_tasks must be a positive integer")

    for key in fields - {"max_tasks"}:
        positive(value[key], key)

    if value["min_seconds"] <= reserve or any(
        value[key] < value["min_seconds"]
        for key in fields - {"max_tasks", "min_seconds"}
    ):
        raise ValueError("adaptation bounds must allow time for execution and preservation")


def proposal_schema():
    fields = {
        key: {"type": "number"}
        for key in ("round_seconds", "experiment_seconds", "integration_seconds")
    }
    fields.update(
        reason={"type": "string"},
        tasks={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "parent": {"type": ["integer", "null"]},
                },
                "required": ["prompt", "parent"],
                "additionalProperties": False,
            },
        },
    )
    return {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }


def validate_proposal(proposal, limits):
    if not isinstance(proposal, dict) or set(proposal) != set(
        proposal_schema()["required"]
    ):
        raise ValueError("invalid fields in the next-round proposal")

    if not isinstance(proposal["reason"], str) or not proposal["reason"].strip():
        raise ValueError("a proposal requires a reason and evidence")

    tasks = proposal["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= limits["max_tasks"]:
        raise ValueError("the proposed task count exceeds the authorized bounds")

    for i, task in enumerate(tasks):
        if (
            not isinstance(task, dict)
            or set(task) != {"prompt", "parent"}
            or not isinstance(task["prompt"], str)
            or not task["prompt"].strip()
            or (
                task["parent"] is not None
                and (type(task["parent"]) is not int or not 0 <= task["parent"] < i)
            )
        ):
            raise ValueError("proposed inheritance must refer to an earlier task in the same round")

    for key in ("round_seconds", "experiment_seconds", "integration_seconds"):
        positive(proposal[key], key)
        if not limits["min_seconds"] <= proposal[key] <= limits["max_" + key]:
            raise ValueError("proposed times exceed the authorized bounds")

    return proposal
