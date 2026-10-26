# Find and harvest results

[Documentation](../README.md) / Guides

**Harvesting** means choosing useful parts of exploration results, such as code, design ideas, or counterexamples, and bringing them into your own work.
`mutagi harvest` helps find results, read their fixed records, prepare trials and prompts, and record selective adoption.
It does not start an agent, change a destination project, or decide adoption.
Apply selected results after defining the target and scope of the change.

Run the commands from the exploration repository, or use `mutagi harvest --repo /path/to/exploration-project <action>`.
Reading results and generating prompts require Git and committed project files, but no Codex installation or authentication.
The committed catalog at `HEAD` supplies the source references; uncommitted catalog edits are left alone.
Local `.mutagi/runs/` history and an existing exploration worktree are not required.
Saved Git objects must be available, including after cloning; shallow clones may need the referenced exploration branches fetched.

| Action | Behavior |
| --- | --- |
| `list [--search TEXT] [--details\|--json]` | List compact references, questions, and reported observations; request full findings and references explicitly |
| `show REF [--details\|--record\|--diff\|--json]` | Read findings and limits before next actions; explicitly open metadata, adoption notes, original records, or changes |
| `checkout UUID [--json]` | Create or inspect a detached trial at the exact saved revision |
| `prepare UUID --purpose TEXT` | Print a harvesting prompt to standard output |
| `record UUID --decision adopt\|defer --note-file FILE` | Append a supplied decision and its evidence to the separate `mutagi/harvests` branch |

## Supported catalog registrations

The catalog is a human-readable document. Its metadata contract requires experiment IDs, parents, findings, limitations, saved revisions, and fixed record entry points; it does not require English prose throughout the document.
The reader intentionally supports the registration layouts already produced or preserved by mutagi projects:

- `<!-- forest-run:UUID -->` identifies a controller registration. Both the generated English `Field | Value` table and the existing Japanese `項目 | 内容` table are supported, with their explicit field labels and separators.
- `<!-- forest-experiment:UUID -->` identifies the existing naedo-forest App registrations. Their three identity layouts use `実験IDと形式`, `ID、開始UTC、親`, or `ID、開始UTC`, with the corresponding explicit parent, base, implementation, saved revision, outcome, verification, and adoption statements.

These layouts are covered by [verbatim committed catalog excerpts](../../tests/fixtures/catalogs/naedo-forest.md), including their source revision.
No saved data or historical adoption decision needs rewriting to use these supported layouts.
Explorer checks and controller reproduction reports retain their respective actors.

Each marked registration must have its own heading, table, required nonempty fields, and exactly one fenced `git show <full-saved-commit>:<record-path>` command matching its saved revision.
Missing, duplicate, malformed, inconsistent, or unsupported marked metadata is an error; the reader does not borrow values from the next registration or an integration assessment.
`show` additionally validates the saved observation's metadata and implementation ancestry, including an implementation revision explicitly declared by an App registration.

Older unmarked narratives and saved-record references are not machine registrations.
When these precede marked registrations, the CLI reports their presence on standard error and JSON entries carry `catalog_has_unmarked_references: true`; inspect the original catalog for those older results.
A catalog containing only unmarked saved-record references produces an explicit error rather than an apparently successful empty list.
Registering such historical material would require an explicit, reviewed metadata migration; unknown start times or revisions must not be invented.

## Choose one result from the catalog

Search titles, explorer-reported findings, and limitations with a case-insensitive text query:

```bash
mutagi harvest list
mutagi harvest list --search "cache"
```

Search includes `observed`, `refuted`, `unresolved`, and `blocked` results.
It does not rank scientific validity or infer suitability for a destination.
The list shows a unique prefix for each experiment and one next-action example.
Every harvest action accepts either the full lowercase experiment UUIDv4 or an unambiguous prefix of at least eight characters.
A prefix shared by several experiments is rejected; use a longer prefix or the full UUID.
JSON and detailed output retain full identifiers. Execution and recovery commands still require full IDs.
In the examples below, set `ID` to a selected experiment's UUID or displayed prefix.

```bash
mutagi harvest show "$ID"
```

`show` checks the fixed record's identity, base, parents, reported outcome, and implementation ancestry against the catalog.
The default display shows literal findings, limits, and catalog decision excerpts, plus the number of scoped harvesting notes.
Excerpts may be shortened; they do not reinterpret historical evidence or infer adoption.
Next-action commands precede supporting evidence.
Use `--details` for full findings and limitations, distinct saved/implementation revisions, verification actors, diff statistics, and separately recorded adoption notes.
Use `--record` for the exact original observation record, `--diff` for the full saved changes, or `--json` for structured data.
These output modes are mutually exclusive; raw records and diffs have no decorative prefix, wrapping, or color.
Moving the exploration branch later does not change the selected saved revision.
These structural checks do not independently reproduce observations.

Alternatively, open the experiment catalog specified by the project's `catalog` setting.
Read its record to understand what was learned, where it applies, which parts are reusable, and what remains unverified.
Negative results and counterexamples can inform decisions as well as successful implementations.
You do not need to read every entry.

`unreviewed` means a human adoption decision has not been made; it does not prohibit reading or trying the result.
Distinguish agent-reported checks, separate reproductions, and human adoption decisions.

## Try the saved revision

If a demo exists, run it and compare its conditions with your intended use.
To try code, prepare a detached trial worktree:

```bash
mutagi harvest checkout "$ID"
```

The worktree is created under `.mutagi/worktrees/harvest-<UUID>/` at the catalog's exact saved commit.
The output identifies the trial directory and observation record containing reproduction steps.
The command does not install dependencies, run reproduction commands, or start an agent.
An existing matching trial is reused with its local changes retained and reported.
If its revision or repository differs, the command stops without resetting it.
The project's existing `.mutagi/worktrees/` exclusion must be present and untracked.

Read the reproduction steps at the saved commit and record path listed in the catalog.
Even if the experiment folder is absent from the shared branch, its saved Git revision remains accessible.

```bash
git show <saved-commit>:<record-path>
git worktree add --detach <unused-trial-directory> <saved-commit>
```

In the trial worktree, prepare the dependencies specified in the experiment record and reproduce results using the original inputs.
Then create separately named inputs closer to your use case and compare the results.
Retain the original inputs, observations, and verifier for comparison.
Record which conditions you reproduced and which remain unverified.

## Select the parts to reuse

| What to harvest | Action |
| --- | --- |
| Ideas | Record the idea, applicability conditions, source experiment ID, and saved revision in your design notes |
| Counterexamples or verification methods | Read the inputs and expected results, then adapt them into tests for the target implementation |
| Code | Identify required functions and dependencies, port or reimplement them for the target API, and verify them there |
| A new question | Propose an inherited exploration with the parent ID, saved revision, and additional conditions to test |

Code and ideas can be selected independently.
Partial harvesting does not require merging an entire branch or approving the whole experiment.

## Generate a prompt for the destination

Specify the intended use and optionally select files, exact Markdown headings, or a destination directory:

```bash
mutagi harvest prepare "$ID" \
  --purpose "Keep the existing API and add the selected search method." \
  --kind code \
  --part src/search.py \
  --target /path/to/application
```

Replace `src/search.py` with a file that exists at the result's saved revision.
Use repeated `--part` options for several files or sections.
For a document section, use `--part 'path/to/note.md#Exact heading text'`.
The heading must occur exactly once outside fenced code blocks; its subsections are included.
Without `--part`, the prompt includes the observation record and directs its recipient to choose the relevant parts.
It does not imply adoption of all changed files.

`--kind` accepts `code` (the default), `idea`, or `counterexample` and changes the requested destination work.
`--target` identifies an existing directory and is optional; the destination is explicitly marked unspecified when omitted.
The destination is not modified or analyzed by prompt generation.

Standard output contains only the UTF-8 prompt, with no execution ID or status prefix.
The prompt contains the purpose, scope, fixed source revisions, reported findings and limitations, original observation record, selected text excerpts, and commands for reading source objects.
Binary inputs are identified but not embedded as text.
Source commands require access to the source repository; selected text and the observation record can also be read directly from the prompt.
The prompt asks its recipient to inspect destination instructions, adapt the selected result, perform appropriate checks, and report what remains unverified.

Create a prompt file with `--output`, or redirect standard output:

```bash
mutagi harvest prepare "$ID" --purpose "Evaluate this idea for our design." \
  --kind idea --output /tmp/harvest-prompt.md
```

The output file must not already exist and its parent directory must exist.
Generation creates no run state, worktree, harvesting note, or Git commit.
Save prompts outside the shared working tree when exploration is running, since an uncommitted output file there would prevent the runner's clean-tree checks.
Give the printed prompt or generated file to the person or agent working in the destination project.
That separate work performs the actual changes and verification.

## Record decisions and use

Write a short UTF-8 review note, using the following fields as guidance:

```text
Target: experiment ID, saved commit, file or section
Selected result: code / idea / counterexample
Reason for selection:
Destination and purpose:
What was tried and its result: state explicitly if not yet tried
Decision: adopt / defer
Unverified conditions:
```

Then record the decision against the **source experiment UUID**, selecting the specific scope and destination where known:

```bash
mutagi harvest record "$ID" --decision adopt \
  --kind code --part src/search.py \
  --purpose "Add the selected search method while preserving the API." \
  --target /path/to/application --target-commit HEAD \
  --verification "Destination unit tests passed; large inputs not tested." \
  --note-file /tmp/harvest-review.md
```

`--target-commit` is resolved to a full commit in the supplied destination and requires `--target`.
It records the associated destination revision; the decision command does not apply changes or verify their equivalence to the source.
Omit it if no implementation revision is being recorded.
`--verification` records the supplied report; omitting it leaves verification explicitly unrecorded, even for an `adopt` decision.
Use `--decision defer` to retain a reason for postponement.
Use `--note-file -` to read the note from standard input.
An empty note is rejected.

`record` returns a new note UUID and appends a JSON record to the local `mutagi/harvests` branch.
It stores the source experiment UUID, fixed saved and implementation revisions, original record path, selected parts, result kind, optional purpose and destination, decision, verification report, and exact note text.
If no `--part` is supplied, the structured selection remains unspecified; describe its scope in the note.
The note UUID identifies this decision entry; it does not replace the source experiment UUID in subsequent harvest commands.
Prompt generation does not allocate a note UUID or retain hidden selection state, so repeat the relevant scope options when recording a decision.

Notes are versioned on an independent branch without changing the current branch, index, original observation, or shared working tree.
No dedicated notes worktree is required.
Recording can run while exploration holds its own execution lock.
Git author identity is required; leave `mutagi/harvests` unchecked out when recording.
Concurrent note updates use an atomic reference check; a conflicting writer is asked to retry rather than overwriting another note.

`list` and `show` display notes for the same experiment UUID, saved commit, and record path.
Each note applies to its stated parts and destination; a partial adoption does not mark the entire experiment adopted.
Notes are appended, so later reconsideration retains earlier reasoning.
`show` also retains the original catalog decision text; existing free-form notes are not converted into structured decisions or silently reclassified.

The branch is not pushed automatically.
To share notes, explicitly push `mutagi/harvests`; in another clone, fetch it to the same local branch before reading or extending it:

```bash
git push origin mutagi/harvests
git fetch origin mutagi/harvests:mutagi/harvests
```

Use these commands only when intending to share or retrieve notes.
If local and remote note histories diverge, reconcile them explicitly before publishing.

Tie the decision to the specific revision and parts reviewed.
Record any difficulty finding results, missing explanations, or reproduction obstacles you encountered.
Distinguish unread results from deferred ones. An agent checking an entry point does not establish that a human used it.
Neither a decision not to harvest nor incomplete human reading is a reason to stop further exploration.
