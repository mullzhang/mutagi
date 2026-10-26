"""Generate reviewable exploration settings in an existing Git repository."""

import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from enum import Enum
from typing import Annotated

import typer

from mutagi.commands import display
from mutagi.commands.common import Effort, Json, Repo

from mutagi.project.config import validate_path
from mutagi.project.initialize import PRESETS, initialize
from mutagi.project.repository import repository_root
from mutagi.project.settings import EFFORTS


def ask(label, *, default=None, choices=None):
    suffix = f" [{default}]" if default is not None else " (blank to omit)"
    value = input(label + suffix + ": ").strip()
    value = default if not value else value
    if choices is not None and value is not None and value not in choices:
        raise ValueError(f"{label}: choose one of {', '.join(choices)}")
    return value


def ask_paths(label):
    print(label + "; enter one path per line, then a blank line to finish.")
    result = []
    while value := input("Path: ").strip():
        validate_path(value)
        result.append(value)
    return result


def interactive_options(args):
    if not sys.stdin.isatty():
        raise ValueError(
            "--interactive requires a terminal; supply initialization options for scripts"
        )
    args.repo = repository_root(args.repo, exact=True)
    if args.name is None:
        args.name = ask("Project name", default=args.repo.name)
    if args.preset is None:
        args.preset = ask(
            "Preparation preset (" + ", ".join(PRESETS) + ")", choices=PRESETS
        )
    if args.explore_path is None:
        args.explore_path = ask_paths("Application paths the explorer may change")
    if args.reference is None:
        args.reference = ask_paths("Existing reference files")
    if args.model is None:
        args.model = ask("Default model (choose a model available to your account)")
    if args.effort is None:
        args.effort = ask(
            "Default reasoning effort (" + ", ".join(EFFORTS) + ")", choices=EFFORTS
        )


Preset = Enum("Preset", {name.replace("-", "_"): name for name in PRESETS}, type=str)


def initialize_project(
    repo: Repo = Path("."),
    name: Annotated[
        str | None, typer.Option("--name", help="Project display name.")
    ] = None,
    explore_path: Annotated[
        list[str] | None,
        typer.Option(
            "--explore-path", help="Additional writable application path; repeatable."
        ),
    ] = None,
    reference: Annotated[
        list[str] | None,
        typer.Option("--reference", help="Existing reference file; repeatable."),
    ] = None,
    preset: Annotated[
        Preset | None,
        typer.Option("--preset", help="Explicit preparation command set."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Save this explicitly selected default model."),
    ] = None,
    effort: Annotated[
        Effort | None,
        typer.Option("--effort", help="Save this default reasoning effort."),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            help="Choose omitted settings, preview, and confirm writing.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview generated contents without writing."),
    ] = False,
    json: Json = False,
):
    args = SimpleNamespace(
        repo=repo,
        name=name,
        explore_path=explore_path,
        reference=reference,
        preset=preset.value if preset is not None else None,
        model=model,
        effort=effort.value if effort is not None else None,
        interactive=interactive,
        dry_run=dry_run,
    )
    if interactive and json:
        raise typer.BadParameter(
            "Use explicit options with --json; --interactive requires human output."
        )
    try:
        if args.interactive:
            interactive_options(args)
        options = {
            "repo": args.repo,
            "name": args.name,
            "exploration_paths": args.explore_path or [],
            "references": args.reference or [],
            "preset": args.preset,
            "execution": {
                key: value
                for key in ("model", "effort")
                if (value := getattr(args, key)) is not None
            },
        }
        if args.interactive or args.dry_run:
            preview = initialize(**options, dry_run=True)
            if json and args.dry_run:
                display.payload(preview)
                return
            show_initialization(preview, repo=args.repo.resolve(), dry_run=True)
            if args.dry_run:
                return
            if input("Write these project files? [y/N]: ").strip().lower() not in (
                "y",
                "yes",
            ):
                print("Initialization cancelled; no files were changed.")
                return
        result = initialize(**options)
        if json:
            display.payload(result)
        else:
            show_initialization(result, repo=args.repo.resolve())
    except (EOFError, KeyboardInterrupt):
        print("\nInitialization cancelled; input was interrupted.", file=sys.stderr)
        raise SystemExit(1) from None


def show_initialization(result, *, repo, dry_run=False):
    content = result["files"]
    suffix = result["ignore_additions"]
    project = result["project"]
    if dry_run:
        for path, text in content.items():
            print(f"--- {path} ---\n{text}", end="" if text.endswith("\n") else "\n")
        print("--- .gitignore additions ---\n" + suffix, end="")
        return
    display.title("Project configured")
    display.fields(
        [
            ("Created files", len(content)),
            ("Project", project["name"]),
            ("Writable scope", ", ".join(project["exploration_paths"])),
        ]
    )
    display.table(["File", "Result"], [(name, "created") for name in content])
    if suffix:
        print("Updated .gitignore")
    print(
        "Exploration paths: "
        + ", ".join(project["exploration_paths"])
        + "; each experiment may also write its own directory."
    )
    display.note("Review generated settings and project prompts before starting work.")
    display.next_steps(
        *(["cd " + shlex.quote(str(repo))] if Path.cwd().resolve() != repo else []),
        "git add -- " + shlex.join([*content, ".gitignore"]),
        'git commit -m "chore: configure mutagi exploration"',
        "mutagi check",
    )
    print(
        "Choose an available model and use mutagi experiment start --help to start an exploration."
    )
