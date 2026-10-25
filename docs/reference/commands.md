# Command reference

[Documentation](../README.md) / Reference

Choose a command below, then follow its guide for inputs, execution, and recovery.
This reference also lists initialization options and shared preview behavior.

## Commands

Use `mutagi` from an installed environment.
Inside the package's development directory, `uv run mutagi` is also available.
Run from the target repository root or supply it through `--repo` where supported.

| Command | Purpose |
| --- | --- |
| [`mutagi init`](../getting-started/first-exploration.md#initialize-an-existing-repository) | Generate project files without overwriting existing files |
| [`mutagi check`](../getting-started/first-exploration.md#check-execution-prerequisites) | Report execution prerequisites and resume commands without starting work |
| [`mutagi experiment`](../guides/experiments.md) | Start, resume, and preserve one exploration |
| [`mutagi round`](../guides/rounds.md) | Run one round of explorations, knowledge integration, and operational assessment |
| [`mutagi series`](../guides/series.md) | Run multiple rounds within a budget |
| [`mutagi service`](../guides/macos-service.md) | Manage the macOS supervisor |
| `mutagi handoff` | Verify App execution results supplied by the controller |
| [`mutagi job`](../guides/command-jobs.md) | Manage an external command with a deadline |
| `mutagi template experiment` | Print the bundled experiment record template |
| `mutagi --version` | Display the installed package version |

`handoff` preserves results using run state and a receipt prepared by the controller. It does not create or stop App chats.
Append `--help` to a command to inspect its arguments.

## Initialization options

For the complete first-run workflow, see [prepare a project](../getting-started/first-exploration.md).
These options control `mutagi init`; existing project files are never overwritten.

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

## Preview without starting work

Append `--dry-run` to `experiment start`, `round start`, or `series start` to print validated, resolved inputs as JSON.
The base is a full commit SHA, and project defaults come from that commit.
No execution IDs, state directories, locks, branches, worktrees, preparation commands, or model sessions are created.
A preview validates input and committed project files; it does not establish authentication, a clean working tree, or runtime availability. Use `mutagi check` for those prerequisites.

For rounds and series, the output is a reusable plan accepted by `--plan FILE` or `--plan -`.
The preview can run while the working tree is dirty; actual execution still requires a clean tree.
Save previews outside the target repository, or commit a saved plan before starting execution.

See [round CLI inputs](../guides/rounds.md#construct-tasks-with-cli-arguments), [series CLI inputs](../guides/series.md#repeat-a-task-sequence-with-cli-arguments), and [status and recovery](../guides/status.md).
