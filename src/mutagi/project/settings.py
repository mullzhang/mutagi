"""Validate explicitly saved execution defaults without choosing a model."""

from __future__ import annotations

import math

EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


EXECUTION_FIELDS = {
    "experiment": ("model", "effort", "seconds", "reserve_seconds"),
    "round": (
        "model",
        "effort",
        "seconds",
        "experiment_seconds",
        "integration_seconds",
        "reserve_seconds",
    ),
    "series": (
        "model",
        "effort",
        "seconds",
        "round_seconds",
        "experiment_seconds",
        "integration_seconds",
        "reserve_seconds",
    ),
}


def validate_defaults(execution):
    if not isinstance(execution, dict):
        raise ValueError("execution: expected a table of explicit defaults")
    unknown = execution.keys() - {"model", "effort", *EXECUTION_FIELDS}
    if unknown:
        raise ValueError("execution: unknown fields: " + ", ".join(sorted(unknown)))

    def validate_values(values, allowed, prefix):
        if not isinstance(values, dict):
            raise ValueError(f"{prefix}: expected a table")
        for field, value in values.items():
            location = f"{prefix}.{field}"
            if field not in allowed:
                raise ValueError(f"{location}: unsupported execution default")
            if field == "model":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{location}: supply a nonempty model name")
            elif field == "effort":
                if value not in EFFORTS:
                    raise ValueError(f"{location}: choose one of {', '.join(EFFORTS)}")
            elif (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{location}: supply a positive finite duration")

    validate_values(
        {key: value for key, value in execution.items() if key in ("model", "effort")},
        ("model", "effort"),
        "execution",
    )
    for kind, fields in EXECUTION_FIELDS.items():
        if kind in execution:
            validate_values(execution[kind], fields, "execution." + kind)


def execution_defaults(project, kind):
    execution = project.get("execution", {})
    return {
        **{key: execution[key] for key in ("model", "effort") if key in execution},
        **execution.get(kind, {}),
    }
