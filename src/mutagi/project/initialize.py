"""Generate reviewable exploration settings in an existing Git repository."""

from __future__ import annotations

import json
from importlib.resources import files

from mutagi.project.config import parse_project, validate_path
from mutagi.project.repository import repository_root, require_regular_file

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
    if not dry_run:
        for name, text in content.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                stream.write(text)
        if suffix:
            with ignore.open("a", encoding="utf-8", newline="") as stream:
                stream.write(suffix)
    return {"project": project, "files": content, "ignore_additions": suffix}
