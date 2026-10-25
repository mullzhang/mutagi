"""Read the exploration settings explicitly provided by a project."""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath

from mutagi.project.settings import validate_defaults


def validate_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a nonempty project-relative path is required")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] == ".git"
        or any(ord(character) < 32 for character in value)
        or "\\" in value
    ):
        raise ValueError(f"a project-relative path is required: {value!r}")
    return value


def parse_project(text: str, source: str = ".mutagi/config.toml") -> dict:
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{source}: fix the TOML syntax: {error}") from error

    fields = {
        "name": str,
        "preparation": list,
        "references": list,
        "experiments": str,
        "catalog": str,
        "exploration_paths": list,
        "integration_paths": list,
        "prompts": dict,
        "generated_hashes": dict,
    }
    errors = [
        f"{key}: unknown field; remove it"
        for key in sorted(config.keys() - fields.keys() - {"execution"})
    ]
    for key, kind in fields.items():
        if key not in config:
            errors.append(f"{key}: missing required field; add a {kind.__name__} value")
        elif not isinstance(config[key], kind):
            errors.append(f"{key}: expected {kind.__name__}; correct its type")
    if errors:
        raise ValueError(f"{source}:\n" + "\n".join(errors))

    if not config["name"].strip():
        errors.append("name: supply a nonempty project name")
    if "execution" in config:
        try:
            validate_defaults(config["execution"])
        except ValueError as error:
            errors.append(str(error))
    if set(config["prompts"]) != {"explore", "inherit", "integrate"}:
        errors.append("prompts: specify exactly explore, inherit, and integrate paths")

    paths = [(key, config[key]) for key in ("experiments", "catalog")]
    for key in ("references", "exploration_paths", "integration_paths"):
        paths.extend(
            (f"{key}[{index}]", value) for index, value in enumerate(config[key])
        )
    paths.extend((f"prompts.{key}", value) for key, value in config["prompts"].items())
    for path, keys in config["generated_hashes"].items():
        paths.append(("generated_hashes", path))
        if not isinstance(keys, list) or not all(
            isinstance(key, str) and key.strip() for key in keys
        ):
            errors.append(
                f"generated_hashes.{path}: supply an array of nonempty hash keys"
            )
    for field, value in paths:
        try:
            validate_path(value)
            if field.startswith(("exploration_paths[", "integration_paths[")):
                expanded = value.replace("{id}", "00000000-0000-4000-8000-000000000000")
                if "{" in expanded or "}" in expanded:
                    raise ValueError("only {id} is supported in path patterns")
        except ValueError as error:
            errors.append(f"{field}: {error}; correct the path")

    for index, command in enumerate(config["preparation"]):
        if (
            not isinstance(command, list)
            or not command
            or not all(
                isinstance(part, str) and part and "\0" not in part for part in command
            )
        ):
            errors.append(
                f"preparation[{index}]: supply a nonempty command argument array"
            )
    if errors:
        raise ValueError(f"{source}:\n" + "\n".join(errors))
    return config


def load_project(repo: Path) -> dict:
    return parse_project((repo / ".mutagi/config.toml").read_text())


def matches(path: str, patterns: list[str], identifier: str) -> bool:
    for pattern in patterns:
        pattern = pattern.format(id=identifier)
        if path.startswith(pattern) if pattern.endswith("/") else path == pattern:
            return True

    return False
