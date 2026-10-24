# Prepare a project and start an exploration

Install mutagi and authenticate the Codex CLI before starting model work.
Use Python 3.12+, POSIX, and Git. Install any tools needed by the project's preparation commands.
For a complete project to copy, follow the [runnable example](../examples/README.md).

## Initialize an existing repository

Run from the root of the Git repository you want to explore:

```bash
mutagi init --explore-path src/ --explore-path tests/ --reference README.md
```

Choose application paths and existing reference files appropriate to the project.
`mutagi init` with no options also works: it permits changes to knowledge notes and each exploration's own directory, with no application paths or reference files added.
Exploration paths define the controller's accepted change scope; they are not an OS-level sandbox.

| Option | When supplied | When omitted |
| --- | --- | --- |
| `--repo PATH` | Use this existing Git repository root | Use the current directory; reject a subdirectory with the root path in the error |
| `--name NAME` | Set the project display name | Use the repository directory name |
| `--explore-path PATH` | Add a repository-relative application path; repeat for multiple paths | Add no application paths |
| `--reference PATH` | Add an existing regular reference file inside the repository; repeat as needed | Add no references |
| `--dry-run` | Show generated paths, contents, and ignore additions without writing | Generate the files |
| `--preset NAME` | Save one explicitly selected preparation command set | Leave preparation empty |
| `--model NAME`, `--effort LEVEL` | Save the selected execution defaults | Save no model or effort defaults |
| `--interactive` | Ask for omitted settings, preview generated files, and confirm writing | Use only the supplied options |

A trailing `/` in an exploration path includes its descendants; a path without it matches that path only.
`{id}` in a scope pattern expands to the current exploration's UUID.

Initialization creates:

```text
.mutagi/config.toml
.mutagi/prompts/explore.md
.mutagi/prompts/inherit.md
.mutagi/prompts/integrate.md
experiments/INDEX.md
knowledge/README.md
knowledge/notes/.gitkeep
```

It also appends missing exclusions for `.mutagi/runs/` and `.mutagi/worktrees/` to `.gitignore`, preserving existing content.
All generated-file collisions are checked before writing. Existing generated files, obstructing paths, or symlinked output paths stop initialization without overwriting user content.
On an already configured project, use `mutagi check` and edit its existing files.
Initialization does not create a Git repository, commit, install dependencies, authenticate, register a service, or launch a model.
A Git repository with no commits is sufficient for initialization; execution requires a commit.

## Choose preparation explicitly

`--preset` writes a documented command array into `preparation`. It does not infer a language, install anything during initialization, or grant additional application paths.

| Preset | Required existing files | Saved preparation |
| --- | --- | --- |
| `python-stdlib` | None; the project must provide its `tests/` suite before execution | `python3 -m unittest discover -s tests` |
| `python-uv` | `pyproject.toml`, `uv.lock` | `uv sync --locked --dev` |
| `node-npm` | `package.json`, `package-lock.json` | `npm ci` |
| `rust-cargo` | `Cargo.toml`, `Cargo.lock` | `cargo fetch --locked` |

The declared manifest files must be regular files inside the repository. Manifest compatibility and preparation success are checked by the chosen tool when preparation runs, not by initialization.
For other layouts, edit the generated preparation arrays before committing.

```bash
mutagi init --preset python-uv --explore-path src/ --explore-path tests/ \
  --model "$MODEL" --effort medium
```

## Initialize interactively

```bash
mutagi init --interactive
```

Interactive mode requires a terminal. It asks for the project name, preparation preset, application paths, references, model, and reasoning effort when those options were omitted.
Enter paths one per line, followed by a blank line. Blank model or effort answers save no default; no model is guessed.
The generated files and ignore additions are shown before a default-no confirmation.
Cancellation or EOF during the questions creates no files. `--interactive --dry-run` collects answers and prints the preview without asking to write.
For scripts, use explicit options instead of piped interactive answers.

## Save explicit execution defaults

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

## Review project policy and commit

Review `.mutagi/config.toml` and the three generated project prompts.
The prompts provide general exploration, inheritance, and knowledge-integration policy. Add project-specific interests, constraints, and verification methods.
The controller automatically supplies the bundled experiment record template to independent and inherited explorations.
`mutagi template experiment` prints that same template for inspection. Custom record-template selection is not supported.

| Prompt | Purpose |
| --- | --- |
| `explore.md` | Choose a question, inspect current behavior, and test it with a small comparison or counterexample |
| `inherit.md` | Read a parent's saved result and test a question newly made possible by that evidence |
| `integrate.md` | Check saved observations and update shared knowledge and the catalog with traceable evidence |

`generated_hashes` is initially empty. `preparation` is empty unless a preset was explicitly chosen.
If preparation is required, edit `preparation` to contain command argument arrays, such as `[["uv", "sync", "--locked", "--dev"]]` for a suitable uv project.
Preparation runs in each dedicated worktree. Declare only commands appropriate to that project.
See the [configuration reference](README.md#configuration) for all required fields.

After reviewing the generated files, commit them on a named branch:

```bash
git add -- .mutagi/config.toml .mutagi/prompts \
  experiments/INDEX.md knowledge/README.md knowledge/notes/.gitkeep .gitignore
git commit -m "chore: configure mutagi exploration"
mutagi check
```

The runner reads configuration and project prompts from the selected commit.
A local edit must be committed before it can affect execution.

## Check execution prerequisites

```bash
mutagi check
mutagi check --repo /path/to/project --base HEAD
```

The default base is `HEAD`.
The check reports independent problems together and returns a nonzero exit status when a prerequisite fails.
It checks runtime versions, the required Codex execution options, login status, a named branch, the base commit, a clean working tree, Git author identity, ignored and untracked runtime storage, current and committed project files, and unfinished executions.
For unfinished work it prints the corresponding experiment, round, or series resume command.
Required configuration fields, types, paths, reference files, prompts, and the catalog are checked through the same validation used at launch.

Preparation commands are validated and displayed, but are not executed by this check.
The check does not launch a model or create execution state, branches, or worktrees.
Model access, preparation success, actual Git write permissions, and future network availability remain untested until those operations run.

## Start the first exploration

Set `MODEL` in your shell to a model available to your account.
Pass a one-off instruction directly:

```bash
mutagi experiment start --base HEAD \
  --prompt "Choose one concrete assumption in this project and test it with a small comparison." \
  --model "$MODEL" --effort medium --seconds 1200 --reserve-seconds 180
```

This allocation includes preparation and reserves three minutes for preservation and registration.
Process-stop grace and preservation work can extend the wall-clock finish time.
The exact instruction text is retained in the execution's `task.txt`; no instruction file is required beforehand.

For reusable instructions, use `--task path/to/instruction.txt`. To pipe an instruction from another program, use `--task -`:

```bash
printf '%s\n' 'Compare one boundary condition and record the limitations.' |
  mutagi experiment start --base HEAD --task - \
    --model "$MODEL" --effort medium --seconds 1200 --reserve-seconds 180
```

Choose exactly one of `--prompt` and `--task`. Empty instructions are rejected before execution state is created.
Files are read as UTF-8; `--task -` reads standard input to EOF. A missing file is an error.
If an instruction file is stored in the target repository, commit it before starting so the shared working tree remains clean.

See [running one exploration](runner.md) for status and recovery.
After preservation, read the experiment catalog and use the [harvesting guide](harvesting-guide.md) to inspect the saved implementation and evidence.
For multiple explorations, use a [round](round-runner.md) or [series](series-runner.md) plan. Plans can come from a file or standard input, with explicit model, effort, and time overrides.

## Preview without starting work

Append `--dry-run` to `experiment start`, `round start`, or `series start` to print validated, resolved inputs as JSON.
The base is a full commit SHA, and project defaults come from that commit.
No execution IDs, state directories, locks, branches, worktrees, preparation commands, or model sessions are created.
A preview validates input and committed project files; it does not establish authentication, a clean working tree, or runtime availability. Use `mutagi check` for those prerequisites.

For rounds and series, the output is a reusable plan accepted by `--plan FILE` or `--plan -`.
The preview can run while the working tree is dirty; actual execution still requires a clean tree.
Save previews outside the target repository, or commit a saved plan before starting execution.

See [round CLI inputs](round-runner.md#construct-tasks-with-cli-arguments), [series CLI inputs](series-runner.md#repeat-a-task-sequence-with-cli-arguments), and [status and recovery](status.md).
