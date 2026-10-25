# Project configuration

[Documentation](../README.md) / Reference

Place `.mutagi/config.toml` in the target Git repository to specify what to explore.
Mutagi operates on that project's existing source tree using Git and the Codex CLI.
Declare external preparation commands explicitly in `preparation`.

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
See [saved execution defaults](#execution-defaults) for precedence and the optional table format.
Configuration does not itself grant execution permissions. Align agent permissions with the controller's preservation scope.

## Execution defaults

`init --model "$MODEL" --effort medium` saves common defaults. Existing projects can add the optional `execution` table to `.mutagi/config.toml` and commit it.
Budget settings can differ between command types:

```toml
[execution]
model = "your-chosen-model"
effort = "medium"

[execution.experiment]
seconds = 1200
reserve_seconds = 180

[execution.round]
seconds = 2400
experiment_seconds = 900
integration_seconds = 600
reserve_seconds = 120

[execution.series]
seconds = 4800
round_seconds = 2400
experiment_seconds = 900
integration_seconds = 600
reserve_seconds = 120
```

Replace `your-chosen-model` with an available model. Each table may contain only the values you want to save; a per-command table can also override `model` and `effort`.
The common table accepts only `model`, `effort`, and the three command tables. Each command table accepts the fields shown for its command plus `model` and `effort`.
The base revision, instructions, tasks, number of rounds, usage rates, and adaptation policy remain explicit execution inputs.

Resolution reads defaults from the selected base commit, then overlays the input plan, then explicitly supplied CLI arguments.
Uncommitted defaults and global model settings do not affect a run. Invalid or unknown settings are rejected.
The resolved input must specify a model and reasoning effort, and all budgets must pass the existing command validation.
Single explorations retain their documented 3600-second total and 600-second reserve when neither an explicit setting nor an argument supplies those durations; rounds and series require every budget through saved settings, the plan, or arguments.
Resolved values are saved at launch, and resume uses the saved values even if project defaults change later.

## Project files

Track `.mutagi/config.toml` and `.mutagi/prompts/` in Git, and track `.mutagi/plans/` if you choose to keep reusable input files there.
Exclude `.mutagi/runs/` and `.mutagi/worktrees/` in `.gitignore`.
See [observation records and reproducibility](../guides/experiments.md#observation-records-and-reproducibility) for experiment preservation.

Write project-specific exploration policy in the project prompts and reference existing specifications or development procedures through `references`.
The controller supplies its bundled record template automatically; a project-specific record-template selector is not provided.
Use these guides for general concepts and harvesting procedures; projects do not need their own copies of that documentation.
