# Compare text-counting definitions

This project counts whitespace-delimited items and distinct items with Python's standard library.
Punctuation remains attached, identifiers remain whole, and repeated equal items count once toward the distinct total.
These are the baseline rules. An exploration can compare a different definition on concrete inputs without assuming these rules are defective.

## Copy and prepare

Install mutagi, Python 3.12+, Git, and an authenticated Codex CLI.
From the root of a mutagi checkout, copy the entire example into a fresh directory:

```bash
example_dir="$(mktemp -d)"
cp -R examples/minimal-python/. "$example_dir/"
cd "$example_dir"
git init -b main
python3 -m unittest discover -s tests
git add -- .
git commit -m "feat: seed text-counting example"
mutagi check
```

Configure Git author information if the commit requires it.
The project already includes its configuration and three policies; `mutagi init` is not needed.
The configured preparation reruns the baseline tests in each exploration worktree.
The explorer may change `src/`, `tests/`, `knowledge/notes/`, and its own experiment directory.

## Start one exploration

Set the shell variable `MODEL` to a model available to your account, then run:

```bash
mutagi experiment start --base HEAD \
  --prompt "Compare whitespace-delimited item counting with one explicit alternative on punctuation, identifiers, or Japanese text. Record where the meanings differ without assuming the baseline is defective." \
  --model "$MODEL" --effort medium --seconds 1200 --reserve-seconds 180
```

The same instruction is available in `.mutagi/plans/first.txt` for reuse with `--task`.
Preparation is included in the time allocation; preservation has a three-minute reserve.
Stop grace and preservation work can extend the wall-clock finish time.

Read `experiments/INDEX.md` after registration. It links each saved revision and record path; the implementation and record remain on that exploration's branch.
Use the `git show <saved-commit>:<record-path>` command recorded in the catalog to read the result.
Check the inputs, observed implementation revision, limitations, and reproduction commands before deciding what to adopt.
For interrupted work, use `mutagi experiment status <UUID>` and `mutagi experiment resume <UUID>`.

## Try a round or a series

The plan files omit `model`; the CLI applies the explicitly supplied model before validating and saving the complete plan.
Run one of these after earlier work is complete:

```bash
mutagi round start --plan .mutagi/plans/round.json --model "$MODEL"
mutagi series start --plan .mutagi/plans/series.json --model "$MODEL"
```

The round compares one question, inherits its result to test a new boundary, and integrates knowledge.
The series runs two rounds; the next round reads the published knowledge. Parent indices refer only to earlier tasks within the same round.
Knowledge integration updates shared notes and the catalog. It does not adopt experiment implementations.
The sample sets no usage prices, adaptation policy, or background service.

## Preview a plan or construct one directly

Previewing does not execute a model or allocate run state:

```bash
mutagi round start --plan .mutagi/plans/round.json --model "$MODEL" --dry-run
mutagi series start --plan .mutagi/plans/series.json --model "$MODEL" --dry-run
```

A round can also be expressed without a plan file:

```bash
mutagi round start --base HEAD \
  --prompt "Compare whitespace-delimited items with one explicit alternative." \
  --inherit 0 "Test another boundary using the saved comparison." \
  --model "$MODEL" --effort medium \
  --seconds 2400 --experiment-seconds 900 \
  --integration-seconds 600 --reserve-seconds 120 --dry-run
```

Remove `--dry-run` to execute the resolved round. Use `series start` with `--rounds 2`, `--round-seconds 2400`, and an appropriate total budget to repeat the task pattern.
After a run, `mutagi round status <UUID>` or `mutagi series status <UUID>` shows checkpoints, result paths, and next actions. Add `--json` for the saved state.
For repeated use of one model and effort, save explicit [execution defaults](../../docs/reference/configuration.md#execution-defaults) and commit them.
