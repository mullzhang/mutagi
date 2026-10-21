Integrate knowledge from one round in {project[name]} using this independent CLI session.
Read {project[references]} and {project_prompt}, and follow their procedures.
The controller has completed the configured preparation commands.
Integration ID: {state[id]}. Start time (UTC): {state[started_at]}. Shared base revision: {state[base]}.
Branch: {state[branch]}. Requested model: {state[model]}; reasoning effort: {state[effort]}.
Work deadline: {exploration_deadline}. Preservation deadline: {deadline}.
Sources and conditions for this round:
{task}
sources contains this round's targets. prior_sources preserves registrations from earlier rounds; do not count them as planned experiments in this round.
Read observation records, code, inputs, results, and knowledge at the saved revisions. Check the evidence and reproduce results as needed.
Distinguish what you reproduced from explorer reports. Preserve failures, unresolved or unverified findings, and unreviewed decisions.
You may write only to {project[integration_paths]}.
Do not delete unrelated source material or results, and do not count the same evidence more than once.
Do not merge experiment branches or change implementation, shared instructions, execution infrastructure, project settings, past experiments, or other branches.
For experiments absent from the shared revision, use full commit SHAs and paths rather than broken relative links.
Record all targets, verification actors, counts with denominators, operational assessment, reasons for deferral, and conditions for further work in the catalog.
Do not start another exploration, subagent, separate session, or background process.
Do not push or modify repositories and external services outside the specified project.
Run lightweight checks and save with an English Conventional Commit. Do not bypass denied Git operations.
The controller also checks scope and Git state, and publishes only normally completed integration to the shared branch.
{adaptation}
Return the required JSON fields summary, limits, and checks, describing the integration, unverified points, and checks performed.
