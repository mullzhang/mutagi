# Inspect execution and find the next action

[Documentation](../README.md) / Guides

Status commands read existing records without acquiring execution locks or changing saved state.
The default display separates the recorded phase, current process observations, reported results, preservation, and human adoption.

```bash
mutagi experiment status <experiment-UUID>
mutagi round status <round-UUID>
mutagi series status <series-UUID>
mutagi service status
mutagi job status /absolute/path/to/job-record
```

Use `--repo /path/to/project` before the action for experiment, round, series, and service commands.
`job` takes its record directory directly.

| Display | Meaning |
| --- | --- |
| Recorded phase or stage | The last persisted controller checkpoint; a running checkpoint does not establish that its controller is alive |
| Observed now | Whether recorded managed processes are present with the same process identities and system boot; process presence does not establish solver or model progress |
| Recorded progress | Completed checkpoints and remaining task slots or rounds; current child details can be ahead of the parent's checkpoint after interruption |
| Reported observation | The explorer's `observed`, `refuted`, `unresolved`, or `blocked` result, distinct from successful preservation |
| Saved revision and record command | The persisted result revision and a command to read its record; this display does not revalidate the Git objects |
| Knowledge publication | Whether integration was published to the shared branch or deferred; it does not adopt an experiment's implementation |
| Human adoption | An independent decision recorded in the catalog or harvesting notes; execution does not track or infer this decision |

The default display includes the phase, progress, current child checkpoint where available, reported observation, preservation, and last recorded error.
Use `--details` for the deadline, model, reasoning effort, state file, child history, and full record references.
Reported summaries and limitations appear as literal excerpts by default; details retain the full text.
Time until the deadline is not a completion estimate, and an expired deadline does not prove all descendants have stopped.

For incomplete work, the suggested resume command follows ownership: a series-owned experiment resumes through its series, and a standalone round's experiment resumes through that round.
If a controller or service is still running, follow its status before attempting manual recovery.
The repository lock rejects concurrent controllers.
Completed or otherwise terminal plans direct you to inspect preserved results; they do not imply that every planned task ran or produced a positive observation.

## Machine-readable state

Append `--json` to print the original saved state JSON instead of the human-readable display:

```bash
mutagi round status <round-UUID> --json
mutagi job status /absolute/path/to/job-record --json
```

This output contains no additional live process measurements and performs no process inspection.
For `job`, `supervisor_alive` is a live observation in the human display, not a field added to the saved JSON.
An unassigned service returns `{"phase":"unassigned"}`.
Scripts written for the earlier JSON-only status output should add `--json`.

Use `mutagi harvest list` to find saved results and `mutagi harvest show <experiment-reference>` to inspect one result. Add `--details` for separately recorded adoption notes.
See [harvesting](harvesting.md) for trials, generated prompts, and decision notes.
