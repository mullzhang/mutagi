# Using mutagi

- [Prepare a project](getting-started.md): generate configuration and prompts, check prerequisites, then start an exploration.
- [Install and update](installation.md): track a branch or select a fixed release and understand the release workflow.
- [Inspect status](status.md): distinguish saved checkpoints, current process observations, results, and adoption.
- [Runnable example](../examples/README.md): copy a complete Python project into a separate repository and try its tasks and plans.
- [Exploration through harvesting](overview.md): how exploration, preservation, inheritance, knowledge integration, and human harvesting relate.
- [Harvest results](harvesting-guide.md): find results, try saved revisions, and record the selected parts and decisions.

mutagi operates on a project's existing source tree.
Place `.mutagi/config.toml` in the target Git repository to specify what to explore.
The current implementation uses Git and Codex CLI. Declare external preparation commands explicitly in `preparation`.

## Configuration

| Field | Purpose |
| --- | --- |
| `name` | Display name of the project being explored |
| `preparation` | Commands run sequentially in each worktree; argument arrays, not shell strings |
| `references` | Relative paths to instructions and procedures the agent must read |
| `experiments` | Directory for individual experiment records |
| `catalog` | Relative path to the catalog registered by the controller and updated by integration |
| `exploration_paths` | Paths the explorer may change; a trailing `/` includes descendants, and `{id}` expands to the current UUID |
| `integration_paths` | Paths the integrator may change, using the same matching rules |
| `prompts` | Relative paths to the `explore`, `inherit`, and `integrate` project prompts |
| `execution` (optional) | Explicit common model/effort and per-command defaults; read from the selected base commit |
| `generated_hashes` | Files and keys for which only generated hash lines may change; use an empty table when unused |

All fields except `execution` are required. Paths are relative to the target repository root.
Preparation and exploration conditions are read from the base commit and saved in run state.
`generated_hashes` recognizes APM lines of the form `  <key>: sha256:<64 hex digits>` and permits no other changes.
See [saved execution defaults](getting-started.md#save-explicit-execution-defaults) for precedence and the optional table format.
Configuration does not itself grant execution permissions. Align agent permissions with the controller's preservation scope.

## Commands

Use `mutagi` from an installed environment.
Inside the package's development directory, `uv run mutagi` is also available.
Run from the target repository root or supply it through `--repo` where supported.

| Command | Purpose |
| --- | --- |
| [`mutagi init`](getting-started.md#initialize-an-existing-repository) | Generate project files without overwriting existing files |
| [`mutagi check`](getting-started.md#check-execution-prerequisites) | Report execution prerequisites and resume commands without starting work |
| [`mutagi experiment`](runner.md) | Start, resume, and preserve one exploration |
| [`mutagi round`](round-runner.md) | Run one round of explorations, knowledge integration, and operational assessment |
| [`mutagi series`](series-runner.md) | Run multiple rounds within a budget |
| [`mutagi service`](unattended.md) | Manage the macOS supervisor |
| `mutagi handoff` | Verify App execution results supplied by the controller |
| [`mutagi job`](command-job.md) | Manage an external command with a deadline |
| `mutagi template experiment` | Print the bundled experiment record template |
| `mutagi --version` | Display the installed package version |

`handoff` preserves results using run state and a receipt prepared by the controller. It does not create or stop App chats.
Append `--help` to a command to inspect its arguments.

## Project files

Track `.mutagi/config.toml` and `.mutagi/prompts/` in Git, and track `.mutagi/plans/` if you choose to keep reusable input files there.
Exclude `.mutagi/runs/` and `.mutagi/worktrees/` in `.gitignore`.
See [observation records and reproducibility](runner.md#observation-records-and-reproducibility) for experiment preservation.

Write project-specific exploration policy in the project prompts and reference existing specifications or development procedures through `references`.
The controller supplies its bundled record template automatically; a project-specific record-template selector is not provided.
Use these guides for general concepts and harvesting procedures; projects do not need their own copies of that documentation.
