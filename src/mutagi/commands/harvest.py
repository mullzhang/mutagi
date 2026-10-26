"""Find results, prepare trials and prompts, and record selective harvesting."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from mutagi.commands.inputs import read_text
from mutagi.execution import harvest
from mutagi.execution.catalog import catalog_entries, read_experiment
from mutagi.execution.harvest_records import (
    DECISIONS,
    KINDS,
    decisions_for,
    read_records,
)
from mutagi.execution.state import validate_id
from mutagi.project.repository import repository_root


def selection_arguments(parser):
    parser.add_argument("--kind", choices=KINDS, default="code")
    parser.add_argument(
        "--part",
        action="append",
        default=[],
        help="Saved file or FILE#Exact heading; repeat to select several parts",
    )
    parser.add_argument(
        "--target",
        type=Path,
        help="Existing destination directory; never changed by this command",
    )


def next_command(repo, action, identifier):
    return shlex.join(["mutagi", "harvest", "--repo", str(repo), action, identifier])


def report_catalog_scope(source):
    if source["catalog_has_unmarked_references"]:
        print(
            "Note: the catalog also contains unmarked historical saved-record references. Harvest indexes marked forest-run and forest-experiment registrations only; inspect the original catalog for those older records.",
            file=sys.stderr,
        )


def show_note(record):
    print(
        f"\n{record['recorded_at']}  {record['decision']} ({record['kind']})  {record['id']}"
    )
    print(
        "Selected parts: "
        + (
            ", ".join(record["parts"])
            if record["parts"]
            else "not separately specified; see note"
        )
    )
    for label, key in (
        ("Purpose", "purpose"),
        ("Destination", "target"),
        ("Associated destination revision", "target_commit"),
        ("Verification reported", "verification"),
    ):
        print(
            label + ": " + (record[key] if record[key] is not None else "not recorded")
        )
    print("Decision note:\n" + record["note"])


def main():
    parser = argparse.ArgumentParser(prog="mutagi harvest", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="Search the committed catalog")
    listing.add_argument(
        "--search",
        help="Case-insensitive text search across titles, findings, and limits",
    )
    listing.add_argument("--json", action="store_true")
    show = actions.add_parser(
        "show", help="Read a fixed result and its harvesting notes"
    )
    show.add_argument("id", type=validate_id)
    show.add_argument("--json", action="store_true")
    trial = actions.add_parser(
        "checkout", help="Create or inspect a detached trial worktree"
    )
    trial.add_argument("id", type=validate_id)
    trial.add_argument("--json", action="store_true")
    prompt = actions.add_parser(
        "prepare", help="Print a harvesting prompt without starting an agent"
    )
    prompt.add_argument("id", type=validate_id)
    prompt.add_argument("--purpose", required=True)
    selection_arguments(prompt)
    prompt.add_argument(
        "--output",
        type=Path,
        help="Create a UTF-8 prompt file instead of printing it; never overwrite a file",
    )
    note = actions.add_parser(
        "record", help="Append a decision note to the mutagi/harvests branch"
    )
    note.add_argument("id", type=validate_id, help="Source experiment UUID")
    note.add_argument("--decision", choices=DECISIONS, required=True)
    note.add_argument(
        "--note-file",
        required=True,
        help="UTF-8 decision note, or - for standard input",
    )
    selection_arguments(note)
    note.add_argument("--purpose")
    note.add_argument(
        "--target-commit",
        help="Destination revision associated with the change; requires --target",
    )
    note.add_argument(
        "--verification", help="Actual verification report; omitted means not recorded"
    )
    note.add_argument("--json", action="store_true")
    args = parser.parse_args()
    repo = repository_root(args.repo)

    if args.action == "list":
        entries = catalog_entries(repo)
        if entries:
            report_catalog_scope(entries[0])
        records = read_records(repo)
        if args.search is not None:
            search = harvest.nonempty(args.search, "search text").casefold()
            entries = [
                entry
                for entry in entries
                if search
                in "\n".join(
                    entry[key] for key in ("title", "summary", "limits")
                ).casefold()
            ]
        entries = [
            {**entry, "harvesting_records": decisions_for(records, entry)}
            for entry in entries
        ]
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
            return
        if not entries:
            print(
                "No registered results match. Search includes negative and unresolved results."
            )
        for entry in entries:
            print(f"{entry['id']}  {entry['title']}")
            print(
                f"Reported observation: {entry['status']}; exit: {entry['exit_reason']}"
            )
            print("Findings: " + entry["summary"])
            print("Limitations: " + entry["limits"])
            print(
                f"Harvesting notes: {len(entry['harvesting_records'])} (specific parts and destinations)"
            )
            print("Next: " + next_command(repo, "show", entry["id"]) + "\n")
        return

    source = read_experiment(repo, args.id)
    report_catalog_scope(source)
    if args.action == "show":
        records = decisions_for(read_records(repo), source)
        if args.json:
            print(
                json.dumps(
                    {**source, "harvesting_records": records},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        print(source["title"] + "\nExperiment: " + source["id"])
        print(
            f"Reported observation: {source['status']}; exit: {source['exit_reason']}"
        )
        print("Findings: " + source["summary"] + "\nLimitations: " + source["limits"])
        print(f"Checks reported by the {source['checks_actor']}: " + source["checks"])
        print("Saved revision: " + source["saved_commit"])
        print(
            "Observed implementation: "
            + (source["implementation_commit"] or "not recorded")
        )
        print("Parents: " + (", ".join(source["parent_ids"]) or "none"))
        print("Original catalog decision text: " + source["original_human_decision"])
        print("Changes from base:\n" + source["diff_stat"])
        print(
            "\nHarvesting notes (decisions apply only to their stated parts and destinations):"
        )
        for record in records:
            show_note(record)
        if not records:
            print(
                "No separate harvesting notes. Check the catalog and observation record for free-form decisions."
            )
        print("\nSaved observation record:\n" + source["record_text"])
        print("Trial: " + next_command(repo, "checkout", args.id))
        print(
            "Prompt: "
            + next_command(repo, "prepare", args.id)
            + " --purpose '<intended use>'"
        )
    elif args.action == "checkout":
        result = harvest.checkout(repo, source)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Trial worktree: " + result["path"])
            print("Saved revision: " + result["saved_commit"])
            print("Observation record and reproduction steps: " + result["record"])
            print(
                "Trial has local changes: "
                + ("yes; retained" if result["dirty"] else "no")
            )
            print(
                "Read and run the recorded reproduction steps in the trial. No dependencies or tests were run by checkout."
            )
    elif args.action == "prepare":
        content = harvest.prepare(
            repo,
            source,
            purpose=args.purpose,
            kind=args.kind,
            parts=args.part,
            target=args.target,
        )
        if args.output is None:
            print(content, end="")
        else:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(content)
            print("Prompt file: " + str(args.output.resolve()))
    else:
        result = harvest.record(
            repo,
            source,
            kind=args.kind,
            parts=args.part,
            purpose=args.purpose,
            target=args.target,
            target_commit=args.target_commit,
            decision=args.decision,
            verification=args.verification,
            note=read_text(args.note_file),
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Harvesting note: " + result["record"]["id"])
            print("Reported decision: " + result["record"]["decision"])
            print(f"Saved on {result['branch']}: {result['commit']}:{result['path']}")
            print(
                "The note records the supplied decision and evidence; it does not apply code or infer verification."
            )
