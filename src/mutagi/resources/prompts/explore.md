Complete one {exploration_kind} exploration in {project[name]}.
Read {project[references]} and {project_prompt}, and record hashes of the instructions you read.
The controller has completed the configured preparation commands.
ID: {state[id]}
Start time (UTC): {state[started_at]}
Base revision: {state[base]}
Parent IDs: {state[parent_ids]}
Parent record: {state[parent_record]}
Branch: {state[branch]}
Model: {state[model]}; reasoning effort: {state[effort]}
Deadline for exploration and agent-authored records: {exploration_deadline}.
The controller will stop the process at this deadline and use the remaining time for preservation and registration. Finish your record early enough.
After execution, the controller will preserve and register the result by the overall deadline {deadline}.
Consider up to three candidates, then check for duplicates with git show {state[registry_base]}:{project[catalog]}.
Choose your question within the following conditions, implement and observe it, and update knowledge where supported by evidence.
{task}

You may write only to {exploration_paths} and
{project[experiments]}/{folder_time}_<slug>_{state[id]}/.
Create one experiment folder; use lowercase English words separated by hyphens for the question's slug.
Do not change _runner/ inside your experiment folder; it is reserved for controller preservation records.
Changes to generated files are limited to the hash lines allowed by generated_hashes.
Do not change past experiments, execution infrastructure, project settings, shared instructions, the shared catalog, or other branches.
Follow the project prompt. Do not modify external systems or the upstream project, and do not push.
Do not use subagents, separate tasks, background processes, setsid, or nohup. Wait for processes you start and ensure they finish.
First commit the implementation and inputs to fix the observed revision, then preserve its results and README.md.
If Git operations are denied, use approval requests within your authorized scope; do not bypass restrictions.
Do not report unverified work as successful or missing costs as zero. Keep human_decision as unreviewed.
In the record frontmatter, use independent or inherited for mode, a single bracketed line for parent_ids, and the UTC timestamp above in single quotes for started_at.
Record relevant checks, reproduction steps, negative results, and limitations. Preserve what is known even if the work cannot be completed.
Return the required JSON with title, summary, limits, status, the repository-relative record path,
implementation_commit for the observed revision (an empty string if unobserved), and checks describing the checks performed.
The final JSON does not require its own preservation commit ID. The controller fixes the final revision.
