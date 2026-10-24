# Install, update, and release mutagi

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and use Python 3.12 or newer.
Execution also requires POSIX, Git, and an authenticated Codex CLI.

## Track the development branch

```bash
uv tool install git+https://github.com/mullzhang/mutagi.git@main
mutagi --version
```

Update that installation from the same branch:

```bash
uv tool upgrade mutagi
mutagi --version
```

A branch update can change the installed commit without changing the package version.
To inspect the installed source and commit, use `uv tool list --show-paths` and the environment's `direct_url.json` metadata when exact provenance is needed.
Do not edit an installed tool environment to update the package.

## Select a fixed release or commit

Choose an existing tag from [GitHub releases](https://github.com/mullzhang/mutagi/releases) and set `TAG` to that tag.
Tags have the form `v<package-version>`.
A local source version is not evidence that its release tag has been published.

```bash
: "${TAG:?Set TAG to an existing release tag}"
uv tool install "git+https://github.com/mullzhang/mutagi.git@${TAG}"
mutagi --version
```

For a fixed commit, replace the tag with its full commit SHA in the same Git URL.
A tag or commit installation remains fixed when `uv tool upgrade mutagi` is run.
To select another release, set `TAG` to the chosen existing tag and repeat the install command.
To return to branch updates, reinstall with `@main`.
These source constraints follow [uv's tool upgrade rules](https://docs.astral.sh/uv/guides/tools/#upgrading-tools).

The distribution verifier exercises branch refreshes, fixed-tag retention, and explicit changes between version tags using a disposable local Git repository and isolated uv tool directories.
It checks the installed package version and recorded Git commit after each operation.
No personal tool installation or remote repository is modified by that verification.
Network access and permissions to a remote Git host are separate from the verified local Git update behavior.

## Publish a version tag

The [release workflow](../.github/workflows/release.yml) runs when a `v*` tag is pushed.
It checks that the tag, `pyproject.toml`, and `uv.lock` versions agree and that the checked-out commit is the tagged clean commit.
It then runs the test suite, builds both distributions, verifies fresh installations and Git-source updates, and creates a GitHub release with the wheel and source archive.
Release creation uses `gh release create --verify-tag`; it cannot silently create a different tag.

Before publishing, update the project version, run `uv lock`, and complete the matching [changelog entry](../CHANGELOG.md).
Review and commit the intended changes, then run the full checks at that commit.
The following commands publish the selected version; use them only for an intended release:

```bash
mise run verify
uv run --locked python scripts/verify_distribution.py
: "${TAG:?Set TAG to v followed by the project version}"
git tag -a "$TAG" -m "Release $TAG"
uv run --locked python scripts/check_release.py "$TAG"
git push origin main
git push origin "$TAG"
```

A failed validation leaves the tag without a new release. Inspect the workflow failure instead of replacing an existing published tag.
The workflow publishes GitHub artifacts; it does not upload to PyPI.
Local validation does not by itself publish a tag, run GitHub Actions, or update users' installations.
