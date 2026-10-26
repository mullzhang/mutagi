# Source and test structure

[Documentation](../README.md) / Development

Mutagi separates command input and presentation from project configuration, execution state, and operating-system operations.
The package has four responsibility groups:

| Package | Responsibilities | Internal dependencies |
| --- | --- | --- |
| `commands` | Argument parsing, stdin, interactive initialization, previews, status, and command exit behavior | `project`, `execution`, `runtime`, and `prompts` |
| `project` | Project configuration, saved defaults, initialization assets, path validation, and Git prerequisites | Other project modules |
| `execution` | Experiments, integration, rounds, series, plan validation, recovery ownership, usage, and App handoff | `project`, `runtime`, and `prompts` |
| `runtime` | POSIX process identity and cleanup, detached command jobs, file storage, and launchd installation | Other runtime modules |

`cli.py` dispatches the public commands and exposes the `mutagi.cli:main` entry point.
`__main__.py` supports `python -m mutagi`.
`prompts.py` reads bundled resources from `importlib.resources.files("mutagi")`; the assets remain under `resources/`.

## Execution and recovery

The controllers depend on one another in this direction:

```text
service -> series -> round -> integration -> experiment
handoff ----------------------------------> experiment
codex_session -> usage -> policy
```

`execution/plans.py` validates inputs and resolves defaults from the selected base commit.
`execution/state.py` owns execution IDs, locking, terminal phases, checkpoint lookup, and recovery ownership.
Status and prerequisite commands read these operations without importing controllers.

`execution/experiment.py` keeps `Runner` and its preparation, execution, collection, preservation, registration, and recovery sequence together.
`execution/integration.py` contains the existing `Integration(Runner)` specialization for knowledge-only scope and publication of the preserved tree.
Separating these files does not introduce a new controller hierarchy or alter saved-state fields.

`execution/catalog.py` reads committed registration blocks and validates their immutable observation records.
`execution/harvest.py` prepares detached trials, selected source excerpts, and packaged harvesting prompts without starting a model.
`execution/harvest_records.py` stores decision notes on the independent `mutagi/harvests` branch using Git objects and an atomic reference update, without acquiring the execution lock or changing a working tree or index.

The execution layer does not import command presentation.
Two internal workers parse their recorded directory arguments when started in subprocesses:

- `python -m mutagi.runtime.jobs _supervise DIRECTORY` runs a detached command supervisor.
- `python -m mutagi.execution.codex_session RUN` manages an App Server turn and its recorded usage budget.

The job supervisor receives permission to start only after the parent saves process ownership.
LaunchAgents invoke the public `python -m mutagi service --repo REPOSITORY tick` entry point.
Existing services need the [service update procedure](../guides/macos-service.md#update-a-registered-macos-service) when their saved invocation changes.

## Tests and shared preparation

`tests/commands`, `tests/project`, `tests/execution`, and `tests/runtime` follow the source responsibility groups.
Test files describe behavior, so a workflow such as preservation can cover several implementation modules in one file.
`tests/packaging` checks the copied example and release rules.

Shared fixtures live in `conftest.py` at the narrowest common directory.
Callable factories and helpers live in `tests/support`; the project configuration fixture lives in `tests/fixtures/project.toml`.
Tests do not import other test modules or register them as pytest plugins.
`pyproject.toml` is the only pytest configuration and supports collection from either the repository root or `tests`.

`tests/test_architecture.py` checks package boundaries, import cycles, and imports between test modules, including imports inside functions.
The runtime tests cover saved launchd commands using temporary files and a simulated `launchctl`.
Process and recovery tests continue to use real child processes and isolated Git repositories.

Run `mise run verify` for the complete checks and builds.
Then run `uv run --locked python scripts/verify_distribution.py` to check fresh wheel and source installations, worker entry points, copied examples, and isolated Git-source updates outside the source checkout.
