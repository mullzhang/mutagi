"""Resolve explicit execution settings and validate experiment, round, and series plans."""

from __future__ import annotations

import math

from mutagi.execution.policy import validate_adaptation
from mutagi.execution.records import record_field, record_frontmatter
from mutagi.execution.state import validate_id
from mutagi.project.repository import git, project_at, resolve_base
from mutagi.project.settings import EFFORTS, execution_defaults


def validate_task(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "provide a nonempty instruction with --prompt, --task FILE, or --task -"
        )
    return text


def resolve_settings(repo, base, kind, supplied):
    if not isinstance(base, str) or not base.strip():
        raise ValueError("specify --base or a nonempty base revision in the plan")
    revision = resolve_base(repo, base)
    project = project_at(repo, revision)
    return {**execution_defaults(project, kind), **supplied, "base": revision}


def validate_experiment(task_text, model, effort, seconds, reserve):
    validate_task(task_text)
    if not isinstance(model, str) or not model.strip() or effort not in EFFORTS:
        raise ValueError("specify a nonempty model and a supported reasoning effort")
    if not (
        type(seconds) in (int, float)
        and type(reserve) in (int, float)
        and math.isfinite(seconds)
        and math.isfinite(reserve)
        and 0 < reserve < seconds
    ):
        raise ValueError(
            "budget values must be finite and satisfy 0 < reserve < total seconds"
        )


def validate_parent(repo, base, parent_id, parent_record):
    if bool(parent_id) != bool(parent_record):
        raise ValueError("inheritance requires both --parent-id and --parent-record")

    if parent_id:
        validate_id(parent_id)
        content = git(repo, "show", base + ":" + parent_record)
        actual_id = record_field(record_frontmatter(content), "id")
        if actual_id != parent_id:
            raise ValueError(
                "the parent record at the base commit does not match its ID: "
                f"expected {parent_id!r}; found {actual_id!r}"
            )


def validate_round(plan):
    expected = {
        "base",
        "model",
        "effort",
        "seconds",
        "experiment_seconds",
        "integration_seconds",
        "reserve_seconds",
        "tasks",
    }
    if not isinstance(plan, dict) or set(plan) != expected:
        raise ValueError("round plan fields do not match the documented schema")

    for name in (
        "seconds",
        "experiment_seconds",
        "integration_seconds",
        "reserve_seconds",
    ):
        if (
            type(plan[name]) not in (int, float)
            or not math.isfinite(plan[name])
            or plan[name] <= 0
        ):
            raise ValueError("round budgets must be positive and finite")

    if plan["reserve_seconds"] >= min(
        plan[k] for k in ("seconds", "experiment_seconds", "integration_seconds")
    ):
        raise ValueError("preservation reserve must be shorter than each run's budget")

    if any(
        not isinstance(plan[k], str) or not plan[k].strip()
        for k in ("base", "model", "effort")
    ):
        raise ValueError("base revision, model, and effort are required")

    if plan["effort"] not in EFFORTS:
        raise ValueError("unsupported reasoning effort")

    if not isinstance(plan["tasks"], list) or not plan["tasks"]:
        raise ValueError("at least one exploration task is required")

    for i, task in enumerate(plan["tasks"]):
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
            raise ValueError(
                "each task requires a prompt and an earlier parent index or null"
            )


def round_plan(plan, tasks):
    return {
        **{
            key: plan[key]
            for key in (
                "base",
                "model",
                "effort",
                "experiment_seconds",
                "integration_seconds",
                "reserve_seconds",
            )
        },
        "seconds": plan["round_seconds"],
        "tasks": tasks,
    }


def validate_series(plan):
    fields = {
        "base",
        "model",
        "effort",
        "seconds",
        "round_seconds",
        "experiment_seconds",
        "integration_seconds",
        "reserve_seconds",
        "rounds",
    }
    if (
        not isinstance(plan, dict)
        or set(plan) - {"adaptation", "usage_limits"} != fields
    ):
        raise ValueError("series plan fields do not match the documented schema")

    if (
        type(plan["seconds"]) not in (int, float)
        or not math.isfinite(plan["seconds"])
        or plan["seconds"] <= 0
    ):
        raise ValueError("series budgets must be positive and finite")

    if not isinstance(plan["rounds"], list) or len(plan["rounds"]) < 2:
        raise ValueError("a series requires at least two planned rounds")

    for item in plan["rounds"]:
        if not isinstance(item, dict) or set(item) != {"tasks"}:
            raise ValueError("each round must specify only tasks")

        validate_round(round_plan(plan, item["tasks"]))

    if "adaptation" in plan:
        validate_adaptation(plan["adaptation"], plan["reserve_seconds"])

    if "usage_limits" in plan:
        from mutagi.execution.usage import validate_limits

        validate_limits(plan["usage_limits"])
        if plan["usage_limits"]["rates"]["model"] != plan["model"]:
            raise ValueError("the rate card model must match the plan model")

    if plan["seconds"] <= plan["reserve_seconds"]:
        raise ValueError("the series budget must exceed the preservation reserve")
