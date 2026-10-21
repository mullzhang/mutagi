from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from mutagi import run_experiment as runner
from mutagi.runner_process import recover_process, run_job


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Given an isolated real Git repository with no network preparation."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    for key, value in [("user.name", "Test"), ("user.email", "test@example.invalid")]:
        runner.git(root, "config", key, value)

    (root / ".gitignore").write_text(".mutagi/runs/\n.mutagi/worktrees/\n")
    (root / "apm.lock.yaml").write_text(
        "dependencies: []\nlocal_deployed_file_hashes:\n"
        "  .claude/rules/naedo.md: sha256:" + "0" * 64 + "\n"
    )
    for name in (
        "AGENTS.md",
        "docs/operations.md",
        ".mutagi/prompts/explore.md",
        ".mutagi/prompts/inherit.md",
        "experiments/INDEX.md",
        "knowledge/notes/seed.md",
    ):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Reference\n")

    config = (
        Path(__file__)
        .with_name("project.toml")
        .read_text()
        .replace("PYTHON", sys.executable)
    )
    (root / ".mutagi/config.toml").write_text(config)
    runner.git(root, "add", ".")
    runner.git(root, "commit", "-m", "feat: seed")
    return root


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    """A child process implementing only the real exec invocation contract."""
    binary = tmp_path / "bin"
    binary.mkdir()
    p = binary / "codex"
    p.write_text(f"""#!{sys.executable}
import json,sys,subprocess,pathlib
args=sys.argv[1:]
assert args[:2]==['exec','--json']
assert '--approve-for-me' in args
assert '--sandbox' not in args
out=pathlib.Path(args[args.index('--output-last-message')+1])
s=json.loads((out.parent/'state.json').read_text())
prompt=sys.stdin.read()
assert s['id'] in prompt
print(json.dumps({{"type":"thread.started","thread_id":"00000000-0000-7000-8000-000000000001"}}),flush=True)
d=pathlib.Path('experiments')/(s['started_at'].replace(':','')+'_test-question_'+s['id'])
d.mkdir()
(d/'input.bin').write_bytes(b'\\x00\\xfffixture')
subprocess.run(['git','add',str(d)],check=True)
subprocess.run(['git','commit','-m','feat(experiment): fix input'],check=True,stdout=subprocess.DEVNULL)
implementation=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
record=d/'README.md'
record.write_text('---\\nid: '+s['id']+"\\nstarted_at: '"+s['started_at']+"'\\nbase_commit: "+s['base']+'\\nmode: '+('inherited' if s['parent_ids'] else 'independent')+'\\nparent_ids: '+json.dumps(s['parent_ids'])+'\\nimplementation_commit: '+implementation+'\\nstatus: observed\\nhuman_decision: unreviewed\\n---\\n\\n# Test question\\n')
(d/'necessary.txt').write_text('untracked evidence')
out.write_text(json.dumps({{"title":"Test question","summary":"Finite result","limits":"Synthetic fixture","record":str(record),"implementation_commit":implementation,"checks":"fixture","status":"observed"}}))
print(json.dumps({{"type":"turn.completed"}}),flush=True)
""")
    p.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return p


def create(repo, tmp_path, **kw):
    task = tmp_path / "task.txt"
    task.write_text("Choose one question.")
    with runner.lock(repo):
        path = runner.create(
            repo,
            "HEAD",
            task,
            "gpt-6-astra",
            "medium",
            kw.pop("seconds", 30),
            kw.pop("reserve", 5),
            **kw,
        )

    return runner.Runner(path)


def test_normal_run_preserves_binary_input_and_registers_once(
    repo, tmp_path, fake_codex
):
    # Given a completed experiment with a fixed implementation and untracked evidence.
    job = create(repo, tmp_path)
    # When the full runner and its idempotent recovery execute.
    result = job.advance()
    head = runner.git(repo, "rev-parse", "HEAD")
    job.advance(recovery=True)
    # Then evidence is saved on its dedicated branch and referenced by one catalog commit.
    assert result["phase"] == "registered"
    assert result["reason"] == "completed"
    assert runner.git(repo, "rev-parse", "HEAD") == head
    index = (repo / "experiments/INDEX.md").read_text()
    assert index.count(f"<!-- forest-run:{result['id']} -->") == 1
    record = result["result"]["record"]
    assert runner.git(repo, "show", result["saved_commit"] + ":" + record)
    evidence = str(Path(record).parent / "necessary.txt")
    assert (
        runner.git(repo, "show", result["saved_commit"] + ":" + evidence)
        == "untracked evidence"
    )
    assert not (repo / Path(record).parent).exists()
    assert job.state["thread_id"] is not None
    assert not runner.git(repo, "status", "--porcelain")


@pytest.mark.parametrize("phase", ["collecting", "saving", "saved"])
def test_resume_after_each_preservation_boundary(
    repo, tmp_path, fake_codex, monkeypatch, phase
):
    # Given a returned agent and a controller interrupted at a phase boundary.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    if phase in ("saving", "saved"):
        job.collect()

    if phase == "saved":
        job.preserve()

    started = job.state["started_at"]
    deadline = job.state["deadline"]
    monkeypatch.setattr(
        runner.Runner, "execute", lambda _: pytest.fail("Agent restarted")
    )
    # When recovery is invoked twice.
    resumed = runner.Runner(job.run)
    resumed.advance(recovery=True)
    saved = resumed.state["saved_commit"]
    resumed.advance(recovery=True)
    # Then identity, budget, and saved revision are retained without repeating execution.
    assert resumed.state["saved_commit"] == saved
    assert resumed.state["started_at"] == started
    assert resumed.state["deadline"] == deadline
    assert resumed.state["phase"] == "registered"


def test_git_save_failure_keeps_recovery_and_can_resume(repo, tmp_path, fake_codex):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    # When a commit is rejected, then the archive exists without reporting successful registration.
    with pytest.raises(RuntimeError, match="preservation failed"):
        job.preserve()

    assert job.state["phase"] == "saving"
    assert list((job.run / "recovery").glob("*/hashes.json"))
    assert job.state["id"] not in (repo / "experiments/INDEX.md").read_text()
    hook.unlink()
    # Then later recovery can complete the same run.
    runner.Runner(job.run).advance(recovery=True)
    assert json.loads((job.run / "state.json").read_text())["phase"] == "registered"


def test_registration_commit_failure_does_not_duplicate(
    repo, tmp_path, fake_codex, monkeypatch
):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    job.preserve()
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="preservation failed"):
        job.register()

    hook.unlink()
    job.register()
    job.register()
    assert (repo / "experiments/INDEX.md").read_text().count(
        f"<!-- forest-run:{job.state['id']} -->"
    ) == 1


def test_deadline_stops_child_processes(tmp_path):
    # Given a waiting parent and a child in the same process group that ignores TERM.
    child = tmp_path / "child.pid"
    program = f"""import subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
open({str(child)!r},'w').write(str(p.pid))
time.sleep(60)
"""
    state = {}
    # When the deadline expires, terminate the group rather than only its leader.
    outcome = run_job(
        [sys.executable, "-c", program],
        tmp_path,
        tmp_path / "job.log",
        time.time() + 0.5,
        state,
        lambda: None,
    )
    assert outcome["timed_out"]
    pid = int(child.read_text())
    rows = subprocess.check_output(["ps", "-axo", "pid=,stat="], text=True)
    assert not any(
        int(p) == pid and not status.startswith("Z")
        for p, status in (r.split() for r in rows.splitlines())
    )
    assert state["process"] is None


def test_success_also_stops_background_children(tmp_path):
    state = {}
    program = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])'
    assert (
        run_job(
            [sys.executable, "-c", program],
            tmp_path,
            tmp_path / "job.log",
            time.time() + 10,
            state,
            lambda: None,
        )["exit_code"]
        == 0
    )
    assert state["process"] is None


def test_recover_pid_reuse_refuses_unrelated_process():
    state = {
        "process": {"pid": os.getpid(), "identity": "not this process", "children": []}
    }
    with pytest.raises(RuntimeError, match="identity changed"):
        recover_process(state, lambda: None)


def test_pending_run_prevents_next_experiment(repo, tmp_path):
    create(repo, tmp_path)
    with pytest.raises(RuntimeError, match="unfinished run"):
        create(repo, tmp_path)


def test_scope_violation_is_preserved_but_not_registered(repo, tmp_path, fake_codex):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    (job.worktree / "docs/operations.md").write_text("Unauthorized change")
    with pytest.raises(RuntimeError, match="out-of-scope"):
        job.collect()

    assert job.state["phase"] == "collecting"
    assert list((job.run / "recovery").glob("*/files.tar"))
    assert job.state["id"] not in (repo / "experiments/INDEX.md").read_text()


def test_preserves_development_instructions_and_their_deployment_hash(
    repo, tmp_path, fake_codex
):
    # Given a completed exploration with updated development instructions and generated hashes.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    name = ".apm/instructions/naedo.instructions.md"
    instruction = job.worktree / name
    instruction.parent.mkdir(parents=True)
    instruction.write_text("---\napplyTo: naedo/**\n---\n\nExample rule.\n")
    lock = job.worktree / "apm.lock.yaml"
    lock.write_text(lock.read_text().replace("0" * 64, "1" * 64))

    # When post-exit collection, preservation, and registration run.
    result = job.advance(recovery=True)

    # Then instructions and hashes enter the saved revision without directly changing the shared revision.
    assert result["phase"] == "registered"
    assert "Example rule." in runner.git(
        repo, "show", result["saved_commit"] + ":" + name
    )
    assert "1" * 64 in runner.git(
        repo, "show", result["saved_commit"] + ":apm.lock.yaml"
    )
    assert not (repo / name).exists()


@pytest.mark.parametrize(
    "path",
    [".apm/instructions/naedo-forest.instructions.md", "apm.yml", "apm.lock.yaml"],
)
def test_instruction_updates_do_not_allow_governance_changes(
    repo, tmp_path, fake_codex, path
):
    # Given changed shared instructions, APM settings, or lockfile dependencies during exploration.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    target = job.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("dependencies: [unauthorized]\n")

    # When collecting, then reject the out-of-scope changes without registration.
    with pytest.raises(RuntimeError, match="out-of-scope"):
        job.collect()

    assert job.state["id"] not in (repo / "experiments/INDEX.md").read_text()


def test_expired_execution_recovers_without_model_call(repo, tmp_path, monkeypatch):
    job = create(repo, tmp_path)
    job.prepare()
    job.state.update(phase="executing", exploration_deadline=0, deadline=0)
    job.save()
    monkeypatch.setattr(
        runner.Runner, "execute", lambda _: pytest.fail("Agent restarted")
    )
    job.advance(recovery=True)
    assert job.state["reason"] == "runner_interrupted"
    assert job.state["result"]["status"] == "unresolved"
    assert job.state["phase"] == "registered"


def test_preparation_failure_registered_as_blocked(repo, tmp_path, monkeypatch):
    job = create(repo, tmp_path)
    job.project["preparation"] = [[sys.executable, "-c", "raise SystemExit(1)"]]
    job.save()
    job.advance()
    assert job.state["phase"] == "registered"
    assert job.state["result"]["status"] == "blocked"
    assert job.state["thread_id"] is None


@pytest.mark.parametrize(
    "seconds,reserve", [(0, 0), (float("inf"), 5), (5, 5), (5, -1)]
)
def test_invalid_budget_rejected(repo, tmp_path, seconds, reserve):
    with pytest.raises(ValueError, match="budget"):
        create(repo, tmp_path, seconds=seconds, reserve=reserve)


def test_crash_after_commit_before_state_update_reuses_saved_commit(
    repo, tmp_path, fake_codex, monkeypatch
):
    # Given a successful preservation commit followed by a failed checkpoint write.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    original = job.save

    def fail_after_commit():
        if job.state["phase"] == "saved":
            raise OSError("simulated state checkpoint failure")

        original()

    monkeypatch.setattr(job, "save", fail_after_commit)
    with pytest.raises(OSError):
        job.preserve()

    saved = runner.git(job.worktree, "rev-parse", "HEAD")
    # When resuming from the preservation checkpoint, create no extra commit.
    resumed = runner.Runner(job.run)
    resumed.advance(recovery=True)
    assert resumed.state["saved_commit"] == saved


def test_crash_after_registration_commit_recovers_without_duplicate(
    repo, tmp_path, fake_codex, monkeypatch
):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    job.preserve()
    original = job.save

    def fail_after_registration():
        if job.state["phase"] == "registered":
            raise OSError("simulated registration checkpoint failure")

        original()

    monkeypatch.setattr(job, "save", fail_after_registration)
    with pytest.raises(OSError):
        job.register()

    commit = runner.git(repo, "rev-parse", "HEAD")
    resumed = runner.Runner(job.run)
    resumed.advance(recovery=True)
    assert resumed.state["phase"] == "registered"
    assert runner.git(repo, "rev-parse", "HEAD") == commit


def test_agent_timeout_is_unresolved_and_registered(repo, tmp_path, fake_codex):
    fake_codex.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    job = create(repo, tmp_path, seconds=20, reserve=10)
    job.prepare()
    job.state["exploration_deadline"] = time.time() + 0.2
    job.save()
    job.advance()
    assert job.state["reason"] == "time_limit"
    assert job.state["phase"] == "registered"
    assert job.state["result"]["status"] == "unresolved"


def test_repository_lock_prevents_parallel_launch(repo):
    with runner.lock(repo):
        with pytest.raises(RuntimeError, match="another runner"):
            with runner.lock(repo):
                pytest.fail("Concurrent controller acquired lock")


def test_interrupted_runner_recovers_its_live_process(repo, tmp_path):
    # Given an interrupted controller with saved ownership of a running process.
    from mutagi.runner_process import identity

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    state = {
        "process": {
            "pid": process.pid,
            "identity": identity(process.pid),
            "children": [],
        }
    }
    try:
        recover_process(state, lambda: None)
        process.wait(timeout=2)
        assert state["process"] is None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, 9)
            process.wait()


def test_invalid_result_cannot_be_registered_as_observed(repo, tmp_path, fake_codex):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    result = json.loads((job.run / "result.json").read_text())
    result["record"] = "../outside.md"
    (job.run / "result.json").write_text(json.dumps(result))
    job.advance()
    assert job.state["reason"] == "invalid_result"
    assert job.state["result"]["status"] == "unresolved"


def test_inherited_run_requires_record_at_fixed_base(repo, tmp_path):
    from uuid import uuid4

    parent = str(uuid4())
    record = repo / "experiments/parent.md"
    record.write_text(f"---\nid: {parent}\n---\n")
    runner.git(repo, "add", ".")
    runner.git(repo, "commit", "-m", "docs: parent record")
    job = create(
        repo, tmp_path, parent_id=parent, parent_record="experiments/parent.md"
    )
    assert job.state["parent_ids"] == [parent]
    assert job.state["base"] == runner.git(repo, "rev-parse", "HEAD")


def test_stdin_backpressure_cannot_extend_deadline(tmp_path):
    # Given a child that does not read a prompt larger than pipe capacity.
    state = {"process": None}
    started = time.monotonic()
    # When the job deadline expires during input transfer.
    outcome = run_job(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        tmp_path,
        tmp_path / "job.log",
        time.time() + 0.2,
        state,
        lambda: None,
        stdin=io.StringIO("x" * 2_000_000),
    )
    # Then input transfer is also bounded by the deadline and managed processes stop.
    assert outcome["timed_out"]
    assert time.monotonic() - started < 6
    assert state["process"] is None


def test_moved_saved_branch_prevents_registration(repo, tmp_path, fake_codex):
    # Given a preserved result whose branch was moved by another actor.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    job.preserve()
    runner.git(job.worktree, "commit", "--allow-empty", "-m", "test: change branch")
    # When registration is attempted, do not accept the changed reference as valid.
    with pytest.raises(RuntimeError, match="branch moved"):
        job.register()

    assert job.state["id"] not in (repo / "experiments/INDEX.md").read_text()


@pytest.mark.parametrize("timeout", [False, True])
def test_child_in_its_own_session_is_stopped(tmp_path, timeout):
    # Given a child in a new session, as used by Codex shell commands.
    from mutagi.runner_process import processes

    child_file = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
        f"pathlib.Path({str(child_file)!r}).write_text(str(p.pid)); "
        f"time.sleep({60 if timeout else 0.7})"
    )
    state = {"process": None}
    saved_children = []
    # When the parent completes or reaches its deadline.
    result = run_job(
        [sys.executable, "-c", script],
        tmp_path,
        tmp_path / "process.log",
        time.time() + (1.0 if timeout else 5.0),
        state,
        lambda: saved_children.extend(
            state["process"]["children"] if state["process"] else []
        ),
    )
    # Then the separate process group is durably tracked and stopped.
    child = int(child_file.read_text())
    assert result["timed_out"] == timeout
    assert any(item["pid"] == child for item in saved_children)
    assert child not in processes()


def test_recovery_stops_recorded_child_after_parent_exited(tmp_path):
    # Given saved ownership of a separate-session child after the controller's parent exits.
    from mutagi.runner_process import identity

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    parent = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    parent.wait()
    state = {
        "process": {
            "pid": parent.pid,
            "identity": "exited",
            "children": [{"pid": child.pid, "identity": identity(child.pid)}],
        }
    }
    try:
        # When recovery uses saved identity to operate across groups.
        recover_process(state, lambda: None)
        child.wait(timeout=2)
        assert state["process"] is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_identity_failure_reaps_unreleased_gate_and_preserves_error(
    tmp_path, monkeypatch
):
    from mutagi import runner_process

    # Given process inspection denied before the command receives permission to execute.
    children = []
    original_popen = runner_process.subprocess.Popen

    def capture_child(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        children.append(process)
        return process

    def denied_identity(_):
        raise PermissionError("process inspection denied")

    monkeypatch.setattr(runner_process.subprocess, "Popen", capture_child)
    monkeypatch.setattr(runner_process, "identity", denied_identity)
    marker = tmp_path / "must-not-exist"
    state = {"process": None}
    # When ownership cannot be recorded, retain the original failure.
    with pytest.raises(PermissionError, match="process inspection denied"):
        run_job(
            [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
            tmp_path,
            tmp_path / "job.log",
            time.time() + 10,
            state,
            lambda: None,
        )

    # Then recover the start gate, leaving neither a command nor stale ownership.
    assert len(children) == 1
    assert children[0].returncode == 125
    assert not marker.exists()
    assert state["process"] is None


@pytest.fixture
def app_ready(repo, tmp_path, fake_codex):
    import hashlib

    job = create(repo, tmp_path)
    job.prepare()
    job.execute()  # Generate the fixture only; do not execute the handoff itself.
    (job.run / "events.jsonl").unlink()
    log = job.directory() / "worker.log"
    log.write_text("App handoff fixture\n")
    (repo / ".git/info/exclude").write_text("*.log\n")
    paths = set(job.changed()) | {log.relative_to(job.worktree).as_posix()}
    receipt = {
        "id": job.state["id"],
        "thread_id": "app-test-chat",
        "files": {
            p: hashlib.sha256((job.worktree / p).read_bytes()).hexdigest()
            for p in paths
        },
    }
    runner.atomic_json(job.run / "receipt.json", receipt)
    job.state.update(phase="awaiting_app", thread_id=receipt["thread_id"], reason=None)
    job.save()
    observation = {
        "thread_id": receipt["thread_id"],
        "thread_status": "idle",
        "turn_status": "completed",
        "turn_id": "app-test-turn",
        "observed_at": runner.utc(time.time()),
    }
    return job, receipt, observation


def test_app_handoff_preserves_ignored_log_and_registers_once(app_ready, monkeypatch):
    from mutagi.app_handoff import accept

    # Given App output with required ignored logs and a turn observed to completion.
    job, receipt, observation = app_ready
    monkeypatch.setattr(
        runner.Runner, "execute", lambda _: pytest.fail("Agent restarted")
    )
    # When the controller accepts the output twice.
    first = accept(job.run, observation)
    second = accept(job.run, observation)
    # Then ignored logs are committed exactly, with one registration and no new agent.
    assert first["saved_commit"] == second["saved_commit"]
    assert first["registry_commit"] == second["registry_commit"]
    assert first["phase"] == "registered"
    for path in receipt["files"]:
        assert (
            subprocess.check_output(
                ["git", "-C", str(job.repo), "show", first["saved_commit"] + ":" + path]
            )
            == (job.worktree / path).read_bytes()
        )

    record = job.directory().relative_to(job.worktree) / "_runner/app-handoff.json"
    stored = json.loads(
        runner.git(job.repo, "show", first["saved_commit"] + ":" + str(record))
    )
    assert stored["receipt"] == receipt
    assert stored["app_observation"] == observation


@pytest.mark.parametrize("defect", ["active", "omitted_log", "modified_input"])
def test_app_handoff_rejects_unready_or_incomplete_evidence(app_ready, defect):
    from mutagi.app_handoff import accept

    # Given a nonterminal turn, missing ignored evidence, or altered evidence contents.
    job, receipt, observation = app_ready
    if defect == "active":
        observation["thread_status"] = "running"
    elif defect == "omitted_log":
        receipt["files"] = {
            p: h for p, h in receipt["files"].items() if not p.endswith(".log")
        }
        runner.atomic_json(job.run / "receipt.json", receipt)
    else:
        (job.directory() / "input.bin").write_bytes(b"changed")

    # When handoff is attempted, reject it before preservation and registration.
    with pytest.raises(ValueError):
        accept(job.run, observation)

    # Then remain awaiting the App without registering in the shared catalog.
    assert runner.Runner(job.run).state["phase"] == "awaiting_app"
    assert job.state["id"] not in (job.repo / "experiments/INDEX.md").read_text()


def test_app_handoff_resumes_failed_commit_with_frozen_receipt(app_ready):
    from mutagi.app_handoff import accept

    # Given a rejected local preservation commit for a completed App turn.
    job, receipt, observation = app_ready
    hook = job.repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="preservation failed"):
        accept(job.run, observation)

    assert runner.Runner(job.run).state["phase"] == "saving"
    hook.unlink()
    # When the declared inventory changes, reject recovery before registration.
    changed = dict(receipt, thread_id="another-chat")
    runner.atomic_json(job.run / "receipt.json", changed)
    with pytest.raises(ValueError, match="identity"):
        accept(job.run, observation)

    runner.atomic_json(job.run / "receipt.json", receipt)
    # Then restoring the accepted receipt allows recovery with the same ID.
    result = accept(job.run, observation)
    assert result["id"] == receipt["id"]
    assert result["phase"] == "registered"


@pytest.mark.parametrize("boundary", ["collecting", "saving", "saved", "registered"])
def test_app_handoff_recovers_launcher_death_before_checkpoint(
    app_ready, tmp_path, monkeypatch, boundary
):
    from mutagi.app_handoff import accept

    # Given completed App evidence and a controller exiting before writing its checkpoint.
    job, receipt, observation = app_ready
    assigned = {key: job.state[key] for key in ("id", "started_at", "deadline")}
    observation_path = tmp_path / "observation.json"
    runner.atomic_json(observation_path, observation)
    program = """import json, os, signal, sys
from pathlib import Path
from mutagi import run_experiment as runner
from mutagi.app_handoff import accept
original = runner.Runner.save
def checkpoint(self):
    if self.state['phase'] == sys.argv[3]:
        os.kill(os.getpid(), signal.SIGKILL)
    original(self)
runner.Runner.save = checkpoint
accept(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))
"""
    process = subprocess.run(
        [sys.executable, "-c", program, str(job.run), str(observation_path), boundary],
        cwd=Path(runner.__file__).parent,
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == -9, process.stderr.decode()
    monkeypatch.setattr(
        runner.Runner, "execute", lambda _: pytest.fail("Agent restarted")
    )

    # When a new controller checks unfinished runs, it does not start another.
    with pytest.raises(
        RuntimeError, match="unfinished run|clean the working tree"
    ):
        create(job.repo, tmp_path)

    recovered = accept(job.run, observation)
    repeated = accept(job.run, observation)

    # Then recovery preserves identity and all contents, with exactly one registration.
    assert {key: recovered[key] for key in assigned} == assigned
    assert recovered["phase"] == "registered"
    assert recovered["saved_commit"] == repeated["saved_commit"]
    assert recovered["registry_commit"] == repeated["registry_commit"]
    assert (job.repo / "experiments/INDEX.md").read_text().count(
        f"<!-- forest-run:{assigned['id']} -->"
    ) == 1
    for path in receipt["files"]:
        assert (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(job.repo),
                    "show",
                    recovered["saved_commit"] + ":" + path,
                ]
            )
            == (job.worktree / path).read_bytes()
        )

    assert not runner.git(job.repo, "status", "--porcelain")
    assert not runner.git(job.worktree, "status", "--porcelain")
