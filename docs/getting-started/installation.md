# Install and update mutagi

[Documentation](../README.md) / Getting started

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and use Python 3.12 or newer.
Execution also requires POSIX, Git, and an authenticated Codex CLI.
If the installation runs a macOS service, follow the [service update procedure](../guides/macos-service.md#update-a-registered-macos-service) before updating its package.

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

After installation, [prepare a project and start an exploration](first-exploration.md).
For publishing package versions, see the [release procedure](../development/releases.md).
