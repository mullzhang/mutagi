"""Verify built artifacts in fresh environments without model or network calls."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
AUDIT = Path(tempfile.mkdtemp(prefix="mutagi-distribution-"))
UV = shutil.which("uv")
if UV is None:
    raise RuntimeError("uv is required to verify package installation and updates")
BASE_ENV = dict(os.environ)
BASE_ENV.pop("PYTHONPATH", None)
BASE_ENV.pop("PYTEST_ADDOPTS", None)
COUNTER = 0


def run(command, cwd, env=BASE_ENV, input=None):
    global COUNTER
    COUNTER += 1
    result = subprocess.run(
        [str(value) for value in command],
        cwd=cwd,
        env=env,
        input=input,
        capture_output=True,
        text=True,
        timeout=120,
    )
    (AUDIT / f"command-{COUNTER:03}.log").write_text(
        shlex.join([str(value) for value in command])
        + "\n"
        + result.stdout
        + result.stderr
    )
    if result.returncode:
        raise RuntimeError(
            f"Command {COUNTER} failed: {result.stdout[-2000:]}\n{result.stderr[-3000:]}"
        )
    return result.stdout


def configure_git(project):
    run(["git", "init", "-b", "main", project], AUDIT)
    run(["git", "-C", project, "config", "user.name", "Distribution Test"], AUDIT)
    run(["git", "-C", project, "config", "user.email", "test@example.invalid"], AUDIT)
    run(["git", "-C", project, "config", "commit.gpgsign", "false"], AUDIT)
    hooks = AUDIT / "empty-hooks"
    hooks.mkdir(exist_ok=True)
    run(["git", "-C", project, "config", "core.hooksPath", hooks], AUDIT)


def commit(project):
    run(["git", "-C", project, "add", "."], AUDIT)
    run(["git", "-C", project, "commit", "-m", "test: prepare isolated project"], AUDIT)


FAKE_CODEX = r"""import json, pathlib, subprocess, sys
args = sys.argv[1:]
if args == ['--version']:
    print('codex-cli distribution-test')
elif args == ['exec', '--help']:
    print('--json --approve-for-me --model --config --output-schema --output-last-message')
elif args == ['login', 'status']:
    print('Authenticated test fixture')
else:
    assert args[:2] == ['exec', '--json'], args
    output = pathlib.Path(args[args.index('--output-last-message') + 1])
    state = json.loads((output.parent / 'state.json').read_text())
    prompt = sys.stdin.read()
    assert state['id'] in prompt
    print(json.dumps({'type': 'thread.started', 'thread_id': state['id']}), flush=True)
    if state['branch'].startswith('codex/integrate-'):
        sources = json.loads((output.parent / 'task.txt').read_text())['sources']
        for source in sources:
            subprocess.run(['git', 'cat-file', '-e', source['saved_commit'] + ':' + source['result']['record']], check=True)
        pathlib.Path('knowledge/notes/integrated-' + state['id'] + '.md').write_text('# Synthetic integration\nNo model was called.\n')
        output.write_text(json.dumps({'summary': 'Saved sources checked', 'limits': 'No model call', 'checks': 'Fixed source paths'}))
    else:
        assert '## Question and observation method' in prompt
        assert '## Reproduction and preservation' in prompt
        folder = pathlib.Path(state['project']['experiments']) / (state['started_at'].replace(':', '') + '_distribution-smoke_' + state['id'])
        folder.mkdir()
        (folder / 'input.txt').write_text('Synthetic input; no model was called.\n')
        subprocess.run(['git', 'add', str(folder)], check=True)
        subprocess.run(['git', 'commit', '-m', 'test: preserve synthetic input'], check=True, stdout=subprocess.DEVNULL)
        implementation = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        record = folder / 'README.md'
        _, header, body = prompt.rsplit('\n---\n', 2)
        fields = dict(line.split(': ', 1) for line in header.splitlines())
        assert fields == {'id': state['id'], 'started_at': "'" + state['started_at'] + "'", 'base_commit': state['base'], 'mode': 'inherited' if state['parent_ids'] else 'independent', 'parent_ids': json.dumps(state['parent_ids']), 'implementation_commit': 'null', 'status': 'unresolved', 'human_decision': 'unreviewed'}
        record.write_text(('---\n' + header + '\n---\n' + body).replace('implementation_commit: null', 'implementation_commit: ' + implementation, 1).replace('status: unresolved', 'status: observed', 1).replace('# <Name of the question>', '# Synthetic package verification', 1))
        record.write_text(record.read_text() + '\nNo model was called.\n')
        output.write_text(json.dumps({'title': 'Package verification', 'summary': 'Synthetic result', 'limits': 'No model call', 'record': str(record), 'implementation_commit': implementation, 'checks': 'Package workflow fixture', 'status': 'observed'}))
    print(json.dumps({'type': 'turn.completed'}), flush=True)
"""


FAKE_APP_SERVER = r"""import json, sys
assert sys.argv[1:] == ['app-server', '--listen', 'stdio://']
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method = message['method']
    if method == 'initialize':
        emit({'id': message['id'], 'result': {}})
    elif method == 'thread/start':
        emit({'id': message['id'], 'result': {'thread': {'id': 'installed-thread'}, 'model': 'synthetic-test-model'}})
    elif method == 'turn/start':
        emit({'id': message['id'], 'result': {'turn': {'id': 'installed-turn'}}})
        emit({'method': 'thread/tokenUsage/updated', 'params': {'threadId': 'installed-thread', 'tokenUsage': {'total': {'inputTokens': 100, 'cachedInputTokens': 0, 'outputTokens': 10, 'reasoningOutputTokens': 0, 'totalTokens': 110}}}})
        emit({'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': '{"done": true}'}}})
        emit({'method': 'turn/completed', 'params': {'turn': {'id': 'installed-turn', 'status': 'completed'}}})
"""


def verify_workers(python, executable, codex, env, project, method):
    run(
        [python, "-I", "-m", "mutagi", "service", "--repo", project, "tick"], AUDIT, env
    )
    assert not (project / ".mutagi/runs/service.json").exists()

    job = AUDIT / (method + "-job")
    run(
        [
            executable,
            "job",
            "start",
            job,
            "--cwd",
            AUDIT,
            "--seconds",
            "20",
            "--command",
            python,
            "-I",
            "-c",
            "print('Installed supervisor verified')",
        ],
        AUDIT,
        env,
    )
    until = time.monotonic() + 25
    while True:
        state = json.loads((job / "state.json").read_text())
        if state["stage"] == "finished":
            break
        assert time.monotonic() < until, state
        time.sleep(0.05)
    assert state["result"] == {"exit_code": 0, "timed_out": False}
    assert (job / "command.log").read_text() == "Installed supervisor verified\n"
    assert (
        json.loads(run([executable, "job", "status", job, "--json"], AUDIT, env))
        == state
    )

    session = AUDIT / (method + "-managed-session")
    session.mkdir()
    (session / "state.json").write_text(
        json.dumps(
            {
                "model": "synthetic-test-model",
                "effort": "medium",
                "worktree": str(session),
                "exploration_deadline": time.time() + 30,
                "usage_budget": {
                    "tokens": 1000,
                    "cost": 1,
                    "rates": {
                        "input": 1,
                        "cached_input": 1,
                        "output": 1,
                        "unit": "test-units",
                        "source": "Synthetic distribution fixture",
                        "model": "synthetic-test-model",
                    },
                },
            }
        )
    )
    (session / "prompt.txt").write_text("Verify the installed managed worker.")
    (session / "schema.json").write_text('{"type":"object"}\n')
    original = codex.read_text()
    try:
        codex.write_text("#!" + str(python) + "\n" + FAKE_APP_SERVER)
        output = run(
            [python, "-I", "-m", "mutagi.execution.codex_session", session], AUDIT, env
        )
    finally:
        codex.write_text(original)
    assert json.loads((session / "result.json").read_text()) == {"done": True}
    usage = json.loads((session / "usage.json").read_text())
    assert usage["complete"] and usage["tokens"] == 110
    assert json.loads(output.splitlines()[-1]) == {"type": "turn.completed"}
    return {
        "job": str(job),
        "managed_session": str(session),
        "unassigned_service_tick": True,
    }


def verify_install(artifact, method):
    environment = AUDIT / (method + "-venv")
    run([UV, "venv", "--offline", "--python", sys.executable, environment], AUDIT)
    python = environment / "bin/python"
    run([UV, "pip", "install", "--offline", "--python", python, artifact], AUDIT)
    binary = AUDIT / (method + "-bin")
    binary.mkdir()
    fake = binary / "codex"
    fake.write_text("#!" + str(python) + "\n" + FAKE_CODEX)
    fake.chmod(0o755)
    env = dict(
        BASE_ENV,
        PATH=str(binary)
        + os.pathsep
        + str(environment / "bin")
        + os.pathsep
        + BASE_ENV["PATH"],
    )
    executable = environment / "bin/mutagi"
    imported = run(
        [python, "-I", "-c", "import mutagi; print(mutagi.__file__)"], AUDIT, env
    ).strip()
    assert str(environment) in imported and str(ROOT) not in imported
    for command in [
        [],
        ["init"],
        ["check"],
        ["experiment"],
        ["round"],
        ["series"],
        ["service"],
        ["handoff"],
        ["job"],
    ]:
        run([executable, *command, "--help"], AUDIT, env)
    assert run([executable, "--version"], AUDIT, env).strip() == "mutagi " + VERSION
    template = run([executable, "template", "experiment"], AUDIT, env)
    assert (
        template == (ROOT / "src/mutagi/resources/templates/experiment.md").read_text()
    )
    project = AUDIT / (method + "-project")
    configure_git(project)
    (project / "README.md").write_text("# Isolated workflow verification\n")
    before = {
        str(path.relative_to(project)): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }
    preview = run(
        [executable, "init", "--reference", "README.md", "--dry-run"], project, env
    )
    assert ".mutagi/config.toml" in preview
    assert before == {
        str(path.relative_to(project)): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }
    run(
        [
            executable,
            "init",
            "--reference",
            "README.md",
            "--model",
            "synthetic-test-model",
            "--effort",
            "medium",
        ],
        project,
        env,
    )
    assert not (project / ".mutagi/plans").exists()
    commit(project)
    checks = run([executable, "check"], project, env)
    assert "BLOCKED" not in checks and "OK Committed project files" in checks
    instruction = "Package-only exploration.\n日本語の指示も記録する。\n"
    source = ["--prompt", instruction] if method == "wheel" else ["--task", "-"]
    run(
        [
            executable,
            "experiment",
            "start",
            "--base",
            "HEAD",
            *source,
            "--seconds",
            "60",
            "--reserve-seconds",
            "10",
        ],
        project,
        env,
        input=instruction if method == "sdist" else None,
    )
    states = list((project / ".mutagi/runs").glob("*/state.json"))
    assert len(states) == 1
    state = json.loads(states[0].read_text())
    assert state["phase"] == "registered" and state["reason"] == "completed", state
    assert (states[0].parent / "task.txt").read_text() == instruction
    record = run(
        [
            "git",
            "-C",
            project,
            "show",
            state["saved_commit"] + ":" + state["result"]["record"],
        ],
        AUDIT,
    )
    assert "No model was called." in record
    assert run(["git", "-C", project, "status", "--porcelain"], AUDIT).strip() == ""
    assert "BLOCKED" not in run([executable, "check"], project, env)
    # Exercise harvesting through the installed CLI, not source imports.
    harvest_head = run(["git", "rev-parse", "HEAD"], project, env).strip()
    listed = json.loads(run([executable, "harvest", "list", "--json"], project, env))
    assert [entry["id"] for entry in listed] == [state["id"]]
    trial = json.loads(
        run([executable, "harvest", "checkout", state["id"], "--json"], project, env)
    )
    assert trial["saved_commit"] == state["saved_commit"]
    assert Path(trial["record"]).is_file() and not trial["dirty"]
    purpose = "Reuse only the verified parts. 日本語の収穫指示。"
    prompt = run(
        [
            executable,
            "harvest",
            "prepare",
            state["id"],
            "--purpose",
            purpose,
            "--kind",
            "idea",
            "--part",
            state["result"]["record"],
        ],
        project,
        env,
    )
    assert prompt.startswith("Use a preserved exploration result")
    assert purpose in prompt and state["saved_commit"] in prompt
    assert "No model was called." in prompt
    prompt_file = AUDIT / (method + "-harvest-prompt.md")
    run(
        [
            executable,
            "harvest",
            "prepare",
            state["id"],
            "--purpose",
            purpose,
            "--kind",
            "idea",
            "--part",
            state["result"]["record"],
            "--output",
            prompt_file,
        ],
        project,
        env,
    )
    assert prompt_file.read_text() == prompt
    review_file = AUDIT / (method + "-harvest-review.md")
    review_file.write_text(
        "Defer until destination verification.\nNo model was called.\n"
    )
    decision = json.loads(
        run(
            [
                executable,
                "harvest",
                "record",
                state["id"],
                "--decision",
                "defer",
                "--kind",
                "idea",
                "--part",
                state["result"]["record"],
                "--note-file",
                review_file,
                "--json",
            ],
            project,
            env,
        )
    )
    assert decision["record"]["verification"] is None
    shown = json.loads(
        run([executable, "harvest", "show", state["id"], "--json"], project, env)
    )
    assert shown["harvesting_records"] == [decision["record"]]
    assert run(["git", "rev-parse", "HEAD"], project, env).strip() == harvest_head
    assert run(["git", "status", "--porcelain"], project, env).strip() == ""
    execution_ids = {"experiment": state["id"]}
    for kind in ("round", "series"):
        flags = [
            "--base",
            "HEAD",
            "--prompt",
            "A finite synthetic case.",
            "--inherit",
            "0",
            "Check the preserved result.",
            "--seconds",
            "600",
            "--experiment-seconds",
            "60",
            "--integration-seconds",
            "60",
            "--reserve-seconds",
            "10",
        ]
        if kind == "series":
            flags += ["--rounds", "2", "--round-seconds", "300"]
        before = {
            str(path.relative_to(project)): path.read_bytes()
            for path in project.rglob("*")
            if path.is_file() and ".mutagi/worktrees" not in str(path)
        }
        preview = json.loads(
            run([executable, kind, "start", *flags, "--dry-run"], project, env)
        )
        assert preview["model"] == "synthetic-test-model"
        assert before == {
            str(path.relative_to(project)): path.read_bytes()
            for path in project.rglob("*")
            if path.is_file() and ".mutagi/worktrees" not in str(path)
        }
        run(
            [executable, kind, "start", "--plan", "-"],
            project,
            env,
            input=json.dumps(preview),
        )
        states_dir = (
            project / ".mutagi/runs" / ("rounds" if kind == "round" else "series")
        )
        completed = [
            json.loads(path.read_text())
            for path in states_dir.glob("*/state.json")
            if "series_id" not in json.loads(path.read_text())
        ]
        assert len(completed) == 1 and completed[0]["phase"] == "completed", completed
        execution_ids[kind] = completed[0]["id"]
        output = run([executable, kind, "status", completed[0]["id"]], project, env)
        assert (
            "Recorded progress:" in output and "Human adoption: not tracked" in output
        )
        assert (
            json.loads(
                run(
                    [executable, kind, "status", completed[0]["id"], "--json"],
                    project,
                    env,
                )
            )
            == completed[0]
        )
    assert "Reported observation: observed" in run(
        [executable, "experiment", "status", state["id"]], project, env
    )
    assert run(["git", "-C", project, "status", "--porcelain"], AUDIT).strip() == ""
    assert "BLOCKED" not in run([executable, "check"], project, env)
    workers = verify_workers(python, executable, fake, env, project, method)
    print(
        method
        + ": installed workflows, harvesting, relocated workers, service entry, and status passed",
        flush=True,
    )
    return {
        "artifact": str(artifact),
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "python": str(python),
        "module": imported,
        "project": str(project),
        "execution_ids": execution_ids,
        "workers": workers,
        "saved_commit": state["saved_commit"],
    }


wheel = ROOT / f"dist/mutagi-{VERSION}-py3-none-any.whl"
sdist = ROOT / f"dist/mutagi-{VERSION}.tar.gz"
inventory = (
    subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    .decode()
    .split("\0")
)
public = {name for name in inventory if name and (ROOT / name).is_file()}
with tarfile.open(sdist) as archive:
    members = {
        str(PurePosixPath(member.name).relative_to(f"mutagi-{VERSION}")): member
        for member in archive.getmembers()
        if member.isfile()
    }
    assert set(members) == public | {"PKG-INFO"}, (
        set(members) - public,
        public - set(members),
    )
    for name in public:
        assert (
            archive.extractfile(members[name]).read() == (ROOT / name).read_bytes()
        ), name
    extracted = AUDIT / "source"
    for name, member in members.items():
        relative = PurePosixPath(name)
        assert not relative.is_absolute() and ".." not in relative.parts
        destination = extracted / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.extractfile(member).read())
with zipfile.ZipFile(wheel) as archive:
    packaged = {
        name.removeprefix("src/") for name in public if name.startswith("src/mutagi/")
    }
    actual = {name for name in archive.namelist() if ".dist-info/" not in name}
    assert actual == packaged, (actual - packaged, packaged - actual)
    for name in packaged:
        assert archive.read(name) == (ROOT / "src" / name).read_bytes(), name
    assert any(name.endswith("/licenses/LICENSE") for name in archive.namelist())
    assert not any(
        part in {"docs_local", ".venv", ".git", "__pycache__", ".pytest_cache"}
        for name in archive.namelist()
        for part in PurePosixPath(name).parts
    )

report = {
    "public_source_files": len(public),
    "package_files": len(packaged),
    "installs": [],
}
for artifact, method in [(wheel, "wheel"), (sdist, "sdist")]:
    report["installs"].append(verify_install(artifact, method))

sample = AUDIT / "copied-example"
shutil.copytree(extracted / "examples/minimal-python", sample)
configure_git(sample)
python = Path(report["installs"][0]["python"])
run([python, "-I", "-m", "unittest", "discover", "-s", "tests"], sample)
commit(sample)
run(
    [
        python,
        "-I",
        "-c",
        """from pathlib import Path
import argparse
from mutagi.commands.inputs import add_plan_arguments, read_plan
from mutagi.execution.experiment import Runner, create
from mutagi.execution.plans import validate_round, validate_series
from mutagi.execution.state import lock
root = Path.cwd()
for kind, validate in [('round', validate_round), ('series', validate_series)]:
    parser = argparse.ArgumentParser()
    add_plan_arguments(parser, series=kind == 'series')
    args = parser.parse_args(['--plan', str(root / '.mutagi/plans' / (kind + '.json')), '--model', 'synthetic-test-model'])
    read_plan(args, validate)
with lock(root):
    directory = create(root, 'HEAD', 'Prepare the copied example.', 'synthetic-test-model', 'medium', 60, 10)
job = Runner(directory)
job.prepare()
assert job.state['phase'] == 'ready'
print('Copied source example: tests, plan resolution, and preparation passed')
""",
    ],
    sample,
)
report["copied_example"] = str(sample)
# Exercise real uv Git installs and updates with isolated tool directories.
# Only this disposable Git repository receives fixture commits and tags.
git_source = AUDIT / "git-source"
shutil.copytree(extracted, git_source)
configure_git(git_source)
commit(git_source)
original = run(["git", "-C", git_source, "rev-parse", "HEAD"], AUDIT).strip()
tag = "v" + VERSION
run(["git", "-C", git_source, "tag", tag], AUDIT)
tool_root, tool_bin = AUDIT / "tools", AUDIT / "tool-bin"
tool_env = dict(
    BASE_ENV,
    UV_TOOL_DIR=str(tool_root),
    UV_TOOL_BIN_DIR=str(tool_bin),
    UV_PYTHON=sys.executable,
    UV_PYTHON_DOWNLOADS="never",
)
url = "git+" + git_source.as_uri()


def installation():
    code = "import importlib.metadata,json; d=importlib.metadata.distribution('mutagi'); print(json.dumps({'version':d.version,'commit':json.loads(d.read_text('direct_url.json'))['vcs_info']['commit_id']}))"
    return json.loads(
        run([tool_root / "mutagi/bin/python", "-I", "-c", code], AUDIT, tool_env)
    )


run([UV, "tool", "install", "--offline", url + "@main"], AUDIT, tool_env)
assert installation() == {"version": VERSION, "commit": original}
marker = git_source / "README.md"
marker.write_text(
    marker.read_text() + "\nDisposable Git-update verification fixture.\n"
)
commit(git_source)
updated = run(["git", "-C", git_source, "rev-parse", "HEAD"], AUDIT).strip()
run([UV, "tool", "upgrade", "--offline", "mutagi"], AUDIT, tool_env)
assert installation() == {"version": VERSION, "commit": updated}
run([UV, "tool", "install", "--offline", url + "@" + tag], AUDIT, tool_env)
assert installation() == {"version": VERSION, "commit": original}
major, minor, patch = VERSION.split(".")
next_version = f"{major}.{minor}.{int(patch) + 1}"
project_file = git_source / "pyproject.toml"
project_file.write_text(
    project_file.read_text().replace(
        f'version = "{VERSION}"', f'version = "{next_version}"', 1
    )
)
lock_file = git_source / "uv.lock"
lock_file.write_text(
    lock_file.read_text().replace(
        f'name = "mutagi"\nversion = "{VERSION}"',
        f'name = "mutagi"\nversion = "{next_version}"',
    )
)
commit(git_source)
run(["git", "-C", git_source, "tag", "v" + next_version], AUDIT)
run([UV, "tool", "upgrade", "--offline", "mutagi"], AUDIT, tool_env)
assert installation() == {"version": VERSION, "commit": original}
run([UV, "tool", "install", "--offline", url + "@v" + next_version], AUDIT, tool_env)
final_commit = run(["git", "-C", git_source, "rev-parse", "HEAD"], AUDIT).strip()
assert installation() == {"version": next_version, "commit": final_commit}
assert (
    run([tool_bin / "mutagi", "--version"], AUDIT, tool_env).strip()
    == "mutagi " + next_version
)
run(
    [
        sys.executable,
        git_source / "scripts/check_release.py",
        "v" + next_version,
        "--repo",
        git_source,
    ],
    AUDIT,
)
report["git_updates"] = {
    "repository": str(git_source),
    "branch_initial": original,
    "branch_updated": updated,
    "pinned_tag": tag,
    "pinned_commit": original,
    "fixture_next_tag": "v" + next_version,
    "fixture_next_commit": final_commit,
    "isolated_tool_directory": str(tool_root),
}
print(
    "Git source: branch upgrades, fixed tags, explicit version changes, and release checks passed",
    flush=True,
)
(AUDIT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print("Verified distribution report: " + str(AUDIT / "report.json"), flush=True)
