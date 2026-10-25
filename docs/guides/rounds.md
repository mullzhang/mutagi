# Run one round sequentially

[Documentation](../README.md) / Guides

`mutagi round` invokes the single-experiment runner sequentially, then integrates knowledge in a separate Codex CLI session.
Independent explorations use a common base revision; inherited explorations use a parent saved earlier in the same round.
It does not automatically start another round after finishing.
Use the [series runner](series.md) to continue across rounds within an overall budget.
It does not operate App chats.

## Plan and start

Use the same environment, authentication, and permissions as single-experiment execution.
Provide the plan as JSON from a file or standard input and leave the shared branch clean.
For reuse, save it in `.mutagi/plans/round-plan.json` and commit it.
This example runs one independent exploration and one inheriting its results.
`parent` is the zero-based index of an earlier item in the same array, or `null` for independent exploration.

```json
{
  "base": "HEAD",
  "model": "gpt-6-astra",
  "effort": "medium",
  "seconds": 10800,
  "experiment_seconds": 3600,
  "integration_seconds": 3600,
  "reserve_seconds": 600,
  "tasks": [
    {"prompt": "Consider up to three distinct questions from a concrete use case and test one with a finite comparison.", "parent": null},
    {"prompt": "Choose a question newly testable because of the parent's results. Do not make refinement the sole objective.", "parent": 0}
  ]
}
```

```bash
mutagi round start --plan .mutagi/plans/round-plan.json
mutagi round status <round-UUID>
mutagi round resume <same-round-UUID>
```

The CLI accepts `--base`, `--model`, `--effort`, `--seconds`, `--experiment-seconds`, `--integration-seconds`, and `--reserve-seconds` as explicit plan overrides.
Saved execution defaults from the selected base commit are applied first, followed by the input plan and explicitly supplied arguments. A file may omit fields supplied through those defaults or arguments; the complete plan must pass the same validation before a round is allocated.
No model is chosen automatically. The input file is unchanged, and the resolved plan is saved in round state.
The [sample round plan](../../examples/minimal-python/.mutagi/plans/round.json) omits the model for this purpose.

```bash
mutagi round start --plan .mutagi/plans/round-plan.json --model "$MODEL"
cat .mutagi/plans/round-plan.json |
  mutagi round start --plan - --model "$MODEL"
```

`--plan -` reads JSON from standard input to EOF, so another program can generate a plan without an intermediate file.
Use `--dry-run` to print the resolved plan as JSON without allocating or executing a round. The output is accepted by `--plan`; input files are unchanged. See [preview behavior](../reference/commands.md#preview-without-starting-work).

At launch, the base is resolved to a full SHA and the plan and round deadline are fixed.
Exploration and integration IDs are recorded in advance; each execution clock starts at its allocation.
Each slot's limit is shortened to the round's remaining time. A slot does not start if even preservation time is unavailable.
Preparation, preservation, registration, and knowledge integration count toward the round budget.
Stop grace periods and Git checks mean the wall-clock finish time is not strictly guaranteed.

## Construct tasks with CLI arguments

A simple round needs no plan file:

```bash
mutagi round start --base HEAD \
  --prompt "Compare two counting definitions on a finite input." \
  --inherit 0 "Use the first result to test a newly identified boundary." \
  --model "$MODEL" --effort medium \
  --seconds 2400 --experiment-seconds 900 \
  --integration-seconds 600 --reserve-seconds 120
```

`--prompt TEXT` adds an independent task. `--inherit INDEX TEXT` adds a task whose parent is an earlier task in the same sequence, using a zero-based index.
The order of repeated options is preserved. Self references, forward references, negative indices, and empty instructions are rejected.
Task-construction options cannot be combined with `--plan`; scalar settings can be used with either input method.
The resolved plan has the same `tasks` schema and follows the same runtime as a JSON plan.
Explicit [saved execution defaults](../reference/configuration.md#execution-defaults) can supply model, effort, and budget fields.
Append `--dry-run` to inspect or export the plan before execution.

[Status output](status.md) shows recorded progress, current child details, result locations, and recovery instructions. Add `--json` for the original saved state.

## Check preservation and inheritance

Before starting the next task, verify registration state, saved branch SHAs, worktree changes, and shared catalog IDs and fixed references for all preceding tasks.
Preservation failure, missing registration, out-of-scope changes, moved branches, or missing catalog entries stop execution before another model starts.
Unresolved observations can inform later work once their exit reasons and evidence are preserved.
Do not treat unresolved results as successful observations.

The inherited code revision and parent record come from the parent's saved result.
Do not merge shared implementation or other experiments into the parent's experiment branch.
Each exploration uses the same scope, stopping, preservation, and registration rules as the [single-experiment runner](experiments.md).

## Integrate knowledge in a separate session

After all explorations are preserved and registered, fix the current shared revision and prepare a dedicated worktree and integration branch.
The integrator reads all target revisions, performs necessary reproductions, updates knowledge, and records an operational assessment in the catalog.
Writes are limited to configured `integration_paths`.
Do not delegate subagents, further exploration, or operational changes to the integrator.

Publish the saved integration tree to the shared branch only after normal completion and the required result JSON are confirmed.
Reject out-of-scope paths, symbolic links, missing catalog references to target experiments, and unexpected shared-branch changes.
Check tree equality before and after publication without importing experiment implementation.
Interrupted integration is preserved on its dedicated branch and in recovery storage, but not automatically published; record `integration=deferred`.

Automatic checks verify preservation structure and references, not scientific correctness or human adoption.
Read the catalog for the integrator's reproduction scope. Human adoption remains `unreviewed`.
Only knowledge is published to the shared revision; this does not adopt experiment implementations, push, or port changes upstream.

## Assess the round and choose new questions

During integration, check the following and record supporting experiment IDs and saved commits in the catalog.

| Aspect | Check |
| --- | --- |
| Distinct questions | Compare use cases, tested assumptions, and findings. A different name or implementation alone is not a new question. |
| Progress to observation | Identify whether work reached implementation, comparison, or a counterexample. Distinguish negative, unresolved, interrupted, and unstarted work. |
| Changed understanding and inheritance | Identify evidence that changed understanding and what the parent made newly testable. Do not count the same evidence as independent confirmation. |
| Access to results | Check that the catalog leads to findings, reusable parts, applicability conditions, saved revisions, and reproduction steps. Reproduce where needed and state the checked scope. |
| Costs and drift | Check measured time and usage, stops and retries, and effort spent on general maintenance. Leave unmeasured values unknown. |

Include denominators and differences in model, budget, and base revision.
One round does not demonstrate general improvements in creativity.
Do not create an aggregate score that maximizes code, test, or documentation volume, or harvesting rates.
Retain competing hypotheses with traceable sources, experiments, and applicability conditions rather than forcing consensus.
Record reasons when integration is deferred.

If inheritance produces only refinement, require a question newly testable because of the parent.
If proposals grow without observations, reduce experiment size. If administration dominates, run a concrete experiment before adding more management features.
If reproduction or reading is difficult, improve saved revisions, dependencies, reproduction steps, or the catalog.

When questions become too similar, try one of these changes. Check existing capabilities before defining a concrete comparison.

| Change | Question to test |
| --- | --- |
| Use case | What information or actions are missing for another user or situation? |
| Assumption | What still works, and which guarantees disappear, when one assumed condition is removed? |
| Size | Can code, configuration, or steps be reduced while retaining the capability needed for the target use? |
| Purpose | What becomes apparent when a saved result is used for another purpose? |

## Interrupt and resume

Round state is saved in `.mutagi/runs/rounds/<UUID>/state.json`.
Exploration and integration sessions use the [single-run storage locations](experiments.md#start-an-independent-exploration).

| Round state | Meaning |
| --- | --- |
| `running` | Running, or waiting to resume after failure; also inspect each task's preservation state |
| `stopped` | Operator interruption stopped further tasks after preserving the active task |
| `budget_exhausted` | Insufficient remaining time; unstarted IDs are recorded in `not_started` |
| `completed` | All slots were processed and the round closed; check the separate `integration` field for publication |

`resume` retains original IDs, start times, allocated inputs, and deadlines.
Recovery of previously active tasks follows [single-run recovery](experiments.md#resume-after-interruption).
Saved tasks are checked against Git and the catalog; only the next unallocated task starts within the remaining budget.
A `budget_exhausted` plan cannot be resumed by extending its original time limit.

SIGTERM or Ctrl-C attempts to preserve the active task where possible and does not start another.
If SIGKILL or host shutdown removes the supervisor, there is no persistent automatic recovery for a standalone round.
Explicitly invoke `resume` for the same round.

The repository lock prevents concurrent runners during execution.
After interruption, recover the original round before starting another.
Do not use single-experiment `resume` directly on a task managed by a round.
