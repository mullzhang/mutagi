"""Share read-only repository prerequisites between checks and execution."""

from pathlib import Path
import subprocess

from mutagi.project import parse_project


def git(repo, *args) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE, timeout=30
    ).strip()


def repository_root(path: Path, *, exact=False) -> Path:
    try:
        root = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
    except subprocess.CalledProcessError as error:
        raise ValueError(f"{path}: use an existing Git working repository") from error
    if exact and path.resolve() != root:
        raise ValueError(f"run this command at the repository root: {root}")
    return root


def require_clean(repo):
    changed = git(repo, "--no-optional-locks", "status", "--porcelain")
    if changed:
        raise RuntimeError(f"clean the working tree: {repo}\n{changed}")


def require_private_paths(repo):
    for name in (".mutagi/runs", ".mutagi/worktrees"):
        if not (repo / name).resolve().is_relative_to(repo.resolve()):
            raise ValueError(
                f"{name}: execution storage must remain inside the repository"
            )
        result = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", name + "/probe"],
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            raise ValueError(f"exclude the runner's private path from Git: /{name}/")
        tracked = git(repo, "ls-files", "--", name)
        if tracked:
            raise ValueError(
                f"{name}: remove runtime files from the Git index before execution:\n{tracked}"
            )


def require_branch(repo):
    branch = git(repo, "branch", "--show-current")
    if not branch:
        raise ValueError("check out a named branch before execution")
    return branch


def resolve_base(repo, base):
    try:
        return git(repo, "rev-parse", "--verify", base + "^{commit}")
    except subprocess.CalledProcessError as error:
        raise ValueError(
            f"cannot resolve base {base!r}; commit the project or select an existing commit"
        ) from error


def require_identity(repo):
    try:
        git(repo, "var", "GIT_AUTHOR_IDENT")
        git(repo, "var", "GIT_COMMITTER_IDENT")
    except subprocess.CalledProcessError as error:
        raise ValueError(
            "configure Git user.name and user.email for preservation commits"
        ) from error


def require_regular_file(repo, name, base=None):
    if base is None:
        path = repo / name
        valid = (
            path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(repo.resolve())
        )
    else:
        entry = git(repo, "ls-tree", base, "--", name)
        valid = entry.startswith(("100644 blob ", "100755 blob ")) and "\n" not in entry
    if not valid:
        location = "working tree" if base is None else base
        raise ValueError(
            f"{name}: provide a regular file inside the repository at {location}"
        )


def project_at(repo, base=None):
    name = ".mutagi/config.toml"
    require_regular_file(repo, name, base)
    text = (
        (repo / name).read_text()
        if base is None
        else git(repo, "show", base + ":" + name)
    )
    source = name if base is None else base + ":" + name
    project = parse_project(text, source)
    errors = []
    for path in dict.fromkeys(
        [*project["references"], *project["prompts"].values(), project["catalog"]]
    ):
        try:
            require_regular_file(repo, path, base)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError(
            "\n".join(errors)
            + "\nCreate or correct these files and commit them before execution."
        )
    return project


def require_ready(repo, base):
    require_clean(repo)
    require_private_paths(repo)
    branch = require_branch(repo)
    revision = resolve_base(repo, base)
    require_identity(repo)
    return branch, revision, project_at(repo, revision)
