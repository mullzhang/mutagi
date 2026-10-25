"""Preserve and publish integration of completed exploration records."""

from __future__ import annotations

import hashlib
import json
import subprocess

from mutagi.execution.experiment import Runner
from mutagi.execution.policy import validate_proposal
from mutagi.project.config import matches
from mutagi.project.repository import git, require_clean
from mutagi.prompts import render_prompt
from mutagi.runtime.storage import utc

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
            raise RuntimeError(
                "the shared branch changed before integration publication"
            )

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
                    raise RuntimeError(
                        "shared working tree changes differ from the saved integration"
                    )
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
