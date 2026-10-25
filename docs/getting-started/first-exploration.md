# Prepare a project and start an exploration

[Documentation](../README.md) / Getting started

[Install mutagi](installation.md) and authenticate the Codex CLI before starting model work.
Use Python 3.12+, POSIX, and Git. Install any tools needed by the project's preparation commands.
For a complete project to copy, follow the [runnable example](../../examples/README.md).

## Initialize an existing repository

Run from the root of the Git repository you want to explore:

```bash
mutagi init --explore-path src/ --explore-path tests/ --reference README.md
```

Choose application paths and existing reference files appropriate to the project.
`mutagi init` with no options also works: it permits changes to knowledge notes and each exploration's own directory, with no application paths or reference files added.
Exploration paths define the controller's accepted change scope; they are not an OS-level sandbox.

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

Use the [initialization options](../reference/commands.md#initialization-options) to choose a preparation preset or initialize interactively.
To reuse a selected model, effort, or time allocation, save [execution defaults](../reference/configuration.md#execution-defaults) before committing.

## Review project policy and commit

Review `.mutagi/config.toml` and the three generated project prompts.
The prompts provide general exploration, inheritance, and knowledge-integration policy. Add project-specific interests, constraints, and verification methods.
The controller automatically supplies the bundled experiment record template to independent and inherited explorations, with the execution ID, start time, mode, parents, and base revision already filled.
Keep those assigned values unchanged in the record and reuse the same ID in knowledge references.
`mutagi template experiment` prints the template with placeholders for inspection before an execution is assigned. Custom record-template selection is not supported.

| Prompt | Purpose |
| --- | --- |
| `explore.md` | Choose a question, inspect current behavior, and test it with a small comparison or counterexample |
| `inherit.md` | Read a parent's saved result and test a question newly made possible by that evidence |
| `integrate.md` | Check saved observations and update shared knowledge and the catalog with traceable evidence |

`generated_hashes` is initially empty. `preparation` is empty unless a preset was explicitly chosen.
If preparation is required, edit `preparation` to contain command argument arrays, such as `[["uv", "sync", "--locked", "--dev"]]` for a suitable uv project.
Preparation runs in each dedicated worktree. Declare only commands appropriate to that project.
See the [configuration reference](../reference/configuration.md#configuration) for all required fields.

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

Append `--dry-run` to [preview the resolved inputs](../reference/commands.md#preview-without-starting-work) without creating a run.

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

See [running one exploration](../guides/experiments.md) for status and recovery.
After preservation, read the experiment catalog and use the [harvesting guide](../guides/harvesting.md) to inspect the saved implementation and evidence.
For multiple explorations, use a [round](../guides/rounds.md) or [series](../guides/series.md) plan. Plans can come from a file or standard input, with explicit model, effort, and time overrides.
