# Unattended operation with bounded adaptation

A finite `mutagi series` plan can include next-round adaptation and usage monitoring.
On macOS, registering `mutagi service` with launchd resumes only the explicitly managed series.
Exploration quality and human adoption are not completion conditions for this execution mechanism.

## Planning

Add either or both optional fields below to the [basic series plan](series-runner.md).
This is a configuration example, not an instruction to start execution or a pricing agreement.

```json
{
  "adaptation": {
    "max_tasks": 3,
    "min_seconds": 300,
    "max_round_seconds": 2400,
    "max_experiment_seconds": 1200,
    "max_integration_seconds": 1200
  },
  "usage_limits": {
    "tokens": 1000000,
    "cost": 500,
    "rates": {
      "input": 250,
      "cached_input": 25,
      "output": 1250,
      "unit": "credits",
      "source": "https://learn.chatgpt.com/docs/pricing#token-rates",
      "model": "gpt-6-astra"
    }
  }
}
```

`min_seconds` must exceed the preservation `reserve_seconds`.
The integrator returns `next_round` in its final JSON, including an evidence-based `reason`, the next `tasks`,
`round_seconds`, `experiment_seconds`, and `integration_seconds`.
`tasks` uses the [round plan format](round-runner.md#plan-and-start).
Task count, independent/inherited mix, prompts, and time allocation can change automatically within the authorized bounds.
The final round is not asked to propose a successor.

The controller checks structure and bounds, then fixes an accepted proposal in assignment state as `accepted_proposal`.
An out-of-bounds proposal is neither clamped nor replaced: integration is preserved, while publication and the next round stop.
Round count, overall deadline, model, permissions, and usage thresholds remain unchanged.
Individual deadlines are still shortened when overall time is limited.

## Usage and stopping

A task with `usage_limits` runs as an independent thread/turn on a controller-owned `codex app-server`.
The controller records `thread/tokenUsage/updated` notifications and sends `turn/interrupt` when tokens or estimated cost reach a threshold.
If responses stop, the process supervisor recovers the task at its deadline, then preserves and registers changes, inputs, and logs.
Model-change notifications, unpriceable usage reports, and requests for human input also stop continuation.
See the [official App Server documentation](https://learn.chatgpt.com/docs/app-server) for the protocol.

**These are stop thresholds detected after usage notifications, not strict billing caps.**
Usage accrues before the first notification and can increase between notification and interruption.
This mechanism alone cannot enforce an absolute billing limit.

Specify rates per million tokens.
The example uses credits from the [standard-speed rate card](https://learn.chatgpt.com/docs/pricing#token-rates) checked on 2026-10-11, not yen or dollars.
Match the plan model to the rate-card model, and check your contract and rates before execution.
Count cached input once at its discounted rate; do not count reasoning tokens again when already included in output.
The estimate uses the target thread's reports and specified rates; it does not reconcile account-wide billing, other chats, or additional review work.

Retain each task's `usage.json`, event log, and `app-server-session.json` identifying its server connection.
Interrupted usage is marked `complete=false` because consumption may remain unreported.
Before a new allocation, aggregate all tasks and stop with `usage_exhausted` when a threshold is reached or `usage_unknown` when data is missing or incomplete.
Do not treat unknown usage as zero cost, start another task, or rerun the same model work.

## Register and operate on macOS

```bash
mutagi service install
mutagi service start --plan .mutagi/plans/series-plan.json
mutagi service status
mutagi service pause
```

`install` creates a uniquely named LaunchAgent from the repository's absolute path and registers startup at login and every 30 seconds.
Registration alone does not start exploration. `start` saves the plan and assigns it for execution on the next service invocation.
Use `manage <UUID>` to manage an existing unfinished series.
The target is saved in `.mutagi/runs/service.json`, with logs in `.mutagi/runs/service.log`.
The Python environment, repository, Codex authentication, and required commands must be available.

`pause` is a persistent stop request. After the active task is recovered and preserved, no new work starts, even after service restart.
Use `manage <same-UUID>` only when resuming; the original deadline remains unchanged.
`uninstall` removes only this LaunchAgent. To preserve and stop ongoing work, first use `pause` and confirm the stopped state.

After host shutdown or supervisor loss, recovery uses the same ID, inputs, preservation state, and original deadline.
Already-started model work is not rerun; recovery and preservation continue instead.
If the OS boot identity changed, old PIDs are not signalled.
Persistent preservation or repository errors limit service attempts to three in total, then stop with `needs_attention`.
Execution still requires a powered-on host, a logged-in user, valid authentication, and an available environment.
