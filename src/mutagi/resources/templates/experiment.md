---
id: {id}
started_at: '{started_at}'
mode: {mode}
parent_ids: {parent_ids}
base_commit: {base_commit}
implementation_commit: null
status: unresolved
human_decision: unreviewed
---

# <Name of the question>

The controller fills the identity fields above from the fixed execution state.
Keep those values unchanged; do not generate a new UUID for the record.
Use that same `id` in the folder name, knowledge notes, and references to this experiment.
Name the folder `YYYY-MM-DDTHHMMSSZ_<question-slug>_<UUID>` using the assigned start time and ID.

## Starting conditions

| Field | Value |
| --- | --- |
| Working branch and worktree | |
| Execution session identity | |
| Starting revision of code and knowledge | |
| Paths and hashes of instructions read | |
| Paths, sections, and revisions of notes read | |
| Model and major settings | |
| Budget, stop conditions, and retry limit | |
| Uncommitted changes at the start | |

## Question and observation method

- Candidates considered and reasons for selection:
- Current behavior confirmed in the target code and usage examples:
- This question and its relevance to the project:
- Inherited results and what this exploration newly tests:
- Comparison targets, inputs, and constructed assumptions:
- Observation method and conditions under which the claim does not hold:

## Results and limitations

- Observed implementation revision, inputs, and dependencies:
- Actual results and saved files:
- Verification actor and method (distinguish the author's run, separate reproduction, and human review):
- Checks performed, expected failures of old contracts, unintended regressions, and checks not run:
- Negative results, applicability conditions, and unverified points:
- If direction changed: original question, reason, and retained failures:

## Changes in knowledge

- Notes and sections changed:
- Source material and previous understanding:
- Understanding changed by these observations and the supporting evidence:
- Competing hypotheses, counterexamples, and applicability conditions:
- Reason if no knowledge update was made:

Make evidence traceable to references, specific revisions of project notes, and experiment records.
Do not count multiple notes citing the same evidence as independent confirmation.

## Reproduction and preservation

- Reproduction commands and expected observations:
- Observed implementation commit (saved before this record):
- Inputs, logs, and untracked files to preserve:
- Exit reason and unsaved or unverified scope:
- For retries: attempt number, reason, and budget used:
- Resume point and next questions to pass to the controller:

The controller references this record's final commit from the catalog.

## Human harvesting

- Reusable code, concepts, or counterexamples:
- Adoption decision: unreviewed
- If a human made a decision: target commit, specific part, and their decision:

Do not substitute agent success reports or reproduction checks for a human adoption decision.
