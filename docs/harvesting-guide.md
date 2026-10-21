# Find and harvest results

**Harvesting** means choosing useful parts of exploration results, such as code, design ideas, or counterexamples, and bringing them into your own work.
mutagi preserves a catalog, fixed revisions, evidence, and reproduction steps through exploration and integration so people can make that choice.
There is no command that automatically decides adoption. Apply selected results only after defining the target and scope of the change.

## Choose one result from the catalog

Open the experiment catalog specified by the project's `catalog` setting and look for a question and findings relevant to your interests.
Read its record to understand what was learned, where it applies, which parts are reusable, and what remains unverified.
Negative results and counterexamples can inform decisions as well as successful implementations.
You do not need to read every entry.

`unreviewed` means a human adoption decision has not been made; it does not prohibit reading or trying the result.
Distinguish agent-reported checks, separate reproductions, and human adoption decisions.

## Try the saved revision

If a demo exists, run it and compare its conditions with your intended use.
To try code, read the reproduction steps at the saved commit and record path listed in the catalog.
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

## Record decisions and use

Add a short note to the catalog or a related knowledge note for the selected parts.

```text
Target: experiment ID, saved commit, file or section
Selected result: code / idea / counterexample
Reason for selection:
Destination and purpose:
What was tried and its result: state explicitly if not yet tried
Decision: adopt / defer
Unverified conditions:
```

Tie the decision to the specific revision and parts reviewed.
Record any difficulty finding results, missing explanations, or reproduction obstacles you encountered.
Distinguish unread results from deferred ones. An agent checking an entry point does not establish that a human used it.
Neither a decision not to harvest nor incomplete human reading is a reason to stop further exploration.
