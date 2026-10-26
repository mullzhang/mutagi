from __future__ import annotations


LIMITS = {
    "max_tasks": 3,
    "min_seconds": 20,
    "max_round_seconds": 600,
    "max_experiment_seconds": 120,
    "max_integration_seconds": 120,
}


PROPOSAL = {
    "reason": "Previous finite observation motivates a different premise",
    "tasks": [{"prompt": "Change the premise", "parent": None}],
    "round_seconds": 180,
    "experiment_seconds": 60,
    "integration_seconds": 60,
}


RATES = {
    "input": 250,
    "cached_input": 25,
    "output": 1250,
    "unit": "credits",
    "source": "https://learn.chatgpt.com/docs/pricing#token-rates",
    "model": "gpt-6-astra",
}


def round_plan():
    return {
        "base": "HEAD",
        "model": "gpt-6-astra",
        "effort": "medium",
        "seconds": 600,
        "experiment_seconds": 60,
        "integration_seconds": 60,
        "reserve_seconds": 10,
        "tasks": [
            {"prompt": "Choose a finite question.", "parent": None},
            {"prompt": "Use the parent to ask a new question.", "parent": 0},
        ],
    }


def plan_for(kind):
    plan = {
        "base": "HEAD",
        "effort": "medium",
        "seconds": 600,
        "experiment_seconds": 60,
        "integration_seconds": 60,
        "reserve_seconds": 10,
        "tasks": [{"prompt": "Check a finite case.", "parent": None}],
    }
    if kind == "series":
        tasks = plan.pop("tasks")
        plan.update(round_seconds=300, rounds=[{"tasks": tasks}, {"tasks": tasks}])
    return plan


def plan_args(kind, *extra):
    flags = [
        "--base",
        "HEAD",
        "--model",
        "selected-model",
        "--effort",
        "high",
        "--seconds",
        "600",
        "--experiment-seconds",
        "60",
        "--integration-seconds",
        "60",
        "--reserve-seconds",
        "10",
    ]
    if kind == "series":
        flags += ["--round-seconds", "300"]
    return flags + list(extra)
