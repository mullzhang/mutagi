# Mutagi documentation

Mutagi runs finite explorations in an existing Git repository, preserves their evidence, and helps people select useful results.
Choose a section by the task you want to complete.

## Getting started

Follow these in order for a first exploration:

1. [Install and update mutagi](getting-started/installation.md): install from a development branch, release tag, or commit.
2. [Prepare a project and start an exploration](getting-started/first-exploration.md): initialize project policy, commit it, check prerequisites, and run one task.
3. [Inspect status](guides/status.md), then [harvest results](guides/harvesting.md): find preserved evidence and decide which parts to use.

For a complete project to copy, use the [runnable example](../examples/README.md).

## Concepts

- [Exploration through harvesting](concepts/exploration-and-harvesting.md): how exploration, preservation, inheritance, knowledge integration, and human adoption relate.

## Guides

| Task | Guide |
| --- | --- |
| Run, inherit, preserve, or resume one exploration | [Experiments](guides/experiments.md) |
| Run several explorations and integrate their knowledge | [Rounds](guides/rounds.md) |
| Continue across a finite sequence of rounds | [Series](guides/series.md) |
| Allow bounded changes to later rounds or set usage thresholds | [Adaptation and usage](guides/adaptation-and-usage.md) |
| Assign a series to automatic recovery after restart or login | [macOS service](guides/macos-service.md) |
| Interpret checkpoints, process observations, and recovery instructions | [Status](guides/status.md) |
| Find saved results, prepare trials or prompts, and record adoption decisions | [Harvesting](guides/harvesting.md) |
| Supervise one external command with a deadline | [Command jobs](guides/command-jobs.md) |

## Reference

- [Project configuration](reference/configuration.md): required fields, explicit execution defaults, precedence, and tracked project files.
- [Commands](reference/commands.md): command families, initialization options, preparation presets, interactive initialization, and execution previews.

## Development

- [Source and test structure](development/architecture.md): package responsibilities, recovery boundaries, shared fixtures, and checks.
- [Distribution verification and releases](development/releases.md): validate package artifacts and Git-source updates, then publish a version tag.
