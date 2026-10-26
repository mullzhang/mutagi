"""Browse and selectively reuse fixed results without executing an agent."""

from enum import Enum
from pathlib import Path
import re
import subprocess
from typing import Annotated

import typer

from mutagi.commands import display
from mutagi.commands.common import (
    Details,
    Json,
    command,
    configure,
    group,
    invocation,
    repository,
)
from mutagi.commands.inputs import read_text
from mutagi.execution import harvest
from mutagi.execution.catalog import catalog_entries, read_experiment
from mutagi.execution.harvest_records import (
    DECISIONS,
    KINDS,
    decisions_for,
    read_records,
)

app = group(
    "Find relevant results, inspect limits, prepare trials or prompts, and record scoped adoption decisions."
)
app.callback()(configure)
Kind = Enum("Kind", {name: name for name in KINDS}, type=str)
Decision = Enum("Decision", {name: name for name in DECISIONS}, type=str)


def checked_reference(value):
    if not re.fullmatch(r"[0-9a-f][0-9a-f-]{7,35}", value):
        raise typer.BadParameter(
            "Use a full UUID or a lowercase hexadecimal prefix of at least eight characters."
        )
    return value


Reference = Annotated[
    str,
    typer.Argument(
        callback=checked_reference,
        help="Experiment UUID or unique prefix of at least eight characters.",
    ),
]
Parts = Annotated[
    list[str],
    typer.Option("--part", help="Saved file or FILE#Exact heading; repeatable."),
]
Target = Annotated[
    Path | None,
    typer.Option(
        "--target",
        exists=True,
        file_okay=False,
        help="Existing destination directory; never modified here.",
    ),
]
KindOption = Annotated[Kind, typer.Option("--kind", help="What to reuse.")]


def report_catalog_scope(source):
    if source["catalog_has_unmarked_references"]:
        display.note(
            "Older unmarked saved-record references are not indexed. Inspect the original catalog for those records.",
            error=True,
        )


def resolve_reference(repo, reference):
    matches = [
        entry["id"]
        for entry in catalog_entries(repo)
        if entry["id"].startswith(reference)
    ]
    if len(matches) != 1:
        raise typer.BadParameter(
            "Reference is ambiguous; use a longer prefix."
            if matches
            else "No registered result matches this reference.",
            param_hint="REFERENCE",
        )
    source = read_experiment(repo, matches[0])
    report_catalog_scope(source)
    return source


def references(entries):
    result = {}
    for entry in entries:
        size = 8
        while any(
            other["id"] != entry["id"] and other["id"].startswith(entry["id"][:size])
            for other in entries
        ):
            size += 1
        result[entry["id"]] = entry["id"][:size]
    return result


def show_note(record):
    display.fields(
        [
            ("Decision", record["decision"] + " (" + record["kind"] + ")"),
            (
                "Selected parts",
                ", ".join(record["parts"])
                if record["parts"]
                else "Not separately specified; see note",
            ),
            (
                "Purpose",
                record["purpose"] if record["purpose"] is not None else "Not recorded",
            ),
            (
                "Destination",
                record["target"] if record["target"] is not None else "Not recorded",
            ),
            (
                "Verification reported",
                record["verification"]
                if record["verification"] is not None
                else "Not recorded",
            ),
            ("Note", record["note"]),
        ]
    )


@command(app, "list")
def list_results(
    ctx: typer.Context,
    search: Annotated[
        str | None,
        typer.Option(
            "--search",
            help="Search titles, findings, and limits without ranking validity.",
        ),
    ] = None,
    json: Json = False,
    details: Details = False,
):
    """Browse compact result references and questions, including negative results."""
    repo = repository(ctx)
    entries = catalog_entries(repo)
    if entries:
        report_catalog_scope(entries[0])
    all_refs = references(entries)
    notes = read_records(repo)
    if search is not None:
        needle = harvest.nonempty(search, "search text").casefold()
        entries = [
            entry
            for entry in entries
            if needle
            in "\n".join(
                entry[key] for key in ("title", "summary", "limits")
            ).casefold()
        ]
    entries = [
        {**entry, "harvesting_records": decisions_for(notes, entry)}
        for entry in entries
    ]
    if json:
        display.payload(entries)
        return
    display.title(f"Saved results · {len(entries)} matches")
    if entries:
        display.table(
            ["Ref", "Question", "Observation"],
            [
                (all_refs[entry["id"]], entry["title"], entry["status"])
                for entry in entries
            ],
        )
    else:
        display.note(
            "No registered results match. Negative and unresolved observations are included in the search."
        )
    display.next_steps(invocation(repo, "harvest", "show", "REF"))
    display.note(
        "References identify saved experiments, not adoption decisions. More: --details or --json."
    )
    if details:
        for entry in entries:
            display.title(entry["title"])
            display.fields(
                [
                    ("Experiment", entry["id"]),
                    ("Findings", entry["summary"]),
                    ("Limits", entry["limits"]),
                    ("Harvesting notes", len(entry["harvesting_records"])),
                    ("Saved revision", entry["saved_commit"]),
                ]
            )


@command(app)
def show(
    ctx: typer.Context,
    reference: Reference,
    json: Json = False,
    details: Details = False,
    record: Annotated[
        bool,
        typer.Option(
            "--record",
            help="Print the exact saved observation record, without decoration.",
        ),
    ] = False,
    diff: Annotated[
        bool,
        typer.Option(
            "--diff",
            help="Print the full saved diff from the experiment base, without decoration.",
        ),
    ] = False,
):
    """Read findings and limits first; explicitly open original records or diffs."""
    if sum((json, record, diff)) > 1 or details and (json or record or diff):
        raise typer.BadParameter(
            "Choose one of --details, --json, --record, or --diff."
        )
    repo = repository(ctx)
    source = resolve_reference(repo, reference)
    if record:
        display.text_payload(source["record_text"])
        return
    if diff:
        content = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "diff",
                "--no-ext-diff",
                "--no-color",
                source["base_commit"],
                source["saved_commit"],
                "--",
            ],
            text=True,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        display.text_payload(content)
        return
    notes = decisions_for(read_records(repo), source)
    if json:
        display.payload({**source, "harvesting_records": notes})
        return
    display.title(source["title"])
    display.fields(
        [
            ("Reported observation", source["status"]),
            (
                "Findings" if details else "Findings excerpt",
                source["summary"]
                if details
                else display.excerpt(source["summary"], 240),
            ),
            (
                "Limits" if details else "Limits excerpt",
                source["limits"] if details else display.excerpt(source["limits"], 200),
            ),
            (
                "Candidate material",
                f"{len(source['changed_files'])} changed files; inspect before selecting",
            ),
            (
                "Catalog decision" if details else "Catalog decision excerpt",
                source["original_human_decision"]
                if details
                else display.excerpt(source["original_human_decision"], 140),
            ),
            ("Scoped harvesting notes", len(notes)),
        ]
    )
    display.next_steps(
        invocation(repo, "harvest", "checkout", reference),
        invocation(repo, "harvest", "prepare", reference)
        + " --purpose '<intended use>'",
    )
    display.note(
        "More: --details for metadata and checks; --record for the original record; --diff for changes; --json for data."
    )
    if details:
        display.title("Supporting evidence and references")
        display.fields(
            [
                ("Experiment UUID", source["id"]),
                ("Saved revision", source["saved_commit"]),
                (
                    "Observed implementation",
                    source["implementation_commit"]
                    if source["implementation_commit"] is not None
                    else "Not recorded",
                ),
                (
                    "Parents",
                    ", ".join(source["parent_ids"]) if source["parent_ids"] else "None",
                ),
                ("Reported exit reason", source["exit_reason"]),
                ("Checks reported by the " + source["checks_actor"], source["checks"]),
                ("Record path", source["record"]),
            ]
        )
        display.console().print(source["diff_stat"])
        for note in notes:
            show_note(note)


@command(app)
def checkout(ctx: typer.Context, reference: Reference, json: Json = False):
    """Prepare an exact detached trial; retain and report existing local changes."""
    repo = repository(ctx)
    result = harvest.checkout(repo, resolve_reference(repo, reference))
    if json:
        display.payload(result)
        return
    display.title("Trial prepared")
    display.fields(
        [
            ("Trial worktree", result["path"]),
            ("Local changes", "Yes; retained" if result["dirty"] else "None"),
            ("Reproduction record", result["record"]),
        ]
    )
    display.note("No dependencies or tests were run by checkout.")
    display.next_steps(
        "Read the reproduction steps, then run them in the trial worktree."
    )


@command(app)
def prepare(
    ctx: typer.Context,
    reference: Reference,
    purpose: Annotated[
        str, typer.Option("--purpose", help="Intended use in the destination.")
    ],
    kind: KindOption = Kind.code,
    part: Parts = [],
    target: Target = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Create a UTF-8 file instead of writing the prompt to stdout; never overwrite.",
        ),
    ] = None,
):
    """Print or save an unstyled destination prompt without starting an agent."""
    repo = repository(ctx)
    content = harvest.prepare(
        repo,
        resolve_reference(repo, reference),
        purpose=purpose,
        kind=kind.value,
        parts=part,
        target=target,
    )
    if output is None:
        display.text_payload(content)
    else:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(content)
        display.title("Prompt saved")
        display.fields([("File", output.resolve())])
        display.next_steps(
            "Give this file to the person or agent working in the destination."
        )


@command(app)
def record(
    ctx: typer.Context,
    reference: Reference,
    decision: Annotated[
        Decision,
        typer.Option("--decision", help="Supplied decision for the stated scope."),
    ],
    note_file: Annotated[
        str, typer.Option("--note-file", help="UTF-8 review note, or - for stdin.")
    ],
    kind: KindOption = Kind.code,
    part: Parts = [],
    target: Target = None,
    purpose: Annotated[
        str | None, typer.Option("--purpose", help="Intended use.")
    ] = None,
    target_commit: Annotated[
        str | None,
        typer.Option(
            "--target-commit",
            help="Destination revision associated with the change; requires --target.",
        ),
    ] = None,
    verification: Annotated[
        str | None,
        typer.Option(
            "--verification",
            help="Actual verification report; omitted means not recorded.",
        ),
    ] = None,
    json: Json = False,
):
    """Record a scoped decision on a separate branch without applying code."""
    repo = repository(ctx)
    result = harvest.record(
        repo,
        resolve_reference(repo, reference),
        kind=kind.value,
        parts=part,
        purpose=purpose,
        target=target,
        target_commit=target_commit,
        decision=decision.value,
        verification=verification,
        note=read_text(note_file),
    )
    if json:
        display.payload(result)
        return
    display.title("Scoped decision recorded · " + decision.value)
    display.fields(
        [
            ("Note UUID", result["record"]["id"]),
            ("Saved branch", result["branch"]),
            ("Saved commit", result["commit"]),
        ]
    )
    display.note(
        "This records the supplied decision and evidence. It does not apply code or establish verification."
    )
    display.next_steps(invocation(repo, "harvest", "show", reference))
