# Exploration through harvesting

[Documentation](../README.md) / Concepts

mutagi helps agents investigate questions, develop results and knowledge, and let people harvest useful parts for their own work.

| Activity | What it leaves behind |
| --- | --- |
| Exploration | An implementation, comparison, or counterexample testing one question, with results and limitations |
| Preservation | Fixed revisions of code, inputs, observation records, and reproduction steps |
| Inheritance | A new question tested using a parent's saved revision, with a reference to that parent |
| Knowledge integration | Updated understanding, applicability conditions, competing hypotheses, and evidence across results |
| Harvesting | Code, ideas, or counterexamples selected by a person, with a decision about their intended use |

## Execution flow

The current CLI runner executes planned explorations sequentially and integrates knowledge in a separate session at the end of each round.
For a series of rounds, the integrated shared revision becomes the next round's starting point.
People can browse and harvest interesting code or ideas while exploration continues.

```mermaid
flowchart TD
    Plan[Fix the base revision, finite plan, and budget] --> Select
    subgraph Round[One round]
        Select{Next planned exploration}
        Select -->|Independent| Independent[Choose a new question from the common base]
        Select -->|Inherited| Inherited[Choose a question from a parent saved in this round]
        Independent --> Observe[Implement, compare, and observe in a new session]
        Inherited --> Observe
        Observe --> Save[Preserve the experiment branch and record; register in the catalog]
        Save --> Verify{Check preservation and registration}
        Verify -->|Incomplete or inconsistent| Recover[Recover before starting another task]
        Recover --> Verify
        Verify -->|More explorations| Select
        Verify -->|All explorations checked| Integrate[Integrate knowledge and assess operations in a separate session]
    end
    Integrate --> Publish{Check publication to the shared branch}
    Publish -->|Ready| Shared[Update shared knowledge and catalog]
    Publish -->|Deferred| Stop[Preserve results and stop]
    Shared --> Continue{Later rounds authorized and budget remaining?}
    Continue -->|Yes| Next[Pass the shared revision and prior records to the next round]
    Next --> Select
    Continue -->|No| End[Finish]
    Save -.-> Harvest[Human browsing, trials, and harvesting]
    Shared -.-> Harvest
    Harvest -.->|Separate request| Adopt[Apply selected results to other work]
```

Solid arrows show execution flow; dotted arrows show optional human actions.
The independent/inherited split concerns starting revisions, not parallel execution.
Time or usage thresholds and operator interruption can stop execution mid-round, followed by preservation.
Successful preservation does not establish successful observation or human adoption.

Updating shared knowledge through integration and adopting selected implementations are separate decisions.
See the [harvesting guide](../guides/harvesting.md) to try results and [configuration](../reference/configuration.md) and [commands](../reference/commands.md) to run explorations.
