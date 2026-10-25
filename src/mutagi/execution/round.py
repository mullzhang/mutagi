"""Allocate and recover one round of exploration and knowledge integration."""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from mutagi.execution.experiment import Runner, create
from mutagi.execution.integration import (
    INTEGRATION_SCHEMA,
    Integration,
    require_catalog_entry,
)
from mutagi.execution.plans import validate_round
from mutagi.execution.policy import proposal_schema
from mutagi.execution.state import (
    ROUND_TERMINAL,
    require_series_owner,
    service_pause_requested,
    validate_id,
)
from mutagi.project.config import load_project
from mutagi.project.repository import git, require_clean, require_ready
from mutagi.runtime.storage import atomic_json, utc


def create_round(
    repo,
    plan,
    *,
    identifier=None,
    started=None,
    series_id=None,
    prior_sources=None,
    adaptation=None,
):
    validate_round(plan)
    require_series_owner(repo, series_id)
    _, base, _ = require_ready(repo, plan["base"])
    for path in (repo / ".mutagi/runs" / "rounds").glob("*/state.json"):
        if json.loads(path.read_text())["phase"] not in ROUND_TERMINAL:
            raise RuntimeError("resume the unfinished round before starting another")

    for path in (repo / ".mutagi/runs").glob("*/state.json"):
        if json.loads(path.read_text())["phase"] != "registered":
            raise RuntimeError("finish the unfinished run before starting a round")

    plan = dict(plan, base=base)
    identifier = str(uuid4()) if identifier is None else validate_id(identifier)
    directory = repo / ".mutagi/runs" / "rounds" / identifier
    if directory.exists():
        if any(p.name != "state.tmp" for p in directory.iterdir()):
            raise RuntimeError("the existing round is not an incomplete allocation")
    else:
        directory.mkdir(parents=True)

    started = time.time() if started is None else started
    state = {
        "id": identifier,
        "repo": str(repo),
        "plan": plan,
        "started_at": utc(started),
        "deadline": started + plan["seconds"],
        "phase": "running",
        "next": 0,
        "jobs": [
            {"id": str(uuid4()), "kind": "exploration", "allocation": None}
            for _ in plan["tasks"]
        ]
        + [{"id": str(uuid4()), "kind": "integration", "allocation": None}],
    }
    if series_id is not None:
        state["series_id"] = series_id
        state["prior_sources"] = prior_sources

    if adaptation is not None:
        state["adaptation"] = adaptation

    atomic_json(directory / "state.json", state)
    return directory


class Round:
    def __init__(self, directory):
        self.directory = directory
        self.state = json.loads((directory / "state.json").read_text())
        self.repo = Path(self.state["repo"])

    def save(self):
        atomic_json(self.directory / "state.json", self.state)

    def allocate(self, index):
        s = self.state
        item = s["jobs"][index]
        plan = s["plan"]
        integration = item["kind"] == "integration"
        catalog = git(self.repo, "show", "HEAD:" + load_project(self.repo)["catalog"])
        for previous in s["jobs"][:index]:
            source = json.loads(
                (self.repo / ".mutagi/runs" / previous["id"] / "state.json").read_text()
            )
            if (
                source["phase"] != "registered"
                or git(self.repo, "rev-parse", source["branch"])
                != source["saved_commit"]
            ):
                raise RuntimeError(
                    "cannot start the next session while experiments are unfinished or changed"
                )

            require_clean(Path(source["worktree"]))
            require_catalog_entry(catalog, source)

        allocation = item["allocation"]
        if allocation is None:
            usage_budget = None
            if s.get("series_id") is not None:
                series = json.loads(
                    (
                        self.repo
                        / ".mutagi/runs/series"
                        / s["series_id"]
                        / "state.json"
                    ).read_text()
                )
                if "usage_limits" in series["plan"]:
                    from mutagi.execution.usage import remaining

                    balance = remaining(self.repo, series)
                    if (
                        balance["unknown_sessions"]
                        or balance["tokens"] <= 0
                        or float(balance["cost"]) <= 0
                    ):
                        s.update(
                            phase="usage_unknown"
                            if balance["unknown_sessions"]
                            else "usage_exhausted",
                            usage=balance,
                            not_started=[j["id"] for j in s["jobs"][index:]],
                        )
                        self.save()
                        return None

                    usage_budget = {
                        "tokens": balance["tokens"],
                        "cost": balance["cost"],
                        "rates": series["plan"]["usage_limits"]["rates"],
                    }

            started = time.time()
            seconds = min(
                plan["integration_seconds"]
                if integration
                else plan["experiment_seconds"],
                s["deadline"] - started,
            )
            if seconds <= plan["reserve_seconds"]:
                s.update(
                    phase="budget_exhausted",
                    not_started=[j["id"] for j in s["jobs"][index:]],
                )
                self.save()
                return None

            parent = None if integration else plan["tasks"][index]["parent"]
            parent_state = None
            if parent is not None:
                parent_state = json.loads(
                    (
                        self.repo
                        / ".mutagi/runs"
                        / s["jobs"][parent]["id"]
                        / "state.json"
                    ).read_text()
                )

            sources = (
                [
                    json.loads(
                        (
                            self.repo / ".mutagi/runs" / j["id"] / "state.json"
                        ).read_text()
                    )
                    for j in s["jobs"][:index]
                ]
                if integration
                else []
            )
            allocation = {
                "started": started,
                "seconds": seconds,
                "base": git(self.repo, "rev-parse", "HEAD")
                if integration
                else (parent_state["saved_commit"] if parent_state else plan["base"]),
                "parent_id": parent_state["id"] if parent_state else None,
                "parent_record": parent_state["result"]["record"]
                if parent_state
                else None,
                "prompt": json.dumps(
                    {
                        "round_id": s["id"],
                        "started_at": s["started_at"],
                        "deadline": utc(s["deadline"]),
                        "planned": len(plan["tasks"]),
                        "sources": [
                            {
                                k: source[k]
                                for k in (
                                    "id",
                                    "parent_ids",
                                    "base",
                                    "saved_commit",
                                    "result",
                                    "reason",
                                )
                            }
                            for source in sources
                        ],
                        "prior_sources": s.get("prior_sources", []),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                if integration
                else plan["tasks"][index]["prompt"],
            }
            if usage_budget is not None:
                allocation["usage_budget"] = usage_budget

            item["allocation"] = allocation
            self.save()  # Persist the ID and original deadline before allocating the run.

        run = self.repo / ".mutagi/runs" / item["id"]
        if not (run / "state.json").exists():
            create(
                self.repo,
                allocation["base"],
                allocation["prompt"],
                plan["model"],
                plan["effort"],
                allocation["seconds"],
                plan["reserve_seconds"],
                allocation["parent_id"],
                allocation["parent_record"],
                identifier=item["id"],
                started=allocation["started"],
                series_id=s.get("series_id"),
            )

        job = Integration(run) if integration else Runner(run)
        if (
            integration
            and job.state["phase"] == "preparing"
            and not job.worktree.exists()
        ):
            job.state["branch"] = "codex/integrate-" + item["id"]
            job.save()
            schema = INTEGRATION_SCHEMA
            if "adaptation" in s:
                job.state["adaptation"] = s["adaptation"]
                job.save()
                schema = {
                    **schema,
                    "properties": {
                        **schema["properties"],
                        "next_round": proposal_schema(),
                    },
                    "required": [*schema["required"], "next_round"],
                }

            atomic_json(run / "schema.json", schema)

        if "usage_budget" in allocation:
            if "usage_budget" not in job.state and job.state["phase"] == "preparing":
                job.state["usage_budget"] = allocation["usage_budget"]
                job.save()

            if job.state["usage_budget"] != allocation["usage_budget"]:
                raise RuntimeError("the usage budget changed after assignment")

        expected = {
            "id": item["id"],
            "repo": str(self.repo),
            "worktree": str(self.repo / ".mutagi/worktrees" / item["id"]),
            "base": allocation["base"],
            "started_at": utc(allocation["started"]),
            "deadline": allocation["started"] + allocation["seconds"],
            "branch": ("codex/integrate-" if integration else "codex/explore-")
            + item["id"],
        }
        if any(job.state[key] != value for key, value in expected.items()):
            raise RuntimeError(
                "the assigned run differs from the round's fixed assignment"
            )

        if (run / "task.txt").read_text() != allocation["prompt"]:
            raise RuntimeError("tasks changed after the round assignment")

        return job

    def advance(self, *, recovery=False):
        s = self.state
        if s["phase"] in (
            "completed",
            "budget_exhausted",
            "usage_exhausted",
            "usage_unknown",
        ):
            return s

        if s["phase"] == "stopped":
            if not recovery:
                return s

            s["phase"] = "running"
            self.save()

        while s["next"] < len(s["jobs"]):
            index = s["next"]
            item = s["jobs"][index]
            had_state = (
                self.repo / ".mutagi/runs" / item["id"] / "state.json"
            ).exists()
            if not had_state and service_pause_requested(self.repo, s.get("series_id")):
                s["phase"] = "stopped"
                self.save()
                return s

            job = self.allocate(index)
            if job is None:
                return s

            result = job.advance(recovery=recovery and had_state)
            if result["phase"] != "registered":
                raise RuntimeError(
                    "cannot start the next session while preservation is incomplete"
                )

            if git(self.repo, "rev-parse", result["branch"]) != result["saved_commit"]:
                raise RuntimeError(
                    "the saved branch moved before the next session started"
                )

            require_clean(job.worktree)
            require_clean(self.repo)
            item.update(
                saved_commit=result["saved_commit"],
                registry_commit=result["registry_commit"],
                reason=result["reason"],
            )
            if item["kind"] == "integration":
                item["published"] = result["published"]

            s["next"] += 1
            self.save()
            if result["reason"] == "interrupted":
                s["phase"] = "stopped"
                self.save()
                return s

            recovery = False

        s.update(
            phase="completed",
            completed_at=utc(time.time()),
            integration="published" if s["jobs"][-1]["published"] else "deferred",
        )
        self.save()
        return s
