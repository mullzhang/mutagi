# Run one exploration

`mutagi experiment` runs one exploration in a new Codex CLI session, preserves it, registers it in the catalog, and stops.
It supports independent exploration and inherited exploration from a parent's saved revision.
It does not run a series, integrate knowledge, merge experiment branches into shared implementation, or push.
When called by the [round runner](round-runner.md), each registration is checked before the next exploration starts, and knowledge integration runs in a separate final session.

## Requirements

Use Python 3.12+, POSIX, Git, and an authenticated Codex CLI.
Install any tools used by configured preparation commands.
The runner invokes `codex exec --json --output-schema --approve-for-me`.
`--approve-for-me` enables `workspace-write` and automatic approval review, so the incompatible `--sandbox` option is not passed.
Options that disable approvals and sandboxing are not used.
Operation has been verified with Codex CLI 0.153.4. See the [official non-interactive execution documentation](https://learn.chatgpt.com/docs/non-interactive-mode) for CLI behavior.

The execution environment must permit dedicated branches and worktrees, preparation commands, and process inspection with `ps`.
If preparation fails, no model starts. If a worktree is available, the runner recovers it as `blocked`.
If a worktree could not be created, the runner retains state and stops; resolve the cause, such as permissions, before resuming.
To add usage-based stopping, see [usage limits](unattended.md#usage-and-stopping).

## Start an independent exploration

Use a named branch for the shared working tree and leave it free of uncommitted changes.
The base revision is resolved to a full commit ID at launch.
Set `MODEL` to a model available to your account and pass exploration-specific interests or constraints directly:

```bash
mutagi experiment start \
  --base HEAD \
  --prompt "Compare one concrete assumption using a small reproducible case." \
  --model "$MODEL" --effort medium \
  --seconds 3600 --reserve-seconds 600
```

This example uses the shared revision at launch. To use a common base across a round, specify that fixed commit ID.
An example task is: "Use a small case with erroneous answers or differing costs to consider up to three distinct questions. Compare the methods' advantages and limitations. A counterexample to a universal guarantee is not a required outcome."
The controller does not need to choose the final question.

Alternatively, supply a UTF-8 file with `--task path/to/instruction.txt`, or read standard input to EOF with `--task -`.
Exactly one of `--prompt` and `--task` is required. Empty instructions, conflicting inputs, and missing files fail before execution state is allocated.
All input methods save the instruction in `.mutagi/runs/<UUID>/task.txt` for the same fixed execution record.
An instruction file kept in the target repository must be committed to keep the shared tree clean; a direct instruction or stdin requires no input file.
Use `mutagi check --base HEAD` to inspect prerequisites without starting work.
Model and effort may instead come from explicit [execution defaults](getting-started.md#save-explicit-execution-defaults) in the selected base commit. CLI values take precedence.
Append `--dry-run` to print the resolved base, instruction, model, effort, durations, and parent references as JSON without creating or executing a run.
The preview reads committed project files and checks input and inheritance references; it does not run preparation or establish runtime readiness.
See [status output](status.md) for the readable checkpoint display and `--json` for scripts.

A full UUIDv4 and UTC start time are generated once at launch and retained in these locations.

| Location | Contents |
| --- | --- |
| `.mutagi/runs/<UUID>/state.json` | Base revision, parents, requested model, deadlines, Codex session ID, descendant process identities, phase, saved commit, and registration commit |
| `.mutagi/runs/<UUID>/` | Fixed prompt, structured result, CLI events, and preparation and preservation logs |
| `.mutagi/runs/<UUID>/recovery/<recovery-UUID>/` | Binary diff, tar archive of target files, and SHA-256 hashes in persistent recovery storage |
| `.mutagi/worktrees/<UUID>/` | Dedicated exploration worktree |
| `codex/explore-<UUID>` | Branch retaining implementation and observations |

Logs and recovery files are kept locally and excluded from Git tracking.
Include required inputs and logs in preserved experiment artifacts before deleting the worktree.
Raw CLI logs are not automatically copied into the shared catalog or Git.

## Experiment names and IDs

Use full lowercase UUIDv4 values for experiment IDs and parent references.
Controllers generate them independently; no shared sequence number needs reserving.
Create an experiment folder named `YYYY-MM-DDTHHMMSSZ_<slug>_<UUID>` under the configured `experiments` directory.
The timestamp is the UTC start time, and `slug` describes the question using lowercase English words and hyphens.
Resuming the same exploration retains its ID and start time. Inheriting a different question creates new ones.
Do not overwrite existing experiment folders.

## Observation records and reproducibility

The controller includes the bundled [experiment record template](../src/mutagi/resources/templates/experiment.md) in both independent and inherited exploration instructions.
Use `mutagi template experiment` to print the same template for inspection. Custom template selection is not supported.
The explorer records the question, comparison targets, decision method, and starting conditions, then preserves work in this order:

1. Commit the implementation, inputs, and verifier used for observation.
2. Observe that revision and record results, limitations, and reproduction steps.
3. Update knowledge where supported by evidence and save it with the observation record.

Distinguish source material, new observations, interpretations, and human decisions.
Reference the implementation commit from the record; do not embed the record's own commit ID within that same commit.
Use `observed`, `refuted`, `unresolved`, or `blocked` for results, and record interruption separately as an exit reason.

The controller checks preservation and records every experiment's ID, parents, findings, limitations, saved commit, and record entry point in the configured `catalog`.
Experiment folders do not need to be collected on the shared branch.
Reference files on other branches by saved commit and path. Retain a reference branch or tag and the catalog even after removing worktrees.

## Inherited exploration

Pass the saved commit containing the parent's code, revised notes, and record to `--base`.
Also specify the parent ID and an observation record present at that revision.
Save the new task as `.mutagi/plans/inherited-exploration.txt` and commit it on the controller's shared branch.
Set these variables to the parent's full saved commit ID, UUID, and repository-relative record path.

```bash
mutagi experiment start \
  --base "$PARENT_COMMIT" \
  --parent-id "$PARENT_ID" \
  --parent-record "$PARENT_RECORD" \
  --task .mutagi/plans/inherited-exploration.txt \
  --model gpt-6-astra --effort medium
```

Updated shared knowledge is not merged into the parent's saved revision.
If needed, specify the shared revision and note paths to read in the task file.

## Stopping and preservation

The budget includes elapsed time from preparation onward.
With the default settings, the agent stops after 50 minutes and the runner uses the remaining 10 minutes for preservation and registration.
The agent is instructed to fix the implementation revision and finish observations and records before its deadline.
If it finishes early, preservation starts immediately.

On stopping, the runner sends TERM to the run's POSIX process group and tracked descendants, followed by KILL if necessary.
Because Codex shell execution can create separate process groups, parent-child relationships are inspected every 0.2 seconds and PIDs and start times are saved in state.
Remaining descendants are stopped before preservation even after normal completion.
Children wait for ownership to be persisted before starting, preventing an unrecorded command from running if the controller exits during startup.
The explorer is instructed not to background or deliberately detach processes; this is not enforced isolation such as a separate container.
Termination is not guaranteed for arbitrary daemons that start and detach between inspections.

Phases are `preparing -> ready -> executing -> collecting -> saving -> saved -> registered`.
On normal completion, the runner checks the final JSON against the record's ID, base revision, parents, implementation ancestry, and write scope.
Use the template's frontmatter fields, a single bracketed line for `parent_ids`, and a single-quoted UTC timestamp for `started_at`.
The agent may write only to configured `exploration_paths` and its experiment folder; the runner checks the diff again before preservation.
If out-of-scope changes exist, it recovers in-scope work and stops automatic registration.
This is a post-change check, not file-level access control.

For deadline expiry, abnormal agent exit, or incomplete final results, the controller writes `RUNNER-RECOVERY.md`.
It registers `unresolved`, or `blocked` for preparation failure, without assuming successful observation.
The saved branch and revision are registered in the shared catalog, with `human_decision` left as `unreviewed`.
Agent-reported checks remain distinct from the controller's preservation-structure checks.
Independent reproduction and adoption decisions are not automated.

Changes and in-scope untracked files are recovered before committing, so inputs survive a failed Git commit.
Unrelated files, ignored environments, and credentials are not added indiscriminately.
The agent must explicitly include any required reproduction inputs that custom ignore rules would otherwise omit.

## Resume after interruption

```bash
mutagi experiment status <full-UUID>
mutagi experiment resume <same-UUID>
```

`resume` retains the original start time, ID, worktree, and exploration deadline.
Resuming from `executing` or later does not restart the agent.
It stops processes matching saved PIDs and start times, then completes only remaining preservation and registration.
Recorded descendants in separate groups are checked even when their parent has exited.
If the original PID was reused or an orphaned process in the original group has unverifiable ownership, recovery stops with an error rather than guessing what to kill.

Explicit `resume` allows up to 60 seconds for local preservation and registration even after the original deadline, to avoid losing evidence.
This does not extend model exploration time.
Up to 4 seconds of process-stop grace may be added, and Git reference checks and file recovery take additional time.
Preservation commands also have deadlines; if unfinished, recovery artifacts and the current phase are retained.

If state updates are interrupted just after preservation or registration, existing commits and registrations are reconciled.
The same experiment is not committed or registered twice.
Unrelated changes in the shared working tree stop registration rather than being included without permission.
No new run starts while any unregistered run remains.
A repository-level lock also prevents two runners from executing concurrently.
