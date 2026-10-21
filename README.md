# mutagi

A Python package for agents to investigate questions, develop results and knowledge, and help people harvest useful code, ideas, and counterexamples.
See [exploration through harvesting](docs/overview.md) and the [harvesting guide](docs/harvesting-guide.md).
Requires Python 3.12+, POSIX, Git, and an authenticated Codex CLI.

## Installation and use

Install mutagi from GitHub:

```bash
uv tool install git+https://github.com/mullzhang/mutagi.git
mutagi --help
```

[Prepare the target Git repository](docs/getting-started.md) before starting an exploration.
See the [user guide](docs/README.md) for configuration and command details.

## Development and distribution builds

Install [mise](https://mise.jdx.dev/getting-started.html), then run from the repository root:

```bash
mise trust
mise install
mise run setup
mise run verify
```

The wheel and source distribution are written to `dist/`.
Internal prompts and the experiment record template are included in the package.
