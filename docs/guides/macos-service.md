# Manage automatic recovery on macOS

[Documentation](../README.md) / Guides

`mutagi service` uses a project-specific macOS LaunchAgent to resume an explicitly managed series after process restart or login.
`launchd` is the macOS mechanism that invokes the registered command.
Prepare a [series plan](series.md) before assigning work to the service.

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
The agent invokes `python -m mutagi service --repo REPOSITORY tick` using the Python executable recorded at installation.
Before updating a package with a registered agent, follow the [pause, uninstall, update, and reinstall procedure](#update-a-registered-macos-service).

`pause` is a persistent stop request. After the active task is recovered and preserved, no new work starts, even after service restart.
Use `manage <same-UUID>` only when resuming; the original deadline remains unchanged.
`uninstall` removes only this LaunchAgent. To preserve and stop ongoing work, first use `pause` and confirm the stopped state.

After host shutdown or supervisor loss, recovery uses the same ID, inputs, preservation state, and original deadline.
Already-started model work is not rerun; recovery and preservation continue instead.
If the OS boot identity changed, old PIDs are not signalled.
Persistent preservation or repository errors limit service attempts to three in total, then stop with `needs_attention`.
Execution still requires a powered-on host, a logged-in user, valid authentication, and an available environment.

## Service inputs and status

`mutagi service start` accepts the same plan files, standard-input plans, ordered task arguments, saved defaults, and explicit scalar overrides as `mutagi series start`.
`service start --dry-run` prints the resolved series plan without registering service ownership or allocating state.
Actual `service start` records a managed series; execution still depends on the installed service or an explicit service tick.

`mutagi service status` displays the service checkpoint and the managed series' progress. A recorded service state does not establish that launchd is running.
Add `--json` to get the original service checkpoint. See [status and recovery](status.md).

## Update a registered macOS service

Pause each managed project and wait until the active work has been preserved and the series has stopped or finished:

```bash
mutagi service --repo /absolute/path/to/project pause
mutagi service --repo /absolute/path/to/project status
```

Uninstall its LaunchAgent with the currently installed package, update the package, then install the agent again.
For an installation tracking a branch:

```bash
mutagi service --repo /absolute/path/to/project uninstall
uv tool upgrade mutagi
mutagi service --repo /absolute/path/to/project install
```

For a fixed release, use the install command for the selected tag instead of the branch upgrade command.
If several projects use the same Python installation, uninstall each agent before updating and reinstall each afterward.

The saved launchd command is `python -m mutagi service --repo REPOSITORY tick`.
Agents created before the package reorganization refer to `mutagi.series_service` and must be reinstalled.
The installer refuses to overwrite a differing existing agent, so repeating `install` alone does not perform this update.

Run IDs, checkpoints, and original deadlines remain unchanged.
Registration does not clear a pause; use `mutagi service --repo REPOSITORY manage UUID` only when you intend to resume that unfinished series.
