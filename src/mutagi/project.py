"""Read the exploration settings explicitly provided by a project."""

from pathlib import Path, PurePosixPath
import tomllib


def parse_project(text: str) -> dict:
    config = tomllib.loads(text)
    paths = [
        config["experiments"],
        config["catalog"],
        *config["references"],
        *config["prompts"].values(),
        *config["exploration_paths"],
        *config["integration_paths"],
        *config["generated_hashes"],
    ]
    for value in paths:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] == ".git"
        ):
            raise ValueError(f"a project-relative path is required: {value}")

    for command in config["preparation"]:
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ValueError("preparation must contain nonempty command argument arrays")

    return config


def load_project(repo: Path) -> dict:
    return parse_project((repo / ".mutagi/config.toml").read_text())


def matches(path: str, patterns: list[str], identifier: str) -> bool:
    for pattern in patterns:
        pattern = pattern.format(id=identifier)
        if path.startswith(pattern) if pattern.endswith("/") else path == pattern:
            return True

    return False
