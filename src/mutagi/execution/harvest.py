"""Prepare trials and portable harvesting prompts without starting an agent."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from mutagi.execution.catalog import read_blob
from mutagi.execution.harvest_records import DECISIONS, KINDS, append_record
from mutagi.project.config import validate_path
from mutagi.project.repository import (
    git,
    repository_root,
    require_private_paths,
    resolve_base,
)
from mutagi.prompts import render_prompt


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"provide a nonempty {name}")
    return value


def selected_parts(repo, source, parts):
    if len(set(parts)) != len(parts):
        raise ValueError("select each file or section only once")
    selected = []
    for part in parts:
        path, separator, heading = part.partition("#")
        validate_path(path)
        content = read_blob(repo, source["saved_commit"], path)
        try:
            text = None if b"\0" in content else content.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if separator:
            if text is None:
                raise ValueError(f"cannot select a heading from a binary file: {path}")
            nonempty(heading, "section heading")
            headings = markdown_headings(text)
            matches = [item for item in headings if item["title"] == heading]
            if len(matches) != 1:
                raise ValueError(
                    f"select exactly one existing heading in {path}: {heading}"
                )
            start = matches[0]
            end = next(
                (
                    item["offset"]
                    for item in headings
                    if item["offset"] > start["offset"]
                    and item["level"] <= start["level"]
                ),
                len(text),
            )
            text = text[start["offset"] : end]
        selected.append({"part": part, "text": text, "bytes": len(content)})
    return selected


def markdown_headings(text):
    headings = []
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if fence is not None:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                fence = None
        elif marker:
            fence = marker[1]
        else:
            heading = re.match(r"^(#{1,6}) (.+?)\s*$", line)
            if heading:
                headings.append(
                    {"title": heading[2], "level": len(heading[1]), "offset": offset}
                )
        offset += len(line)
    return headings


def target_directory(target):
    if target is None:
        return None
    path = Path(target).resolve()
    if not path.is_dir():
        raise ValueError(f"target must be an existing directory: {path}")
    return str(path)


def checkout(repo, source):
    require_private_paths(repo)
    path = repo / ".mutagi/worktrees" / ("harvest-" + source["id"])
    if path.is_symlink():
        raise ValueError(f"the trial worktree must not be a symbolic link: {path}")
    if path.exists():
        repository_root(path, exact=True)
        common = Path(git(repo, "rev-parse", "--git-common-dir"))
        actual = Path(git(path, "rev-parse", "--git-common-dir"))
        if (
            (repo / common).resolve() != (path / actual).resolve()
            or git(path, "rev-parse", "HEAD") != source["saved_commit"]
            or git(path, "branch", "--show-current")
        ):
            raise ValueError(
                "the existing trial differs from the saved detached worktree; its contents were retained"
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--detach", str(path), source["saved_commit"])
    return {
        "id": source["id"],
        "path": str(path),
        "saved_commit": source["saved_commit"],
        "record": str(path / source["record"]),
        "dirty": bool(git(path, "--no-optional-locks", "status", "--porcelain")),
    }


def quoted(text):
    longest = max((len(match[0]) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return fence + "text\n" + text + ("" if text.endswith("\n") else "\n") + fence


def prepare(repo, source, *, purpose, kind, parts, target):
    nonempty(purpose, "purpose")
    target = target_directory(target)
    selected = selected_parts(repo, source, parts)
    excerpts = []
    for part in selected:
        content = (
            quoted(part["text"])
            if part["text"] is not None
            else f"Binary file ({part['bytes']} bytes); retrieve it from the fixed source revision."
        )
        excerpts.append("### " + part["part"] + "\n\n" + content)
    work = {
        "code": "Adapt the selected implementation to the destination's API and dependencies. Check existing behavior and test the destination change.",
        "idea": "Explain the selected idea in the destination's design notes, retaining its evidence, applicability conditions, and limitations.",
        "counterexample": "Adapt the selected inputs and expected observations into destination tests. Check that their assumptions fit the destination.",
    }[kind]
    return render_prompt(
        "harvest",
        purpose=purpose,
        kind=kind,
        target=target
        if target is not None
        else "Not specified. Establish the destination before making changes.",
        source=source,
        implementation=source["implementation_commit"]
        if source["implementation_commit"] is not None
        else "Not recorded; the observed implementation is unverified.",
        parents=", ".join(source["parent_ids"]) if source["parent_ids"] else "none",
        scope="\n".join("- " + part for part in parts)
        if parts
        else "No specific file or section selected; choose the relevant parts before changing the destination.",
        repo=str(repo),
        record_command=shlex.join(
            [
                "git",
                "-C",
                str(repo),
                "show",
                source["saved_commit"] + ":" + source["record"],
            ]
        ),
        diff_command=shlex.join(
            [
                "git",
                "-C",
                str(repo),
                "diff",
                source["base_commit"],
                source["saved_commit"],
                "--",
            ]
        ),
        changes="\n".join("- " + path for path in source["changed_files"]),
        excerpts="\n\n".join(excerpts)
        if excerpts
        else "No source excerpts selected. Inspect the fixed source revision as needed.",
        record=quoted(source["record_text"]),
        work=work,
    )


def record(
    repo,
    source,
    *,
    kind,
    parts,
    purpose,
    target,
    target_commit,
    decision,
    verification,
    note,
):
    if kind not in KINDS or decision not in DECISIONS:
        raise ValueError("choose a supported result kind and adoption decision")
    nonempty(note, "decision note")
    if purpose is not None:
        nonempty(purpose, "purpose")
    if verification is not None:
        nonempty(verification, "verification report")
    selected_parts(repo, source, parts)
    target = target_directory(target)
    if target_commit is not None:
        if target is None:
            raise ValueError("--target-commit requires --target")
        target_commit = resolve_base(Path(target), target_commit)
    return append_record(
        repo,
        {
            "source": {
                key: source[key]
                for key in (
                    "id",
                    "saved_commit",
                    "implementation_commit",
                    "record",
                    "catalog_commit",
                )
            },
            "kind": kind,
            "parts": parts,
            "purpose": purpose,
            "target": target,
            "target_commit": target_commit,
            "decision": decision,
            "verification": verification,
            "note": note,
        },
    )
