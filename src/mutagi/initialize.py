"""Generate reviewable exploration settings in an existing Git repository."""

import argparse
from importlib.resources import files
import json
from pathlib import Path
import shlex
import sys

from mutagi.project import parse_project, validate_path
from mutagi.repository import repository_root, require_regular_file
from mutagi.settings import EFFORTS

ASSETS = {
    ".mutagi/config.toml": "config.toml",
    ".mutagi/prompts/explore.md": "explore.md",
    ".mutagi/prompts/inherit.md": "inherit.md",
    ".mutagi/prompts/integrate.md": "integrate.md",
    "experiments/INDEX.md": "catalog.md",
    "knowledge/README.md": "knowledge.md",
}

PRESETS = {
    "python-stdlib": {
        "requires": (),
        "commands": [["python3", "-m", "unittest", "discover", "-s", "tests"]],
    },
    "python-uv": {
        "requires": ("pyproject.toml", "uv.lock"),
        "commands": [["uv", "sync", "--locked", "--dev"]],
    },
    "node-npm": {
        "requires": ("package.json", "package-lock.json"),
        "commands": [["npm", "ci"]],
    },
    "rust-cargo": {
        "requires": ("Cargo.toml", "Cargo.lock"),
        "commands": [["cargo", "fetch", "--locked"]],
    },
}


def initialize(
    repo,
    name,
    exploration_paths,
    references,
    *,
    dry_run=False,
    preset=None,
    execution=None,
):
    repo = repository_root(repo, exact=True)
    preparation = []
    if preset is not None:
        if preset not in PRESETS:
            raise ValueError(
                "choose a supported preparation preset: " + ", ".join(PRESETS)
            )
        for path in PRESETS[preset]["requires"]:
            require_regular_file(repo, path)
        preparation = PRESETS[preset]["commands"]
    for path in [*exploration_paths, *references]:
        validate_path(path)
    for path in references:
        require_regular_file(repo, path)
    resources = files("mutagi").joinpath("resources/project")
    content = {
        path: resources.joinpath(asset).read_text(encoding="utf-8")
        for path, asset in ASSETS.items()
    }
    content[".mutagi/config.toml"] = content[".mutagi/config.toml"].format(
        name=json.dumps(repo.name if name is None else name, ensure_ascii=False),
        exploration_paths=json.dumps(
            list(dict.fromkeys(["knowledge/notes/", *exploration_paths])),
            ensure_ascii=False,
        ),
        references=json.dumps(list(dict.fromkeys(references)), ensure_ascii=False),
        preparation=json.dumps(preparation),
    )
    if execution:
        content[".mutagi/config.toml"] += "\n[execution]\n" + "".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
            for key, value in execution.items()
        )
    project = parse_project(content[".mutagi/config.toml"])
    content["knowledge/notes/.gitkeep"] = ""

    conflicts = []
    for name in [*content, ".gitignore"]:
        path = repo / name
        for parent in [path, *path.parents]:
            if parent == repo:
                break
            if parent.is_symlink() or (
                parent != path and parent.exists() and not parent.is_dir()
            ):
                conflicts.append(
                    f"{parent.relative_to(repo)}: remove the symlink or choose a directory"
                )
        if name == ".gitignore":
            if path.exists() and not path.is_file():
                conflicts.append(".gitignore: expected a regular file")
        elif path.exists():
            conflicts.append(f"{name}: already exists")
    if conflicts:
        raise ValueError(
            "Initialization would conflict with existing files:\n"
            + "\n".join(dict.fromkeys(conflicts))
            + "\nNo files were changed. Use mutagi check for an initialized project."
        )

    ignore = repo / ".gitignore"
    previous = ignore.read_bytes().decode("utf-8") if ignore.exists() else ""
    additions = [
        "/" + path + "/"
        for path in (".mutagi/runs", ".mutagi/worktrees")
        if not {path + "/", "/" + path + "/"}.intersection(previous.splitlines())
    ]
    suffix = ""
    if additions:
        suffix = ("\n" if previous and not previous.endswith("\n") else "") + "".join(
            line + "\n" for line in additions
        )
    if dry_run:
        for path, text in content.items():
            print(f"--- {path} ---\n{text}", end="" if text.endswith("\n") else "\n")
        print("--- .gitignore additions ---\n" + suffix, end="")
        return project

    for name, text in content.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
        print("Created " + name)
    if suffix:
        with ignore.open("a", encoding="utf-8", newline="") as stream:
            stream.write(suffix)
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
    return project


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
            initialize(**options, dry_run=True)
            if args.dry_run:
                return
            if input("Write these project files? [y/N]: ").strip().lower() not in (
                "y",
                "yes",
            ):
                print("Initialization cancelled; no files were changed.")
                return
        initialize(**options)
    except (EOFError, KeyboardInterrupt):
        print("\nInitialization cancelled; input was interrupted.", file=sys.stderr)
        raise SystemExit(1) from None
