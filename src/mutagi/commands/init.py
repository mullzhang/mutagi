"""Generate reviewable exploration settings in an existing Git repository."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

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


def main():
    parser = argparse.ArgumentParser(prog="mutagi init", description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Git repository root (default: current directory)",
    )
    parser.add_argument(
        "--name", help="Project name (default: repository directory name)"
    )
    parser.add_argument(
        "--explore-path",
        action="append",
        help="Additional application path the explorer may change; repeatable",
    )
    parser.add_argument(
        "--reference",
        action="append",
        help="Existing project reference file; repeatable",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated contents without writing files",
    )
    parser.add_argument(
        "--preset", choices=PRESETS, help="Save an explicit preparation command set"
    )
    parser.add_argument("--model", help="Save this explicitly selected default model")
    parser.add_argument(
        "--effort", choices=EFFORTS, help="Save this default reasoning effort"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Ask for omitted settings, preview files, and confirm before writing",
    )
    args = parser.parse_args()
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
            show_initialization(initialize(**options, dry_run=True), dry_run=True)
            if args.dry_run:
                return
            if input("Write these project files? [y/N]: ").strip().lower() not in (
                "y",
                "yes",
            ):
                print("Initialization cancelled; no files were changed.")
                return
        show_initialization(initialize(**options))
    except (EOFError, KeyboardInterrupt):
        print("\nInitialization cancelled; input was interrupted.", file=sys.stderr)
        raise SystemExit(1) from None


def show_initialization(result, *, dry_run=False):
    content = result["files"]
    suffix = result["ignore_additions"]
    project = result["project"]
    if dry_run:
        for path, text in content.items():
            print(f"--- {path} ---\n{text}", end="" if text.endswith("\n") else "\n")
        print("--- .gitignore additions ---\n" + suffix, end="")
        return
    for name in content:
        print("Created " + name)
    if suffix:
        print("Updated .gitignore")
    print(
        "Exploration paths: "
        + ", ".join(project["exploration_paths"])
        + "; each experiment may also write its own directory."
    )
    print("Review the generated settings and project prompts, then run:")
    print("  git add -- " + shlex.join([*content, ".gitignore"]))
    print('  git commit -m "chore: configure mutagi exploration"')
    print("  mutagi check")
    print(
        "Choose an available model and use mutagi experiment start --help to start an exploration."
    )
