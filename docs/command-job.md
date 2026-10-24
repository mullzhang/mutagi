# Manage one command from the App

`mutagi job` runs one command under a supervisor separate from the App chat.
It does not launch Codex CLI. It uses the Python standard library and POSIX processes and file locks.
It uses `runner_process.py` to track the command and descendants, recovering them on deadline expiry or an explicit stop request.

## Start and inspect

Specify the working directory and record directory explicitly.
Use a persistent, experiment-specific record directory; do not reuse an existing directory.
Arguments after `--command` are an executable and argument list, not a shell string.

```bash
mutagi job start /absolute/path/to/job-record \
  --cwd /absolute/path/to/experiment --seconds 60 \
  --command python3 experiment.py
mutagi job status /absolute/path/to/job-record
```

`start` launches the supervisor and returns. Successful startup does not mean the command has finished.
The supervisor PID and start time are saved before execution is permitted, and command ownership is also persisted before execution.
The supervisor detaches from the launcher's standard streams and writes to `supervisor.log`.

| Artifact | Contents |
| --- | --- |
| `state.json` | UUID, worktree, command, deadline, supervisor and process ownership, state, and exit result |
| `command.log` | Command standard output and standard error |
| `supervisor.log` | Supervisor errors |
| `stop-requested` | Persistent explicit stop request; repeated calls do not cancel it |
| `lock` | Lock preventing concurrent supervision and recovery of the same record |

`status` displays the saved stage, command and log paths, recorded exit result, current process observations, and next action.
Use `status --json` to read the original saved state without live process measurements; see [status output](status.md).
Confirm completion with `stage=finished` and `process=null`; distinguish `result.exit_code`, `timed_out`, and `cancelled`.
`finished` does not imply success: the exit code may be nonzero.
An absent supervisor alone does not establish that the command has stopped.

## Stop and recover

```bash
mutagi job stop /absolute/path/to/job-record
mutagi job status /absolute/path/to/job-record
```

`stop` only saves a request; it does not wait for completion. The supervisor reads the request and terminates the target.
The App's stop button does not automatically send this request.
If the chat is interrupted while the supervisor remains alive, the command is recovered at its original deadline.
To stop earlier, the controller must issue `stop` and check completion.

If the supervisor itself exits abnormally, run:

```bash
mutagi job recover /absolute/path/to/job-record
```

`recover` terminates remaining targets and sets `stage=recovered`. It retains the original ID, deadline, and logs without rerunning the command.
It rejects races with a live supervisor, and repeated recovery does not rewrite completed records.
Recovery after SIGKILL or terminal loss is not automatic; the controller must detect the failure and invoke it.
After confirming completion or recovery, preserve inputs, results, and changes, then perform Git preservation and catalog registration separately.

## Scope and limitations

- Only commands launched through this interface are managed. App edits, directly launched commands, and model execution time are outside its scope.
- Each supervisor manages one command; it does not add a persistent service or control subsequent explorations.
- Locking is per record directory. The controller must prevent jobs with different record directories from concurrently modifying the same worktree.
- Descendants are tracked periodically. Arbitrary daemons that detach before inspection are not isolated.
- PIDs and start times are checked. Mismatched or unverifiable ownership does not authorize stopping unrelated processes.
- Stop grace periods, OS scheduling, clock changes, and file-write failures prevent strict timing guarantees.
- Unattended operation is not guaranteed through supervisor or storage failure. Recover and preserve unfinished work before starting another exploration.
