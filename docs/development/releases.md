# Verify distributions and publish releases

[Documentation](../README.md) / Development

Use the [development setup](../../README.md#development-and-distribution-builds) before running package checks.
For installing an existing version, follow [installation and updates](../getting-started/installation.md).

## Distribution and source-update verification

Build the wheel and source distribution with `mise run verify`, then run `uv run --locked python scripts/verify_distribution.py`.
The verifier checks packaged files, installs each artifact in a fresh environment outside the checkout, and exercises execution workflows, worker entry points, status, and the copied example.

The distribution verifier exercises branch refreshes, fixed-tag retention, and explicit changes between version tags using a disposable local Git repository and isolated uv tool directories.
It checks the installed package version and recorded Git commit after each operation.
No personal tool installation or remote repository is modified by that verification.
Network access and permissions to a remote Git host are separate from the verified local Git update behavior.

## Publish a version tag

The [release workflow](../../.github/workflows/release.yml) runs when a `v*` tag is pushed.
It checks that the tag, `pyproject.toml`, and `uv.lock` versions agree and that the checked-out commit is the tagged clean commit.
It then runs the test suite, builds both distributions, verifies fresh installations and Git-source updates, and creates a GitHub release with the wheel and source archive.
Release creation uses `gh release create --verify-tag`; it cannot silently create a different tag.

Before publishing, update the project version, run `uv lock`, and complete the matching [changelog entry](../../CHANGELOG.md).
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
