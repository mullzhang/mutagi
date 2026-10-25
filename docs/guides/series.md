# Run multiple rounds within a budget

[Documentation](../README.md) / Guides

`mutagi series` runs rounds sequentially, starting each new round from the shared revision published by the previous round's knowledge integration.
Exploration and integration each use new CLI sessions.
By default, total counts, model, time limits, and each round's independent/inherited mix are fixed at launch.
See [adaptation and usage limits](adaptation-and-usage.md) for optional `adaptation` and `usage_limits`, and [the macOS service](macos-service.md) for automatic recovery.
The controller does not generate unlimited plans or increase its own budget or permissions.

## Plan and execute

Use a clean shared branch and specify a JSON plan from a file or standard input.
Keep reusable plans under `.mutagi/plans/` in Git.
Create `.mutagi/plans/series-plan.json` before using the commands below.
The same CLI, authentication, dependencies, and permissions as single-experiment and round execution are required.
This example runs two rounds, each with one independent exploration and one integration, within 80 minutes.

```json
{
  "base": "HEAD",
  "model": "gpt-6-astra",
  "effort": "medium",
  "seconds": 4800,
  "round_seconds": 2400,
  "experiment_seconds": 1200,
  "integration_seconds": 1200,
  "reserve_seconds": 180,
  "rounds": [
    {"tasks": [{"prompt": "Choose a new question from existing knowledge and use cases, then test it with a finite comparison.", "parent": null}]},
    {"tasks": [{"prompt": "Read the previous integration assessment and choose a new question by changing the use case or an assumption.", "parent": null}]}
  ]
}
```

```bash
mutagi series start --plan .mutagi/plans/series-plan.json
mutagi series status <series-UUID>
mutagi series resume <same-series-UUID>
```

Use `--base`, `--model`, `--effort`, `--seconds`, `--round-seconds`, `--experiment-seconds`, `--integration-seconds`, and `--reserve-seconds` to override individual plan fields.
Defaults from the selected base commit are applied first, followed by the input plan and explicitly supplied arguments. The input may omit fields supplied by saved defaults or arguments, but the resolved plan must contain every required field.
For example, the [sample series plan](../../examples/minimal-python/.mutagi/plans/series.json) omits `model`:

```bash
mutagi series start --plan .mutagi/plans/series-plan.json --model "$MODEL"
cat .mutagi/plans/series-plan.json |
  mutagi series start --plan - --model "$MODEL"
```

`--plan -` reads JSON from standard input to EOF. Both paths use the same validators and persist the resolved plan without changing an input file.
Model selection is required. When a plan includes usage limits, the rate-card model must still match the model after overrides; incompatible rates are rejected.
Use `--dry-run` to print the resolved plan as JSON without allocating or executing a series. The output is accepted by `--plan`; see [preview behavior](../reference/commands.md#preview-without-starting-work).

`rounds` must be a finite array containing at least two entries.
Each round's `tasks` and `parent` use the [round plan format](rounds.md#plan-and-start).
This format does not support inheritance from experiment branches across rounds.
Code from earlier experiments can be read by full SHA and path, but is not automatically incorporated into the next round's shared implementation.

Budgets use wall-clock time and include preparation, preservation, registration, and knowledge integration.
Round and task deadlines are shortened to the overall remaining time.
No new slot starts when only preservation time remains.
Stop grace periods and preservation work mean these are not strict finish-time guarantees or cost/token caps.

## Repeat a task sequence with CLI arguments

For repeated rounds with the same task pattern, specify `--rounds N` and ordered `--prompt TEXT` or `--inherit INDEX TEXT` options:

```bash
mutagi series start --base HEAD --rounds 2 \
  --prompt "Read the integrated knowledge and test one concrete assumption." \
  --inherit 0 "Test a new boundary made visible by the first result." \
  --model "$MODEL" --effort medium \
  --seconds 4800 --round-seconds 2400 --experiment-seconds 900 \
  --integration-seconds 600 --reserve-seconds 120
```

At least two rounds are required. Each round receives its own copy of the ordered task sequence; parent indices are local to that round.
Knowledge is handed off through the existing verified publication mechanism between rounds.
`--rounds`, `--prompt`, and `--inherit` cannot be combined with `--plan`.
Use JSON plans for different task patterns per round, adaptation policy, or usage limits.
CLI task construction does not enable adaptation or infer prices.

Model, effort, and budget fields may come from explicit [saved defaults](../reference/configuration.md#execution-defaults).
Append `--dry-run` to validate and export the complete plan as JSON before starting.
[Readable status](status.md) shows progress and current child details; `status --json` prints the original saved state.

## Handoff to the next round

After the previous round reaches `completed` with `integration=published`, verify all saved branches, worktrees, and catalog references.
Check tree equality between the saved integration and published revision, as well as the shared branch and HEAD, before using the published full SHA as the next common base.
Include the previous round ID, integration result, and each experiment's saved SHA and observation record in the next round's prompt.

The next explorer reads knowledge and the operational assessment in that revision's catalog, then chooses a question and comparison conditions within the fixed task constraints.
Record what was inherited and which conditions changed.
Without `adaptation`, integrator proposals are reference material and the original prompts and task mix remain fixed.
To authorize changes, see [adaptation planning](adaptation-and-usage.md#planning).

Before publishing later integrations, check that registration references from every earlier round remain intact.
Do not count earlier results toward the current round's planned total.
Publication scope, scientific verification, and human adoption follow the round runner's rules.

## Interrupt and resume

State is saved in `.mutagi/runs/series/<UUID>/state.json`.
See [round state management](rounds.md#interrupt-and-resume) for round and session storage.
Round IDs are created first; assigned revisions, prompts, start times, and deadlines are saved before worktrees are created.

| State | Meaning |
| --- | --- |
| `running` | Running, or waiting to resume the same ID after a failure such as preservation |
| `stopped` | Operator interruption; the active task is preserved and further work waits for explicit resumption |
| `budget_exhausted` | Overall or round budget was insufficient; unstarted rounds and task slots are recorded in their states |
| `integration_deferred` | Integration was saved on its dedicated branch but not published; the next round does not start |
| `completed` | All planned rounds have completed preservation, registration, and knowledge integration |

Resumption retains original IDs and deadlines.
It reconciles saved state and continues after interruptions during round allocation, experiment preservation, or parent checkpoint updates following publication.
Previously started model work is not restarted.
`budget_exhausted` and `integration_deferred` are terminal states with no automatic retry of the same plan.
Preservation failures, moved references, missing registrations, or shared-branch changes cannot be skipped to advance to another round.

An unfinished series blocks new standalone experiments, rounds, and series.
Directly resuming its managed tasks or rounds through standalone commands is also rejected; use `mutagi series resume`.
Execution holds the shared lock.
When launched directly from the CLI, explicitly resume the same ID after supervisor loss.
For persistent automatic recovery, register [mutagi service](macos-service.md#register-and-operate-on-macos) and assign it the target ID.
