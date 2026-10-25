# Changelog

## 0.2.0 (unreleased)

- Group documentation by getting started, concepts, guides, reference, and development; separate configuration, service operation, and release procedures from the introductory guides.
- Organize implementation and tests into commands, project, execution, and runtime packages; separate shared test fixtures and enforce package boundaries.
- Launch macOS services through the public service command. Pause and uninstall existing LaunchAgents before updating, then reinstall them with the updated package; saved execution IDs and deadlines are retained.
- Generate project configuration and policies with `mutagi init`, including explicit preparation presets, saved model and effort defaults, and an optional interactive preview and confirmation.
- Check prerequisites and recovery commands with `mutagi check`; include the bundled record template automatically in exploration prompts.
- Accept direct or standard-input exploration instructions, construct ordered rounds and repeated series from CLI arguments, and read or override JSON plans.
- Resolve explicit execution defaults from the selected base commit and preview the exact resolved inputs with `start --dry-run`.
- Display readable status for experiments, rounds, series, services, and command jobs. Scripts consuming status JSON must now request `--json`.
- Include a complete Python example and fresh-distribution verification, plus a version-tag release workflow and tested Git installation/update instructions.

## 0.1.0

- Provide single explorations, inherited rounds, fixed-budget series, explicit unattended execution policies, preservation, and human harvesting references.
