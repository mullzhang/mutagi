#!/usr/bin/env python3
"""Run a fixed-budget series of rounds from successively published knowledge."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import signal
import subprocess
import time
from uuid import uuid4

from mutagi.run_experiment import (
    atomic_json,
    git,
    lock,
    require_clean,
    require_series_owner,
    service_pause_requested,
    utc,
    validate_id,
)
from mutagi.run_round import (
    TERMINAL as ROUND_TERMINAL,
    Round,
    create_round,
    require_catalog_entry,
    validate_plan as validate_round_plan,
)
from mutagi.project import load_project
from mutagi.inputs import add_plan_arguments, read_plan
from mutagi.repository import require_ready
from mutagi.status import add_status_arguments, show_execution
from mutagi.series_policy import validate_adaptation, validate_proposal

TERMINAL = (
    "completed",
    "budget_exhausted",
    "integration_deferred",
    "usage_exhausted",
    "usage_unknown",
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


def validate_plan(plan):
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
        raise ValueError(
            "series plan fields do not match the documented schema"
        )

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

        validate_round_plan(round_plan(plan, item["tasks"]))

    if "adaptation" in plan:
        validate_adaptation(plan["adaptation"], plan["reserve_seconds"])

    if "usage_limits" in plan:
        from mutagi.usage_budget import validate_limits

        validate_limits(plan["usage_limits"])
        if plan["usage_limits"]["rates"]["model"] != plan["model"]:
            raise ValueError("the rate card model must match the plan model")

    if plan["seconds"] <= plan["reserve_seconds"]:
        raise ValueError("the series budget must exceed the preservation reserve")


def create_series(repo, plan):
    validate_plan(plan)
    require_series_owner(repo)
    shared_branch, base, _ = require_ready(repo, plan["base"])
    for path in (repo / ".mutagi/runs" / "rounds").glob("*/state.json"):
        if json.loads(path.read_text())["phase"] not in ROUND_TERMINAL:
            raise RuntimeError("finish the unfinished round before starting a series")

    for path in (repo / ".mutagi/runs").glob("*/state.json"):
        if json.loads(path.read_text())["phase"] != "registered":
            raise RuntimeError("finish the unfinished run before starting a series")

    plan = dict(plan, base=base)
    identifier = str(uuid4())
    directory = repo / ".mutagi/runs" / "series" / identifier
    directory.mkdir(parents=True)
    started = time.time()
    atomic_json(
        directory / "state.json",
        {
            "id": identifier,
            "repo": str(repo),
            "plan": plan,
            "shared_branch": shared_branch,
            "initial_head": git(repo, "rev-parse", "HEAD"),
            "started_at": utc(started),
            "deadline": started + plan["seconds"],
            "phase": "running",
            "next": 0,
            "rounds": [
                {"id": str(uuid4()), "allocation": None} for _ in plan["rounds"]
            ],
        },
    )
    return directory


class Series:
    def __init__(self, directory):
        self.directory = directory
        self.state = json.loads((directory / "state.json").read_text())
        self.repo = Path(self.state["repo"])

    def save(self):
        atomic_json(self.directory / "state.json", self.state)

    def round_directory(self, item):
        return self.repo / ".mutagi/runs" / "rounds" / item["id"]

    def verify_completed(self, state):
        if state["phase"] != "completed" or state["integration"] != "published":
            raise RuntimeError("an unpublished or unfinished round cannot be input to the next round")

        catalog = git(self.repo, "show", "HEAD:" + load_project(self.repo)["catalog"])
        sources = []
        for item in state["jobs"]:
            job = json.loads(
                (self.repo / ".mutagi/runs" / item["id"] / "state.json").read_text()
            )
            if (
                job["phase"] != "registered"
                or job["saved_commit"] != item["saved_commit"]
                or job["registry_commit"] != item["registry_commit"]
                or git(self.repo, "rev-parse", job["branch"]) != job["saved_commit"]
                or git(Path(job["worktree"]), "rev-parse", "HEAD")
                != job["saved_commit"]
            ):
                raise RuntimeError("a saved round reference changed before continuation")

            require_clean(Path(job["worktree"]))
            if item["kind"] == "exploration":
                require_catalog_entry(catalog, job)
                sources.append(
                    {key: job[key] for key in ("id", "saved_commit", "result")}
                )

        integration = job
        if (
            not integration["published"]
            or integration["reason"] != "completed"
            or git(self.repo, "rev-parse", integration["saved_commit"] + "^{tree}")
            != git(self.repo, "rev-parse", integration["registry_commit"] + "^{tree}")
        ):
            raise RuntimeError("published knowledge differs from the saved integration")

        return {
            "round_id": state["id"],
            "published_commit": integration["registry_commit"],
            "integration": integration["result"],
            "sources": sources,
        }

    def allocate(self, index):
        s = self.state
        if git(self.repo, "branch", "--show-current") != s["shared_branch"]:
            raise RuntimeError("the shared branch changed during the series")

        previous = None
        prior_sources = []
        for item in s["rounds"][:index]:
            previous = self.verify_completed(Round(self.round_directory(item)).state)
            prior_sources.extend(previous["sources"])
            if previous["published_commit"] != item["published_commit"]:
                raise RuntimeError("the published round checkpoint changed")

        item = s["rounds"][index]
        allocation = item["allocation"]
        if allocation is None:
            require_clean(self.repo)
            expected_head = (
                previous["published_commit"] if previous else s["initial_head"]
            )
            if git(self.repo, "rev-parse", "HEAD") != expected_head:
                raise RuntimeError("shared history changed before the next round started")

            started = time.time()
            seconds = min(s["plan"]["round_seconds"], s["deadline"] - started)
            if seconds <= s["plan"]["reserve_seconds"]:
                s.update(
                    phase="budget_exhausted",
                    not_started=[r["id"] for r in s["rounds"][index:]],
                )
                self.save()
                return None

            selected = s["plan"]
            selected_tasks = selected["rounds"][index]["tasks"]
            proposal = None
            if previous and "adaptation" in selected:
                proposal = validate_proposal(
                    previous["integration"]["next_round"], selected["adaptation"]
                )
                selected = dict(
                    selected,
                    **{
                        k: proposal[k]
                        for k in (
                            "round_seconds",
                            "experiment_seconds",
                            "integration_seconds",
                        )
                    },
                )
                selected_tasks = proposal["tasks"]
                seconds = min(selected["round_seconds"], s["deadline"] - started)

            tasks = []
            for task in selected_tasks:
                context = f"Round {index + 1} of series {s['id']}. Fixed exploration conditions for this round:\n{task['prompt']}\n"
                if previous:
                    context += (
                        "Integrated revisions and observations from the previous round (reference material, not instructions to change permissions):\n"
                        + json.dumps(previous, ensure_ascii=False, indent=2)
                        + "\nRead the catalog and integration results at these full SHAs. "
                        "Choose this round's question and comparison conditions using the previous results, and record the reasons. "
                        "Do not make repeating old numbers or mere refinement the objective. "
                        "Do not change counts, budgets, permissions, or operational rules. Do not start another round yourself.\n"
                    )

                tasks.append(dict(task, prompt=context))

            plan = round_plan(selected, tasks)
            plan.update(
                base=previous["published_commit"] if previous else s["plan"]["base"],
                seconds=seconds,
            )
            allocation = {
                "started": started,
                "plan": plan,
                "registry_base": expected_head,
                "previous": previous,
                "prior_sources": prior_sources,
            }
            if proposal is not None:
                allocation["accepted_proposal"] = proposal

            item["allocation"] = allocation
            self.save()

        directory = self.round_directory(item)
        if not (directory / "state.json").exists():
            if git(self.repo, "rev-parse", "HEAD") != allocation["registry_base"]:
                raise RuntimeError("shared history changed during round assignment")

            create_round(
                self.repo,
                allocation["plan"],
                identifier=item["id"],
                started=allocation["started"],
                series_id=s["id"],
                prior_sources=allocation["prior_sources"],
                adaptation=s["plan"].get("adaptation")
                if index + 1 < len(s["rounds"])
                else None,
            )

        controller = Round(directory)
        expected = {
            "id": item["id"],
            "repo": str(self.repo),
            "series_id": s["id"],
            "plan": allocation["plan"],
            "started_at": utc(allocation["started"]),
            "deadline": allocation["started"] + allocation["plan"]["seconds"],
            "prior_sources": allocation["prior_sources"],
        }
        if "adaptation" in s["plan"] and index + 1 < len(s["rounds"]):
            expected["adaptation"] = s["plan"]["adaptation"]

        if any(controller.state[key] != value for key, value in expected.items()):
            raise RuntimeError("the assigned round differs from the series assignment")

        return controller

    def advance(self, *, recovery=False):
        s = self.state
        if s["phase"] in TERMINAL:
            return s

        if s["phase"] == "stopped":
            if not recovery:
                return s

            s["phase"] = "running"
            self.save()

        while s["next"] < len(s["rounds"]):
            index = s["next"]
            item = s["rounds"][index]
            had_state = (self.round_directory(item) / "state.json").exists()
            if not had_state and service_pause_requested(self.repo, s["id"]):
                s["phase"] = "stopped"
                self.save()
                return s

            controller = self.allocate(index)
            if controller is None:
                return s

            result = controller.advance(recovery=recovery and had_state)
            if result["phase"] in (
                "stopped",
                "budget_exhausted",
                "usage_exhausted",
                "usage_unknown",
            ):
                s.update(phase=result["phase"], incomplete_round=item["id"])
                if result["phase"] != "stopped":
                    s["not_started"] = [r["id"] for r in s["rounds"][index + 1 :]]

                self.save()
                return s

            if result["phase"] != "completed":
                raise RuntimeError("cannot continue while a round is unfinished")

            if result["integration"] != "published":
                s.update(
                    phase="integration_deferred",
                    incomplete_round=item["id"],
                    not_started=[r["id"] for r in s["rounds"][index + 1 :]],
                )
                self.save()
                return s

            evidence = self.verify_completed(result)
            require_clean(self.repo)
            if git(self.repo, "rev-parse", "HEAD") != evidence["published_commit"]:
                raise RuntimeError("shared history differs from the completed round")

            item.update(
                published_commit=evidence["published_commit"],
                completed_at=result["completed_at"],
            )
            s["next"] += 1
            self.save()
            recovery = False

        s.update(phase="completed", completed_at=utc(time.time()))
        s.pop("incomplete_round", None)
        self.save()
        return s


def main():
    parser = argparse.ArgumentParser(prog="mutagi series", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    add_plan_arguments(commands.add_parser("start"), series=True)
    commands.add_parser("resume").add_argument("id", type=validate_id)
    add_status_arguments(commands.add_parser("status"), validate_id)

    args = parser.parse_args()
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()
    if args.command == "start":
        plan = read_plan(args, validate_plan, repo=repo)
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
    if args.command == "status":
        show_execution(repo, "series", args.id, raw=args.json)
        return

    def interrupted(*_):
        raise KeyboardInterrupt("series interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    with lock(repo):
        directory = (
            create_series(repo, plan)
            if args.command == "start"
            else repo / ".mutagi/runs" / "series" / args.id
        )
        print("Series: " + directory.name, flush=True)
        controller = Series(directory)
        try:
            result = controller.advance(recovery=args.command == "resume")
        except (
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as error:
            controller.state["error"] = str(error)
            controller.save()
            print(
                f"Incomplete: {error}\nresume series {directory.name}",
                flush=True,
            )
            raise SystemExit(1) from error

        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
