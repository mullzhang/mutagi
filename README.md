# mutagi

A Python package for agents to investigate questions, develop results and knowledge, and help people harvest useful code, ideas, and counterexamples.
See [exploration through harvesting](docs/concepts/exploration-and-harvesting.md) and the [harvesting guide](docs/guides/harvesting.md).
Requires Python 3.12+, POSIX, Git, and an authenticated Codex CLI.

## Execution and usage limits

Use mutagi only with repositories and preparation commands you trust. It runs project commands, delegates executable work to Codex, and creates Git branches and commits. Exploration paths limit the changes the controller accepts; they do not provide OS-level isolation.

Time limits can be exceeded while stopping and preserving work, and usage limits do not guarantee strict billing caps. Read the [project setup and boundaries](docs/getting-started/first-exploration.md) and [usage monitoring limits](docs/guides/adaptation-and-usage.md#usage-and-stopping) before starting an exploration. For suspected vulnerabilities, follow the [security reporting policy](SECURITY.md).

## Installation and use

Install mutagi from GitHub:

```bash
uv tool install git+https://github.com/mullzhang/mutagi.git
mutagi --version
mutagi --help
```

From the root of the existing Git repository you want to explore, generate its configuration and project policies:

```bash
mutagi init --explore-path src/ --explore-path tests/ --reference README.md
```

Choose paths that exist in your project or use `mutagi init` for a minimal configuration.
[Review and commit the generated files, check prerequisites, and start the first exploration](docs/getting-started/first-exploration.md).
One-off instructions can be passed with `--prompt`; task files and standard input are also supported.
Use `mutagi init --interactive` to choose settings with a preview, or save explicit model and effort defaults through initialization options.
Rounds and series can be constructed from CLI arguments; `start --dry-run` prints the resolved inputs before execution.
See [fixed versions and updates](docs/getting-started/installation.md) and [readable status](docs/guides/status.md).
For a complete project to copy and try, see the [runnable example](examples/README.md).
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
Internal prompts, initialization assets, and the experiment record template are included in the wheel.
The source distribution also includes the runnable example.

See [source and test structure](docs/development/architecture.md) and [distribution verification and releases](docs/development/releases.md) for contributor details.
