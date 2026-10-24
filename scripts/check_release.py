"""Require a clean commit, a matching version tag, and a synchronized lockfile."""

import argparse
from pathlib import Path
import subprocess
import tomllib


def validate_version(root, tag):
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    expected = "v" + project["version"]
    if tag != expected:
        raise ValueError(f"release tag must be {expected}, received {tag!r}")
    lock = tomllib.loads((root / "uv.lock").read_text())
    package = next(item for item in lock["package"] if item["name"] == project["name"])
    if package["version"] != project["version"]:
        raise ValueError("uv.lock and pyproject.toml versions differ; run uv lock")
    return project["version"]


def verify_tag(root, tag):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True
        ).strip()

    if git("--no-optional-locks", "status", "--porcelain"):
        raise ValueError("commit the intended release contents before checking its tag")
    if git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}") != git(
        "rev-parse", "HEAD"
    ):
        raise ValueError("the release tag must point to the checked-out commit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    validate_version(args.repo, args.tag)
    verify_tag(args.repo, args.tag)
    print(f"Verified release tag {args.tag} at the clean current commit.")


if __name__ == "__main__":
    main()
