#!/usr/bin/env python3
"""Run independent CLI sessions and one knowledge integration within a bounded round."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import signal
import subprocess
import time
from uuid import uuid4

from mutagi.run_experiment import (
    Runner,
    atomic_json,
    create,
    git,
    lock,
    require_clean,
    require_series_owner,
    service_pause_requested,
    utc,
    validate_id,
)

from mutagi.prompts import render_prompt
from mutagi.project import matches, load_project
from mutagi.series_policy import proposal_schema, validate_proposal

INTEGRATION_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in ("summary", "limits", "checks")},
    "required": ["summary", "limits", "checks"],
    "additionalProperties": False,
}


def require_catalog_entry(content, source):
    marker = f"<!-- forest-run:{source['id']} -->"
    if marker not in content:
        raise RuntimeError("a saved experiment is missing from the shared catalog")

    entry = content.split(marker, 1)[1].split("<!-- forest-run:", 1)[0]
    if source["saved_commit"] not in entry or source["result"]["record"] not in entry:
        raise RuntimeError("the catalog entry differs from the saved experiment")


class Integration(Runner):
    def directory(self):
        return self.worktree

    def allowed(self, path, directory):
        return matches(path, self.project["integration_paths"], self.state["id"])

    def prepare(self):
        super().prepare()
        self.state["instruction_hashes"][self.project["prompts"]["integrate"]] = (
            hashlib.sha256(
                (self.worktree / self.project["prompts"]["integrate"]).read_bytes()
            ).hexdigest()
        )
        self.save()

    def prompt(self):
        s = self.state
        adaptation = ""
        if "adaptation" in s:
            adaptation = (
                "Return a structured proposal in next_round. In reason, cite this round's observations and explain the proposal. Use tasks to choose prompts and the mix of independent and inherited explorations. parent is an earlier item's index in the same next round, or null. Choose counts and times within the following authorized bounds. The controller will validate and apply the proposal automatically. Do not change the model, permissions, total round count, or overall deadline.\n"
                + json.dumps(s["adaptation"], ensure_ascii=False)
            )

        return render_prompt(
            "integrate",
            state=s,
            project=self.project,
            project_prompt=self.project["prompts"]["integrate"],
            exploration_deadline=utc(s["exploration_deadline"]),
            deadline=utc(s["deadline"]),
            task=(self.run / "task.txt").read_text(),
            adaptation=adaptation,
        )

    def collect(self):
        s = self.state
        paths = self.changed()
        self.snapshot([p for p in paths if self.allowed(p, self.worktree)])
        if any(
            not self.allowed(p, self.worktree)
            or not (self.worktree / p).resolve().is_relative_to(self.worktree.resolve())
            for p in paths
        ):
            raise RuntimeError("out-of-scope integration changes require review")

        if git(self.worktree, "branch", "--show-current") != s["branch"]:
            raise RuntimeError("integration changed its assigned branch")

        git(self.worktree, "merge-base", "--is-ancestor", s["base"], "HEAD")
        self.read_session_id()
        if s["reason"] == "completed":
            try:
                result = json.loads((self.run / "result.json").read_text())
                if (
                    not isinstance(result, dict)
                    or set(result)
                    != set(
                        json.loads((self.run / "schema.json").read_text())["required"]
                    )
                    or any(
                        not isinstance(result[k], str) or not result[k].strip()
                        for k in INTEGRATION_SCHEMA["required"]
                    )
                    or s["thread_id"] is None
                ):
                    raise ValueError("invalid integration result")

                if "adaptation" in s:
                    validate_proposal(result["next_round"], s["adaptation"])

                s["result"] = result
            except (ValueError, OSError) as error:
                s.update(reason="invalid_result", result_error=str(error))

        s["phase"] = "saving"
        self.save()

    def preservation_message(self):
        return (
            "docs(knowledge): preserve round integration",
            f"Preserve integration {self.state['id']} with {self.state['reason']} outcome.\n\n"
            "Validation: knowledge-only scope, fixed base and git diff --check.",
        )

    def register(self):
        s = self.state
        if (
            git(self.worktree, "rev-parse", "HEAD") != s["saved_commit"]
            or git(self.repo, "rev-parse", s["branch"]) != s["saved_commit"]
        ):
            raise RuntimeError("the saved integration changed before publication")

        paths = (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "diff",
                    "--name-only",
                    "-z",
                    s["base"],
                    s["saved_commit"],
                ]
            )
            .decode()
            .split("\0")
        )
        for path in filter(None, paths):
            if not self.allowed(path, self.worktree) or git(
                self.repo, "ls-tree", s["saved_commit"], "--", path
            ).startswith("120000"):
                raise RuntimeError(
                    "the saved integration contains out-of-scope paths or symbolic links"
                )

        if s["reason"] != "completed":
            s.update(phase="registered", published=False, registry_commit=None)
            self.save()
            return

        catalog = git(
            self.repo, "show", s["saved_commit"] + ":" + self.project["catalog"]
        )
        task = json.loads((self.run / "task.txt").read_text())
        for source in task["sources"] + task.get("prior_sources", []):
            require_catalog_entry(catalog, source)

        if git(self.repo, "branch", "--show-current") != s["shared_branch"]:
            raise RuntimeError("the shared branch changed before integration publication")

        expected_tree = git(self.repo, "rev-parse", s["saved_commit"] + "^{tree}")
        head = git(self.repo, "rev-parse", "HEAD")
        marker = f"Forest-integration: {s['id']}"
        if head != s["base"]:
            if (
                marker not in git(self.repo, "log", "-1", "--format=%B")
                or git(self.repo, "rev-parse", "HEAD^") != s["base"]
                or git(self.repo, "rev-parse", "HEAD^{tree}") != expected_tree
            ):
                raise RuntimeError("shared history changed after integration started")

            require_clean(self.repo)
        else:
            if git(self.repo, "status", "--porcelain"):
                # The previous application may have stopped before committing.
                if (
                    git(self.repo, "write-tree") != expected_tree
                    or git(self.repo, "diff", "--name-only")
                    or git(self.repo, "ls-files", "--others", "--exclude-standard")
                ):
                    raise RuntimeError("shared working tree changes differ from the saved integration")
            elif git(self.repo, "rev-parse", "HEAD^{tree}") != expected_tree:
                patch = subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(self.repo),
                        "diff",
                        "--binary",
                        s["base"],
                        s["saved_commit"],
                    ]
                )
                subprocess.run(
                    ["git", "-C", str(self.repo), "apply", "--index", "-"],
                    input=patch,
                    check=True,
                    timeout=30,
                )

            if git(self.repo, "write-tree") != expected_tree:
                raise RuntimeError(
                    "the published integration tree differs from the saved tree"
                )

            if git(self.repo, "status", "--porcelain"):
                git(self.repo, "diff", "--cached", "--check")
                self.local(
                    [
                        "git",
                        "-C",
                        str(self.repo),
                        "commit",
                        "-m",
                        "docs(knowledge): integrate completed CLI round",
                        "-m",
                        f"{marker}\n\nApply the saved knowledge-only tree. "
                        "Validation: exact tree equality, scope and git diff --check; human adoption stays unreviewed.",
                    ],
                    self.repo,
                    "integrate",
                )

            require_clean(self.repo)

        s.update(
            phase="registered",
            published=True,
            registry_commit=git(self.repo, "rev-parse", "HEAD"),
        )
        self.save()


def validate_plan(plan):
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

    if plan["effort"] not in ("low", "medium", "high", "xhigh", "max", "ultra"):
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
    validate_plan(plan)
    require_series_owner(repo, series_id)
    require_clean(repo)
    for path in (repo / ".mutagi/runs" / "rounds").glob("*/state.json"):
        if json.loads(path.read_text())["phase"] not in (
            "completed",
            "budget_exhausted",
            "usage_exhausted",
            "usage_unknown",
        ):
            raise RuntimeError("resume the unfinished round before starting another")

    for path in (repo / ".mutagi/runs").glob("*/state.json"):
        if json.loads(path.read_text())["phase"] != "registered":
            raise RuntimeError("finish the unfinished run before starting a round")

    plan = dict(plan, base=git(repo, "rev-parse", plan["base"] + "^{commit}"))
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
                    from mutagi.usage_budget import remaining

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
            task = self.directory / (item["id"] + ".txt")
            task.write_text(allocation["prompt"])
            create(
                self.repo,
                allocation["base"],
                task,
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
                raise RuntimeError("cannot start the next session while preservation is incomplete")

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


def main():
    parser = argparse.ArgumentParser(prog="mutagi round", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start").add_argument("--plan", type=Path, required=True)
    for name in ("resume", "status"):
        commands.add_parser(name).add_argument("id", type=validate_id)

    args = parser.parse_args()
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()
    if args.command == "status":
        print((repo / ".mutagi/runs" / "rounds" / args.id / "state.json").read_text())
        return

    def interrupted(*_):
        raise KeyboardInterrupt("round interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    with lock(repo):
        directory = (
            create_round(repo, json.loads(args.plan.read_text()))
            if args.command == "start"
            else repo / ".mutagi/runs" / "rounds" / args.id
        )
        print("Round: " + directory.name, flush=True)
        controller = Round(directory)
        if controller.state.get("series_id") is not None:
            raise RuntimeError(
                "use mutagi series resume for a round owned by a series"
            )

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
                f"Incomplete: {error}\nresume round {directory.name}", flush=True
            )
            raise SystemExit(1) from error

        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
