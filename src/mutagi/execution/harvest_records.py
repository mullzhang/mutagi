"""Append human harvesting notes to an independent Git branch atomically."""

from __future__ import annotations

import json
import re
import subprocess
import time
from uuid import uuid4

from mutagi.execution.catalog import COMMIT
from mutagi.execution.state import validate_id
from mutagi.project.config import validate_path
from mutagi.project.repository import git, require_identity
from mutagi.runtime.storage import utc

BRANCH = "mutagi/harvests"
REF = "refs/heads/" + BRANCH
KINDS = ("code", "idea", "counterexample")
DECISIONS = ("adopt", "defer")


def branch_revision(repo):
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", REF],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 1:
        return None
    result.check_returncode()
    return result.stdout.strip()


def read_records(repo):
    revision = branch_revision(repo)
    if revision is None:
        return []
    records = []
    for line in git(repo, "ls-tree", revision).splitlines():
        metadata, name = line.split("\t", 1)
        if not metadata.startswith("100644 blob ") or not name.endswith(".json"):
            raise ValueError(f"invalid file on {BRANCH}: {name}")
        identifier = validate_id(name[:-5])
        try:
            record = json.loads(git(repo, "show", revision + ":" + name))
            fields = {
                "id",
                "recorded_at",
                "source",
                "kind",
                "parts",
                "purpose",
                "target",
                "target_commit",
                "decision",
                "verification",
                "note",
            }
            source_fields = {
                "id",
                "saved_commit",
                "implementation_commit",
                "record",
                "catalog_commit",
            }
            if (
                not isinstance(record, dict)
                or set(record) != fields
                or record["id"] != identifier
                or record["kind"] not in KINDS
                or record["decision"] not in DECISIONS
                or not isinstance(record["recorded_at"], str)
                or not isinstance(record["note"], str)
                or not record["note"].strip()
                or not isinstance(record["parts"], list)
                or not all(isinstance(part, str) and part for part in record["parts"])
                or any(
                    record[key] is not None and not isinstance(record[key], str)
                    for key in ("purpose", "target", "target_commit", "verification")
                )
                or not isinstance(record["source"], dict)
                or set(record["source"]) != source_fields
            ):
                raise ValueError("fields do not match the harvesting record format")
            source = record["source"]
            if not isinstance(source["id"], str):
                raise ValueError("the source ID must be a UUID string")
            validate_id(source["id"])
            validate_path(source["record"])
            for part in record["parts"]:
                path, separator, heading = part.partition("#")
                validate_path(path)
                if separator and not heading.strip():
                    raise ValueError("a selected section requires a heading")
            for key in ("saved_commit", "catalog_commit"):
                if not isinstance(source[key], str) or not re.fullmatch(
                    COMMIT, source[key]
                ):
                    raise ValueError(f"invalid {key}")
            for commit in (source["implementation_commit"], record["target_commit"]):
                if commit is not None and (
                    not isinstance(commit, str) or not re.fullmatch(COMMIT, commit)
                ):
                    raise ValueError("invalid implementation or target commit")
        except (ValueError, TypeError) as error:
            raise ValueError(f"invalid harvesting record {name}: {error}") from error
        records.append(record)
    return sorted(records, key=lambda record: (record["recorded_at"], record["id"]))


def decisions_for(records, source):
    return [
        record
        for record in records
        if all(
            record["source"][key] == source[key]
            for key in ("id", "saved_commit", "record")
        )
    ]


def git_input(repo, *args, content):
    return (
        subprocess.check_output(
            ["git", "-C", str(repo), *args],
            input=content,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        .decode()
        .strip()
    )


def append_record(repo, value):
    require_identity(repo)
    if "branch " + REF in git(repo, "worktree", "list", "--porcelain").splitlines():
        raise ValueError(f"leave {BRANCH} unchecked out before recording a decision")
    previous = branch_revision(repo)
    # Validate the existing branch before extending its fixed data format.
    read_records(repo)
    record = {"id": str(uuid4()), "recorded_at": utc(time.time()), **value}
    name = record["id"] + ".json"
    blob = git_input(
        repo,
        "hash-object",
        "-w",
        "--stdin",
        content=(json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode(),
    )
    tree_entries = (
        b""
        if previous is None
        else subprocess.check_output(
            ["git", "-C", str(repo), "ls-tree", "-z", previous],
            stderr=subprocess.PIPE,
            timeout=30,
        )
    )
    tree = git_input(
        repo,
        "mktree",
        "-z",
        content=tree_entries + f"100644 blob {blob}\t{name}\0".encode(),
    )
    parents = [] if previous is None else ["-p", previous]
    commit = git_input(
        repo,
        "commit-tree",
        tree,
        *parents,
        content=f"docs(harvest): record {record['decision']} for {value['source']['id']}\n".encode(),
    )
    # Compare-and-swap keeps concurrent writers from losing another note.
    try:
        git(
            repo,
            "update-ref",
            REF,
            commit,
            previous if previous is not None else "0" * 40,
        )
    except subprocess.CalledProcessError as error:
        if branch_revision(repo) != previous:
            raise RuntimeError(
                "harvesting records changed concurrently; retry the record command"
            ) from error
        raise
    return {"record": record, "branch": BRANCH, "commit": commit, "path": name}
