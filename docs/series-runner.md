# Run multiple rounds within a budget

`mutagi series` runs rounds sequentially, starting each new round from the shared revision published by the previous round's knowledge integration.
Exploration and integration each use new CLI sessions.
By default, total counts, model, time limits, and each round's independent/inherited mix are fixed at launch.
See [unattended operation](unattended.md) for optional `adaptation`, `usage_limits`, and the persistent service.
The controller does not generate unlimited plans or increase its own budget or permissions.

## Plan and execute

Use a clean shared branch and specify a JSON plan.
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

`rounds` must be a finite array containing at least two entries.
Each round's `tasks` and `parent` use the [round plan format](round-runner.md#plan-and-start).
This format does not support inheritance from experiment branches across rounds.
Code from earlier experiments can be read by full SHA and path, but is not automatically incorporated into the next round's shared implementation.

Budgets use wall-clock time and include preparation, preservation, registration, and knowledge integration.
Round and task deadlines are shortened to the overall remaining time.
No new slot starts when only preservation time remains.
Stop grace periods and preservation work mean these are not strict finish-time guarantees or cost/token caps.

## Handoff to the next round

After the previous round reaches `completed` with `integration=published`, verify all saved branches, worktrees, and catalog references.
Check tree equality between the saved integration and published revision, as well as the shared branch and HEAD, before using the published full SHA as the next common base.
Include the previous round ID, integration result, and each experiment's saved SHA and observation record in the next round's prompt.

The next explorer reads knowledge and the operational assessment in that revision's catalog, then chooses a question and comparison conditions within the fixed task constraints.
Record what was inherited and which conditions changed.
Without `adaptation`, integrator proposals are reference material and the original prompts and task mix remain fixed.
To authorize changes, see [adaptation planning](unattended.md#planning).

Before publishing later integrations, check that registration references from every earlier round remain intact.
Do not count earlier results toward the current round's planned total.
Publication scope, scientific verification, and human adoption follow the round runner's rules.

## Interrupt and resume

State is saved in `.mutagi/runs/series/<UUID>/state.json`.
See [round state management](round-runner.md#interrupt-and-resume) for round and session storage.
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
For persistent automatic recovery, register [mutagi service](unattended.md#register-and-operate-on-macos) and assign it the target ID.
