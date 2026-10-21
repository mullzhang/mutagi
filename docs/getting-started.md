# Prepare a project

Install mutagi and work in the Git repository you want to explore.
Use a named branch and commit configuration and prompts before execution.
The example below assumes implementation under `src/` and tests under `tests/`.
Adjust paths and verification procedures for the target project.

## Configuration and storage

Save the following as `.mutagi/config.toml`.

```toml
name = "example-project"
preparation = []
references = []
experiments = "experiments"
catalog = "experiments/INDEX.md"
exploration_paths = ["src/", "tests/", "knowledge/notes/"]
integration_paths = ["knowledge/README.md", "knowledge/notes/", "experiments/INDEX.md"]

[prompts]
explore = ".mutagi/prompts/explore.md"
inherit = ".mutagi/prompts/inherit.md"
integrate = ".mutagi/prompts/integrate.md"

[generated_hashes]
```

If preparation is needed, list command argument arrays in `preparation`.
For example, a Python project using uv could use `[["uv", "sync", "--locked", "--dev"]]`.
List repository-relative paths to existing specifications or development procedures in `references`.
Both arrays above are empty for a project that needs no preparation or additional references.
See the [configuration reference](README.md#configuration) for field definitions.

Create `experiments/INDEX.md` and `knowledge/README.md` with headings and no entries, and create `knowledge/notes/`.
The experiment catalog provides entry points to saved revisions and records; the knowledge README provides entry points to updated understanding.
Experiment folders and execution state are created during execution.
Add the following to `.gitignore`.

```gitignore
.mutagi/runs/
.mutagi/worktrees/
```

## Write project policy

Create these three Markdown files under `.mutagi/prompts/`.
mutagi adds internal execution instructions. In both `explore.md` and `inherit.md`, instruct the agent to run `mutagi template experiment` and use its output for the experiment README.
Describe project-specific interests, applicability conditions, and verification methods in these prompts.

| File | Example content |
| --- | --- |
| `explore.md` | Consider up to three distinct questions grounded in this project's users and use cases, then choose one. Test it with a minimal comparison or counterexample and record the verification method and limitations. |
| `inherit.md` | Read the parent's saved revision and results. Choose a question that those results make newly testable, and record changed conditions and inherited evidence. |
| `integrate.md` | Check every target's saved revision. Update knowledge with changes in understanding and competing hypotheses. Record evidence, verification scope, and next questions in the catalog. |

Write the interests or constraints for this execution in `.mutagi/plans/first.txt`.
For example: "Choose one condition where missing input could cause a user to misinterpret a result, and test it with a concrete example."
Commit configuration, the catalog, the knowledge entry point, prompts, and the execution plan, then check that no uncommitted changes remain.

## Start the first exploration

Specify an available model.
This example allows up to 20 minutes for work and preservation, reserving 3 minutes for preservation.

```bash
mutagi experiment start --base HEAD \
  --task .mutagi/plans/first.txt \
  --model <available-model> --effort medium \
  --seconds 1200 --reserve-seconds 180
```

See [running one exploration](runner.md) for status and recovery, and [running a round](round-runner.md) for plans with multiple explorations.
After preservation, use the experiment catalog and [harvesting guide](harvesting-guide.md) to read and try the results.
