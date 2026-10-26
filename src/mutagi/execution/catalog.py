"""Read committed catalog registrations and their immutable observation records."""

from __future__ import annotations

import re
import subprocess

from mutagi.execution.records import (
    OBSERVATION_STATUSES,
    record_field,
    record_frontmatter,
    record_parents,
)
from mutagi.execution.state import validate_id
from mutagi.project.config import validate_path
from mutagi.project.repository import (
    git,
    project_at,
    require_regular_file,
    resolve_base,
)

COMMIT = r"[0-9a-f]{40}"
MARKER = re.compile(r"^<!-- forest-run:([^\n]+) -->$", re.M)


def catalog_entries(repo):
    revision = git(repo, "rev-parse", "HEAD")
    project = project_at(repo, revision)
    content = git(repo, "show", revision + ":" + project["catalog"])
    markers = list(MARKER.finditer(content))
    entries = []
    seen = set()
    for i, marker in enumerate(markers):
        identifier = validate_id(marker[1])
        if identifier in seen:
            raise ValueError(f"duplicate experiment ID in the catalog: {identifier}")
        seen.add(identifier)
        end = markers[i + 1].start() if i + 1 < len(markers) else len(content)
        block = content[marker.end() : end]
        table = re.search(
            r"^\| Field \| Value \|\n\| --- \| --- \|\n((?:\|[^\n]*\|\n)+)", block, re.M
        )
        if table is None:
            raise ValueError(f"catalog entry {identifier} has no registration table")
        fields = {}
        for name, value in re.findall(r"^\| ([^|]+) \| (.*) \|$", table[1], re.M):
            if name in fields:
                raise ValueError(f"duplicate catalog field for {identifier}: {name}")
            fields[name] = value.replace(r"\|", "|")
        required = {
            "Title",
            "Start time (UTC) and parents",
            "Base revision",
            "Saved revision and branch",
            "Result and exit reason",
            "Findings (reported by the explorer)",
            "Limitations",
            "Checks (reported by the explorer)",
            "Human decision",
        }
        if not required <= fields.keys():
            missing = ", ".join(sorted(required - fields.keys()))
            raise ValueError(f"catalog entry {identifier} is missing fields: {missing}")
        saved = re.fullmatch(
            r"`(" + COMMIT + r")`、`([^`]+)`", fields["Saved revision and branch"]
        )
        base = re.fullmatch(r"`(" + COMMIT + r")`", fields["Base revision"])
        records = re.findall(
            r"^Record and reproduction entry point:\n\n```bash\ngit show ("
            + COMMIT
            + r"):(.+)\n```",
            block,
            re.M,
        )
        started, separator, parents = fields["Start time (UTC) and parents"].partition(
            "; "
        )
        status, result_separator, reason = fields["Result and exit reason"].partition(
            "; "
        )
        if (
            saved is None
            or base is None
            or len(records) != 1
            or records[0][0] != saved[1]
            or not separator
            or not result_separator
            or status not in OBSERVATION_STATUSES
            or not reason
        ):
            raise ValueError(f"catalog entry {identifier} has invalid fixed references")
        parent_ids = [] if parents == "none" else parents.split(", ")
        for parent in parent_ids:
            validate_id(parent)
        validate_path(records[0][1])
        entries.append(
            {
                "id": identifier,
                "title": fields["Title"],
                "started_at": started,
                "parent_ids": parent_ids,
                "base_commit": base[1],
                "saved_commit": saved[1],
                "branch": saved[2],
                "status": status,
                "exit_reason": reason,
                "summary": fields["Findings (reported by the explorer)"],
                "limits": fields["Limitations"],
                "checks": fields["Checks (reported by the explorer)"],
                "record": records[0][1],
                "catalog_commit": revision,
                "original_human_decision": fields["Human decision"],
            }
        )
    return entries


def read_blob(repo, revision, path):
    validate_path(path)
    require_regular_file(repo, path, revision)
    return subprocess.check_output(
        ["git", "-C", str(repo), "show", revision + ":" + path],
        stderr=subprocess.PIPE,
        timeout=30,
    )


def read_experiment(repo, identifier):
    validate_id(identifier)
    entry = next((e for e in catalog_entries(repo) if e["id"] == identifier), None)
    if entry is None:
        raise ValueError(
            f"experiment is not registered in the committed catalog: {identifier}"
        )
    try:
        saved = resolve_base(repo, entry["saved_commit"])
    except ValueError as error:
        raise ValueError(
            f"saved revision {entry['saved_commit']} is unavailable; fetch the recorded exploration's Git objects ({entry['branch']}) before reading this result"
        ) from error
    record = read_blob(repo, saved, entry["record"]).decode("utf-8")
    header = record_frontmatter(record)
    for name, expected in {
        "id": identifier,
        "base_commit": entry["base_commit"],
        "status": entry["status"],
        "started_at": f"'{entry['started_at']}'",
        "mode": "inherited" if entry["parent_ids"] else "independent",
        "human_decision": "unreviewed",
    }.items():
        actual = record_field(header, name)
        if actual != expected:
            raise ValueError(
                f"saved observation record {name} does not match: expected {expected!r}; found {actual!r}"
            )
    if record_parents(header) != entry["parent_ids"]:
        raise ValueError("saved observation record parents do not match the catalog")
    implementation = record_field(header, "implementation_commit")
    if implementation == "null":
        implementation = None
    if implementation is not None:
        if not re.fullmatch(COMMIT, implementation):
            raise ValueError("the saved implementation requires a full commit ID")
        for before, after in (
            (entry["base_commit"], implementation),
            (implementation, saved),
        ):
            git(repo, "merge-base", "--is-ancestor", before, after)
    elif entry["status"] in ("observed", "refuted"):
        raise ValueError("an observed result requires a fixed implementation commit")
    files = (
        subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "diff",
                "--name-only",
                "-z",
                entry["base_commit"],
                saved,
            ],
            stderr=subprocess.PIPE,
            timeout=30,
        )
        .decode("utf-8")
        .split("\0")
    )
    return {
        **entry,
        "implementation_commit": implementation,
        "record_text": record,
        "changed_files": [path for path in files if path],
        "diff_stat": git(repo, "diff", "--stat", entry["base_commit"], saved),
    }
