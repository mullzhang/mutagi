from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pytest

from mutagi import run_experiment as runner
from mutagi import run_round as rounds

pytest_plugins = ["test_runner"]


@pytest.fixture
def round_environment(repo, fake_codex, tmp_path):
    for name in (".mutagi/prompts/integrate.md", "knowledge/README.md"):
        (repo / name).write_text("# Integration reference\n")

    runner.git(repo, "add", ".")
    runner.git(repo, "commit", "-m", "docs: add integration references")
    original = fake_codex.read_text()
    calls = tmp_path / "sessions.jsonl"
    fake_codex.write_text(f"""#!{sys.executable}
import json,sys,pathlib,subprocess
args=sys.argv[1:]
out=pathlib.Path(args[args.index('--output-last-message')+1])
s=json.loads((out.parent/'state.json').read_text())
with open({str(calls)!r},'a') as f:f.write(s['id']+'\\n')
if s['branch'].startswith('codex/integrate-'):
 prompt=sys.stdin.read()
 assert s['id'] in prompt
 sources=json.loads((out.parent/'task.txt').read_text())['sources']
 assert sources
 for source in sources:
  subprocess.run(['git','cat-file','-e',source['saved_commit']+':'+source['result']['record']],check=True)
 print(json.dumps({{'type':'thread.started','thread_id':s['id']}}),flush=True)
 pathlib.Path('knowledge/notes/integrated.md').write_text('# Integrated finite observations '+s['id']+'\\n')
 out.write_text(json.dumps({{'summary':'Two saved sources','limits':'Synthetic fixture','checks':'Fixed source paths'}}))
 print(json.dumps({{'type':'turn.completed'}}),flush=True)
else:
 exec(compile({original!r},'fixture','exec'))
""")
    plan = {
        "base": "HEAD",
        "model": "gpt-6-astra",
        "effort": "medium",
        "seconds": 600,
        "experiment_seconds": 60,
        "integration_seconds": 60,
        "reserve_seconds": 10,
        "tasks": [
            {"prompt": "Choose a finite question.", "parent": None},
            {"prompt": "Use the parent to ask a new question.", "parent": 0},
        ],
    }
    return repo, plan, calls


@pytest.fixture
def round_case(round_environment):
    repo, plan, calls = round_environment
    return repo, rounds.create_round(repo, plan), calls


def completed_jobs(repo, state):
    return [
        json.loads((repo / ".mutagi/runs" / item["id"] / "state.json").read_text())
        for item in state["jobs"]
    ]


def test_round_runs_inheritance_and_separate_integration_once(round_case):
    # Given two planned explorations and a separately allocated integration session.
    repo, directory, calls = round_case
    initial = rounds.Round(directory).state
    # When the round runs and is resumed after completion.
    result = rounds.Round(directory).advance()
    head = runner.git(repo, "rev-parse", "HEAD")
    repeated = rounds.Round(directory).advance(recovery=True)
    # Then all three sessions run once and inheritance starts from the saved parent.
    jobs = completed_jobs(repo, result)
    assert result["phase"] == repeated["phase"] == "completed"
    assert calls.read_text().splitlines() == [item["id"] for item in initial["jobs"]]
    assert jobs[1]["base"] == jobs[0]["saved_commit"]
    assert jobs[1]["parent_ids"] == [jobs[0]["id"]]
    assert jobs[2]["published"]
    assert runner.git(repo, "rev-parse", "HEAD") == head
    assert (repo / "knowledge/notes/integrated.md").exists()
    assert not list((repo / "experiments").glob("*_test-question_*"))
    assert all(job["deadline"] <= result["deadline"] for job in jobs)


def test_round_recovers_stale_parent_checkpoint_without_relaunch(
    round_case, monkeypatch
):
    # Given a lost parent checkpoint after child preservation and registration completed.
    repo, directory, calls = round_case
    original = rounds.Round.save

    def fail_after_child(self):
        if self.state["next"] == 1:
            raise OSError("round checkpoint lost")

        original(self)

    with monkeypatch.context() as m:
        m.setattr(rounds.Round, "save", fail_after_child)
        with pytest.raises(OSError, match="checkpoint"):
            rounds.Round(directory).advance()

    assert rounds.Round(directory).state["next"] == 0
    # When a new controller resumes from disk.
    result = rounds.Round(directory).advance(recovery=True)
    # Then reconcile the completed child without duplicating sessions.
    assert result["phase"] == "completed"
    assert (
        len(calls.read_text().splitlines())
        == len(set(calls.read_text().splitlines()))
        == 3
    )
    assert len(completed_jobs(repo, result)) == 3


@pytest.mark.parametrize("allocation_written", [False, True])
def test_round_recovers_allocation_with_same_id_and_time(
    round_case, monkeypatch, allocation_written
):
    # Given a round-owned ID and child allocation interrupted around the state.json write.
    _, directory, calls = round_case
    original_create = rounds.create
    original_json = runner.atomic_json

    def allocate_then_fail(*args, **kwargs):
        original_create(*args, **kwargs)
        raise OSError("allocation interrupted")

    def fail_state(path, value):
        if path.name == "state.json" and value["phase"] == "preparing":
            path.with_suffix(".tmp").write_text('{"partial":')
            raise OSError("allocation interrupted")

        original_json(path, value)

    with monkeypatch.context() as m:
        if allocation_written:
            m.setattr(rounds, "create", allocate_then_fail)
        else:
            m.setattr(runner, "atomic_json", fail_state)

        with pytest.raises(OSError, match="allocation"):
            rounds.Round(directory).advance()

    intent = rounds.Round(directory).state["jobs"][0]
    # When the same round resumes after allocation failure.
    result = rounds.Round(directory).advance(recovery=True)
    # Then retain the original ID and start time without creating extra children.
    state = json.loads(
        (
            Path(result["repo"]) / ".mutagi/runs" / intent["id"] / "state.json"
        ).read_text()
    )
    assert state["started_at"] == runner.utc(intent["allocation"]["started"])
    assert (
        state["deadline"]
        == intent["allocation"]["started"] + intent["allocation"]["seconds"]
    )
    assert calls.read_text().splitlines()[0] == intent["id"]
    assert len(calls.read_text().splitlines()) == 3


def test_pending_child_preservation_blocks_next_session(round_case, monkeypatch):
    # Given a first child unable to complete preservation.
    _, directory, calls = round_case
    with monkeypatch.context() as m:
        m.setattr(
            runner.Runner,
            "preserve",
            lambda _: (_ for _ in ()).throw(RuntimeError("save failed")),
        )
        with pytest.raises(RuntimeError, match="save failed"):
            rounds.Round(directory).advance()

    state = rounds.Round(directory).state
    assert state["jobs"][1]["allocation"] is None
    assert len(calls.read_text().splitlines()) == 1
    # When preservation becomes possible and the round resumes.
    result = rounds.Round(directory).advance(recovery=True)
    # Then advance only after preservation without restarting the first agent.
    assert result["phase"] == "completed"
    assert len(calls.read_text().splitlines()) == 3


def test_round_budget_exhaustion_does_not_start_next_session(round_case, monkeypatch):
    # Given the round budget expires after preserving its first child.
    _, directory, calls = round_case
    original = rounds.Round.save

    def expire_after_child(self):
        if self.state["next"] == 1:
            self.state["deadline"] = time.time() - 1

        original(self)

    monkeypatch.setattr(rounds.Round, "save", expire_after_child)
    # When the controller considers the next task and is later resumed.
    result = rounds.Round(directory).advance()
    repeated = rounds.Round(directory).advance(recovery=True)
    # Then record two unstarted slots without extending the budget or starting sessions.
    assert result["phase"] == repeated["phase"] == "budget_exhausted"
    assert len(result["not_started"]) == 2
    assert len(calls.read_text().splitlines()) == 1


def test_integration_scope_violation_blocks_publication(round_case, monkeypatch):
    # Given an integration session changing out-of-scope operational instructions.
    repo, directory, _ = round_case
    original = rounds.Integration.execute

    def violate_scope(self):
        original(self)
        (self.worktree / "docs/operations.md").write_text("Out of scope\n")

    monkeypatch.setattr(rounds.Integration, "execute", violate_scope)
    # When integration results are collected.
    with pytest.raises(RuntimeError, match="out-of-scope"):
        rounds.Round(directory).advance()

    # Then preserve evidence for review without publishing to the shared tree.
    state = rounds.Round(directory).state
    assert state["next"] == 2
    assert not (repo / "knowledge/notes/integrated.md").exists()
    assert list(
        (repo / ".mutagi/runs" / state["jobs"][2]["id"] / "recovery").glob(
            "*/files.tar"
        )
    )


def test_interrupted_integration_is_saved_without_publication(round_case, monkeypatch):
    # Given changes interrupted by the integration deadline.
    repo, directory, _ = round_case
    original = rounds.Integration.execute

    def timeout(self):
        original(self)
        self.state["reason"] = "time_limit"
        self.save()

    monkeypatch.setattr(rounds.Integration, "execute", timeout)
    # When the interrupted integration is preserved.
    result = rounds.Round(directory).advance()
    # Then retain the branch without publishing unverified changes.
    integration = completed_jobs(repo, result)[2]
    assert not integration["published"]
    assert integration["reason"] == "time_limit"
    assert not (repo / "knowledge/notes/integrated.md").exists()
    assert runner.git(
        repo, "show", integration["saved_commit"] + ":knowledge/notes/integrated.md"
    )


def test_publication_checkpoint_loss_does_not_duplicate_commit(round_case, monkeypatch):
    # Given a successful publication commit followed by a lost final checkpoint.
    repo, directory, calls = round_case
    original = rounds.Integration.save

    def fail_published(self):
        if self.state["phase"] == "registered":
            raise OSError("publication checkpoint lost")

        original(self)

    with monkeypatch.context() as m:
        m.setattr(rounds.Integration, "save", fail_published)
        with pytest.raises(OSError, match="publication checkpoint"):
            rounds.Round(directory).advance()

    published = runner.git(repo, "rev-parse", "HEAD")
    # When a new controller compares the saved tree and publication marker.
    result = rounds.Round(directory).advance(recovery=True)
    # Then neither the integration agent nor the publication commit is duplicated.
    assert result["phase"] == "completed"
    assert runner.git(repo, "rev-parse", "HEAD") == published
    assert len(calls.read_text().splitlines()) == 3


def test_failed_publication_commit_resumes_exact_staged_tree(round_case):
    # Given a shared-branch hook rejecting final knowledge publication.
    repo, directory, calls = round_case
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\nif [ \"$(git branch --show-current)\" = main ] && git diff --cached --name-only | grep -q '^knowledge/notes/integrated.md$'; then exit 1; fi\n"
    )
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="preservation failed"):
        rounds.Round(directory).advance()

    # When the hook is removed and the round resumes.
    hook.unlink()
    result = rounds.Round(directory).advance(recovery=True)
    # Then publish exactly the saved tree without rerunning the agent.
    assert result["phase"] == "completed"
    assert not runner.git(repo, "status", "--porcelain")
    assert len(calls.read_text().splitlines()) == 3


@pytest.mark.parametrize(
    "defect", ["self_parent", "nan_budget", "zero_reserve", "extra_field"]
)
def test_invalid_round_plan_is_rejected_before_allocation(round_case, defect):
    # Given a plan with invalid dependencies, budgets, or fields.
    repo, directory, calls = round_case
    plan = rounds.Round(directory).state["plan"]
    if defect == "self_parent":
        plan["tasks"][0]["parent"] = 0
    elif defect == "nan_budget":
        plan["seconds"] = float("nan")
    elif defect == "zero_reserve":
        plan["reserve_seconds"] = 0
    else:
        plan["automatic_retries"] = 3

    # When validation runs before round creation.
    with pytest.raises(ValueError):
        rounds.create_round(repo, plan)

    # Then no session has started.
    assert not calls.exists()


def test_changed_assignment_blocks_resume(round_case):
    # Given an allocated run whose deadline no longer matches its round assignment.
    repo, directory, calls = round_case
    controller = rounds.Round(directory)
    job = controller.allocate(0)
    job.state["deadline"] += 100
    job.save()
    # When a new controller resumes.
    with pytest.raises(RuntimeError, match="fixed assignment"):
        rounds.Round(directory).advance(recovery=True)

    # Then retain the unexpected state for inspection without invoking a model.
    assert not calls.exists()
    assert (repo / ".mutagi/runs" / job.state["id"] / "state.json").exists()


def test_missing_registered_entry_blocks_next_session(round_case, monkeypatch):
    # Given a completed first child whose registration is missing from the shared catalog.
    repo, directory, calls = round_case
    original = rounds.Round.save

    def stop_after_child(self):
        original(self)
        if self.state["next"] == 1:
            raise OSError("stop before next task")

    with monkeypatch.context() as m:
        m.setattr(rounds.Round, "save", stop_after_child)
        with pytest.raises(OSError):
            rounds.Round(directory).advance()

    (repo / "experiments/INDEX.md").write_text("# Lost registration\n")
    runner.git(repo, "add", "experiments/INDEX.md")
    runner.git(repo, "commit", "-m", "test: remove registration")
    # When resuming a checkpoint that marks the first child as complete.
    with pytest.raises(RuntimeError, match="a saved experiment is missing from the shared catalog"):
        rounds.Round(directory).advance(recovery=True)

    # Then the missing registration prevents a second model invocation.
    assert len(calls.read_text().splitlines()) == 1


def test_integration_cannot_remove_source_registration(round_case, monkeypatch):
    # Given a completed integration that accidentally removed a source registration.
    repo, directory, _ = round_case
    original = rounds.Integration.execute

    def remove_catalog(self):
        original(self)
        (self.worktree / "experiments/INDEX.md").write_text("# Missing sources\n")

    monkeypatch.setattr(rounds.Integration, "execute", remove_catalog)
    # When publication validates the saved integration tree.
    with pytest.raises(RuntimeError, match="a saved experiment is missing from the shared catalog"):
        rounds.Round(directory).advance()

    # Then retain both source registrations in the shared catalog and preserve the integration.
    assert (repo / "experiments/INDEX.md").read_text().count("<!-- forest-run:") == 2
    state = rounds.Round(directory).state
    integration = json.loads(
        (repo / ".mutagi/runs" / state["jobs"][2]["id"] / "state.json").read_text()
    )
    assert integration["phase"] == "saved"


def test_pending_round_rejects_second_controller(round_case):
    # Given an unfinished round with allocated session IDs.
    repo, directory, calls = round_case
    plan = rounds.Round(directory).state["plan"]
    # When another round is requested in the same repository.
    with pytest.raises(RuntimeError, match="unfinished round"):
        rounds.create_round(repo, plan)

    # Then neither a second round nor a session exists.
    assert len(list((repo / ".mutagi/runs/rounds").glob("*/state.json"))) == 1
    assert not calls.exists()


def test_operator_interruption_preserves_current_run_and_stops_sequence(
    round_case, monkeypatch
):
    # Given an operator interruption during the first model invocation.
    _, directory, calls = round_case
    with monkeypatch.context() as m:
        m.setattr(
            runner.Runner,
            "execute",
            lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        result = rounds.Round(directory).advance()

    # When the controller preserves the interrupted run and returns.
    assert result["phase"] == "stopped"
    assert result["next"] == 1
    assert not calls.exists()
    assert rounds.Round(directory).advance()["phase"] == "stopped"
    # Then only explicit resumption starts remaining sessions within the same budget.
    resumed = rounds.Round(directory).advance(recovery=True)
    assert resumed["phase"] == "completed"
    assert resumed["deadline"] == result["deadline"]
    assert len(calls.read_text().splitlines()) == 2
