"""Read committed catalog registrations and their immutable observation records."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime

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
MARKER = re.compile(r"^<!-- forest-(run|experiment):([^\n]*) -->$", re.M)
RUN_FIELDS = {
    "Title": "表示名",
    "Start time (UTC) and parents": "開始UTCと親",
    "Base revision": "開始版",
    "Saved revision and branch": "保存版と枝",
    "Result and exit reason": "結果と終了理由",
    "Findings (reported by the explorer)": "発見（探索側の報告）",
    "Limitations": "限界",
    "Checks (reported by the explorer)": "検査（探索側の報告）",
    "Human decision": "採用判断",
}


def catalog_entries(repo):
    revision = git(repo, "rev-parse", "HEAD")
    project = project_at(repo, revision)
    content = git(repo, "show", revision + ":" + project["catalog"])
    return parse_catalog(content, revision)


def match_field(value, pattern, identifier, name):
    match = re.fullmatch(pattern, value)
    if match is None:
        raise ValueError(f"catalog entry {identifier} has invalid {name}")
    return match


def require_fields(fields, names, identifier):
    missing = set(names) - fields.keys()
    empty = {name for name in names if name in fields and not fields[name].strip()}
    if missing or empty:
        raise ValueError(
            f"catalog entry {identifier} is missing nonempty fields: "
            + ", ".join(sorted(missing | empty))
        )


def one_field(fields, names, identifier):
    present = [name for name in names if name in fields]
    if len(present) != 1:
        raise ValueError(
            f"catalog entry {identifier} requires exactly one of: {', '.join(names)}"
        )
    require_fields(fields, present, identifier)
    return fields[present[0]]


def run_entry(fields, language, identifier):
    names = (
        RUN_FIELDS
        if language == "Field | Value"
        else {name: name for name in RUN_FIELDS.values()}
    )
    require_fields(fields, names, identifier)
    # Normalize only the two controller registration layouts that are in use.
    values = (
        fields
        if language == "Field | Value"
        else {name: fields[translated] for name, translated in RUN_FIELDS.items()}
    )
    delimiter, no_parent = (
        ("; ", "none") if language == "Field | Value" else ("、", "なし")
    )
    started, separator, parents = values["Start time (UTC) and parents"].partition(
        delimiter
    )
    status, result_separator, reason = values["Result and exit reason"].partition(
        delimiter
    )
    if not separator or not result_separator or not reason.strip():
        raise ValueError(
            f"catalog entry {identifier} has invalid start or result metadata"
        )
    saved = match_field(
        values["Saved revision and branch"],
        r"`(" + COMMIT + r")`、`([^`]+)`",
        identifier,
        "saved revision and branch",
    )
    base = match_field(
        values["Base revision"], r"`(" + COMMIT + r")`", identifier, "base revision"
    )
    return {
        "title": values["Title"],
        "started_at": started,
        "parent_ids": [] if parents == no_parent else parents.split(", "),
        "base_commit": base[1],
        "saved_commit": saved[1],
        "branch": saved[2],
        "status": status,
        "exit_reason": reason,
        "summary": values["Findings (reported by the explorer)"],
        "limits": values["Limitations"],
        "checks": values["Checks (reported by the explorer)"],
        "checks_actor": "explorer",
        "original_human_decision": values["Human decision"],
    }


def app_entry(fields, heading, introduction, identifier):
    """Read the three App registration layouts preserved in naedo-forest."""
    require_fields(fields, ["限界"], identifier)
    layouts = [
        name
        for name in ("実験IDと形式", "ID、開始UTC、親", "ID、開始UTC")
        if name in fields
    ]
    if len(layouts) != 1:
        raise ValueError(
            f"catalog entry {identifier} has an unsupported App identity layout"
        )
    layout = layouts[0]
    if layout == "実験IDと形式":
        require_fields(
            fields,
            [
                layout,
                "開始UTC",
                "親ID",
                "開始版",
                "観測した実装版",
                "保存版",
                "保持枝",
                "結果と終了理由",
                "発見",
                "起動側の確認",
                "採用判断",
            ],
            identifier,
        )
        identity = match_field(
            fields[layout], r"`([^`]+)`、(独立|継承)", identifier, layout
        )
        parent = match_field(fields["親ID"], r"`([^`]+)`", identifier, "parent ID")[1]
        parents = [parent]
        if identity[2] != "継承":
            raise ValueError(
                f"catalog entry {identifier} has inconsistent App inheritance metadata"
            )
        started = match_field(
            fields["開始UTC"], r"`([^`]+)`", identifier, "start time"
        )[1]
        base = fields["開始版"]
        implementation = fields["観測した実装版"]
        saved = match_field(
            fields["保存版"], r"`(" + COMMIT + r")`", identifier, "saved revision"
        )[1]
        branch = match_field(fields["保持枝"], r"`([^`]+)`", identifier, "branch")[1]
        status, separator, reason = fields["結果と終了理由"].partition("、")
        if not separator or not reason.strip():
            raise ValueError(f"catalog entry {identifier} has invalid result metadata")
        summary = fields["発見"]
        checks = "起動側の確認: " + fields["起動側の確認"]
        adoption = fields["採用判断"]
    else:
        require_fields(
            fields,
            [
                layout,
                "観測実装",
                "保存版と保持枝",
                "Appチャット",
                "観測",
                "起動側の再現",
                "起動側の保存確認",
            ],
            identifier,
        )
        suffix = "、親なし" if layout == "ID、開始UTC、親" else ""
        identity = match_field(
            fields[layout], r"`([^`]+)`、([^、]+)" + suffix, identifier, layout
        )
        started = identity[2]
        if layout == "ID、開始UTC、親":
            parents = []
            base = one_field(fields, ("開始版", "共通開始版"), identifier)
        else:
            require_fields(fields, ["親IDと開始版"], identifier)
            parent = match_field(
                fields["親IDと開始版"],
                r"`([^`]+)`、親保存版 (`" + COMMIT + r"`)",
                identifier,
                "parent and base",
            )
            parents, base = [parent[1]], parent[2]
        implementation = fields["観測実装"]
        saved_branch = match_field(
            fields["保存版と保持枝"],
            r"`(" + COMMIT + r")`、`([^`]+)`",
            identifier,
            "saved revision and branch",
        )
        saved, branch = saved_branch[1], saved_branch[2]
        statuses = re.findall(r"結果は\s*`([^`]+)`", introduction)
        decisions = re.findall(r"^.*採用判断は\s*`[^`]+`.*$", introduction, re.M)
        if len(statuses) != 1 or len(decisions) != 1:
            raise ValueError(
                f"catalog entry {identifier} requires an explicit App result and adoption statement"
            )
        status, reason, adoption = statuses[0], fields["Appチャット"], decisions[0]
        summary = fields["観測"]
        checks = "\n".join(
            name + ": " + fields[name] for name in ("起動側の再現", "起動側の保存確認")
        )
    if identity[1] != identifier:
        raise ValueError(
            f"catalog entry {identifier} differs from its App identity field"
        )
    return {
        "title": heading,
        "started_at": started,
        "parent_ids": parents,
        "base_commit": match_field(
            base, r"`(" + COMMIT + r")`", identifier, "base revision"
        )[1],
        "implementation_commit": match_field(
            implementation,
            r"`(" + COMMIT + r")`",
            identifier,
            "implementation revision",
        )[1],
        "saved_commit": saved,
        "branch": branch,
        "status": status,
        "exit_reason": reason,
        "summary": summary,
        "limits": fields["限界"],
        "checks": checks,
        "checks_actor": "controller",
        "original_human_decision": adoption,
    }


def parse_catalog(content, revision):
    """Parse explicit registrations; historical unmarked references remain visible."""
    markers = list(MARKER.finditer(content))
    if len(markers) != len(
        re.findall(r"^[ \t]*<!-- forest-(?:run|experiment):", content, re.M)
    ):
        raise ValueError(
            "the catalog contains a malformed experiment registration marker"
        )
    prefix = content[: markers[0].start()] if markers else content
    unmarked = bool(re.search(r"^git show " + COMMIT + r":.+README\.md$", prefix, re.M))
    if unmarked and not markers:
        raise ValueError(
            "the catalog contains saved-record references without supported registration markers; inspect the historical catalog directly"
        )
    entries = []
    seen = set()
    for i, marker in enumerate(markers):
        identifier = validate_id(marker[2])
        if identifier in seen:
            raise ValueError(f"duplicate experiment ID in the catalog: {identifier}")
        seen.add(identifier)
        end = markers[i + 1].start() if i + 1 < len(markers) else len(content)
        block = content[marker.end() : end]
        heading = re.search(r"^## (.+)$", block, re.M)
        if heading is None or not heading[1].strip():
            raise ValueError(f"catalog entry {identifier} has no registration heading")
        next_heading = re.search(r"^## ", block[heading.end() :], re.M)
        if next_heading:
            block = block[: heading.end() + next_heading.start()]
        table = re.search(
            r"^\| (Field \| Value|項目 \| 内容) \|\n\| --- \| --- \|\n((?:\|[^\n]*\|\n)+)",
            block,
            re.M,
        )
        if table is None:
            raise ValueError(f"catalog entry {identifier} has no registration table")
        fields = {}
        for name, value in re.findall(r"^\| ([^|]+) \| (.*) \|$", table[2], re.M):
            if name in fields:
                raise ValueError(f"duplicate catalog field for {identifier}: {name}")
            fields[name] = value.replace(r"\|", "|")
        if marker[1] == "run":
            entry = run_entry(fields, table[1], identifier)
        elif table[1] == "項目 | 内容":
            entry = app_entry(
                fields, heading[1], block[heading.end() : table.start()], identifier
            )
        else:
            raise ValueError(
                f"catalog entry {identifier} has an unsupported App table layout"
            )
        records = re.findall(
            r"^```bash\ngit show (" + COMMIT + r"):(.+)\n```[ \t]*$",
            block,
            re.M,
        )
        if (
            len(records) != 1
            or records[0][0] != entry["saved_commit"]
            or entry["status"] not in OBSERVATION_STATUSES
        ):
            raise ValueError(f"catalog entry {identifier} has invalid fixed references")
        try:
            datetime.strptime(entry["started_at"], "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise ValueError(
                f"catalog entry {identifier} has an invalid UTC start time"
            ) from error
        for parent in entry["parent_ids"]:
            validate_id(parent)
        validate_path(records[0][1])
        entries.append(
            {
                "id": identifier,
                **entry,
                "record": records[0][1],
                "catalog_commit": revision,
                "catalog_has_unmarked_references": unmarked,
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
    if (
        "implementation_commit" in entry
        and entry["implementation_commit"] != implementation
    ):
        raise ValueError(
            "saved observation implementation does not match the App catalog"
        )
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
