from __future__ import annotations

import os
import sys

from mutagi.project.repository import git


def create_diagnostic_codex(tmp_path, monkeypatch):
    binary = tmp_path / "diagnostic-bin"
    binary.mkdir()
    command = binary / "codex"
    command.write_text(f"""#!{sys.executable}
import os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli test")
elif args == ["exec", "--help"]:
    print("--json --approve-for-me --model --config --output-schema --output-last-message")
elif args == ["login", "status"]:
    print("private-auth-output-for-test")
    sys.exit(int(os.environ.get("TEST_AUTH_STATUS", "0")))
else:
    raise AssertionError("Diagnostics must not execute a model: " + repr(args))
""")
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return command


def create_codex(tmp_path, monkeypatch):
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
_, header, body = prompt.rsplit('\\n---\\n', 2)
fields = dict(line.split(': ', 1) for line in header.splitlines())
assert fields == {{'id': s['id'], 'started_at': "'" + s['started_at'] + "'", 'base_commit': s['base'], 'mode': 'inherited' if s['parent_ids'] else 'independent', 'parent_ids': json.dumps(s['parent_ids']), 'implementation_commit': 'null', 'status': 'unresolved', 'human_decision': 'unreviewed'}}
record.write_text(('---\\n' + header + '\\n---\\n' + body).replace('implementation_commit: null', 'implementation_commit: ' + implementation, 1).replace('status: unresolved', 'status: observed', 1).replace('# <Name of the question>', '# Test question', 1))
(d/'necessary.txt').write_text('untracked evidence')
out.write_text(json.dumps({{"title":"Test question","summary":"Finite result","limits":"Synthetic fixture","record":str(record),"implementation_commit":implementation,"checks":"fixture","status":"observed"}}))
print(json.dumps({{"type":"turn.completed"}}),flush=True)
""")
    p.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return p


def prepare_round_codex(repo, fake_codex, tmp_path):
    for name in (".mutagi/prompts/integrate.md", "knowledge/README.md"):
        (repo / name).write_text("# Integration reference\n")

    git(repo, "add", ".")
    git(repo, "commit", "-m", "docs: add integration references")
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
    return calls
