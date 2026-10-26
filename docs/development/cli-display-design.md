# Common CLI display design

Status: implemented. Scope: every public `mutagi` command, including help, errors, handoff, and templates. Execution and runtime contracts remain separate from this presentation change.

## Command inventory and immediate questions

| Surface | Immediate question | Default display | Explicit supporting detail |
| --- | --- | --- | --- |
| Root and group help | Which command solves my task? | Purpose for each group and action; typed options | Action help |
| `init` | What was configured and what should I review? | Created files, scope, review and check steps | `--dry-run` previews; `--json` structured result |
| `check` | Can work start? | Ready/blocked total, failed checks first, next action | `--details` all checks; `--json` results |
| `experiment`, `round`, `series` start/resume/status | What is happening and does it need intervention? | Recorded phase, progress, observation, preservation, actionable next step | `--details` checkpoints/IDs; `--json` original state or completion result |
| `service` install/uninstall/start/manage/pause/tick/status | What is managed and enabled? | Management result, operating checkpoint and next action | `--details` series/checkpoints; `--json` results. Tick preserves its worker behavior. |
| `job` start/status/stop/recover | Is the command running, finished, or awaiting recovery? | Stage, supervisor observation, exit result and next action | `--details` command/process/log paths; `--json` state |
| `harvest` list/show | Which saved result is relevant and reusable? | Compact reference, question, reported outcome; findings and limits before next steps | `--details` full metadata; `show --record`, `show --diff`, `--json` |
| `harvest` checkout/record | What was prepared or recorded? | Trial location or supplied decision and next step | `--json`; fixed source and note references remain available |
| `harvest prepare`, `template experiment` | What can I pass to another tool? | Exact unstyled text payload | Prepare `--output FILE`; no decorative prefix |
| Errors | What failed, and where can I correct the input? | Typer usage errors (exit 2), concise operational errors (exit 1) on stderr | No automatic traceback or state dump |

## Presentation and stream contract

- Public inputs and help use Typer. Human output uses shared Rich title, key/value, table, note, and next-step components. Source content is literal text, never Rich markup.
- Lead with outcome/state, then material limitations and required action, then next steps. Long records, diffs, history, settings and storage paths require an explicit request.
- A clipped source excerpt is labeled as an excerpt and ends with an ellipsis; it is not a generated conclusion. Historical findings, quotations, and saved observations are never rewritten.
- At 60 columns or less, lists use stacked cards rather than compressed multi-column tables. Long values wrap. Raw payloads are never wrapped, shortened or colored.
- Rich detects terminal support; redirected human output has no ANSI escapes. `NO_COLOR` disables color for both help and human output. Machine payloads bypass Rich, even when color is forced in the environment.
- `--json` emits one JSON payload on stdout. Saved-state JSON remains unchanged. Notices and startup/interruption messages go to stderr in machine mode. Reusable dry-run plans, generated prompts, raw observation records, diffs, and templates remain clean stdout payloads.
- Execution completion, successful preservation, explorer-reported observations, controller reproduction reports, and human adoption are separate facts. A terminal phase does not imply a positive observation or adoption.

## Deliberate interface changes

Round/series/service ordered tasks use one repeatable typed option, `--step 'PARENT: TEXT'`: `none` starts an independent task; an earlier zero-based index selects an inherited task. For example, `--step 'none: Compare' --step '0: Test another condition'`. This replaces the interleaved `--prompt`/`--inherit` pair without a second argument parser or option-order reconstruction. JSON `--plan` remains supported and keeps its persisted schema. Callers, examples, and tests move to this interface together; there are no old-name aliases.

Job commands use `job start DIRECTORY --cwd PATH --seconds N -- COMMAND ARG...`, so command flags are unambiguously owned by the external command. The persisted job command and supervisor invocation remain unchanged.

Harvest accepts a full UUID or an unambiguous prefix of at least eight characters. Lists show a unique prefix; JSON and detailed output retain full IDs. Execution and recovery IDs remain full lowercase UUIDv4 values.

## Representative before and after

Examples below are illustrative layouts, not observations or benchmark results.

```text
Before root help:
  {init,check,experiment,round,series,service,handoff,harvest,job,template}
After:
  init        Configure an exploration project
  check       Check readiness and attention items
  experiment  Run, preserve, or inspect one exploration
  harvest     Find, try, and reuse saved results
  ...         Every group has a purpose and action-level help

Before prerequisites:
  OK Runtime: ...
  OK Git: ...
  ...many successful diagnostics...
  BLOCKED Authentication: ...
After:
  Work cannot start · 1 item needs attention
  Authentication  Codex is not authenticated; run codex login and retry
  Next: resolve the blocked item, then run mutagi check
  Details: mutagi check --details

Before harvest list:
  <full UUID> <title>
  <long findings, process details, limitations, full next command>
  ...repeated for every result...
After:
  Saved results · 11 matches
  Ref       Question                                      Observation
  b9aa4d73  誤り率と費用が異なる質問の順序を選ぶ               observed
  ...
  Next: mutagi harvest show REF

Before details:
  <metadata, diff statistics, full observation record, then next steps>
After:
  <question>
  Reported observation: observed
  Findings excerpt: <literal bounded source text>
  Limits excerpt: <literal bounded source text>
  Next: mutagi harvest checkout REF
  Next: mutagi harvest prepare REF --purpose '...'
  More: --details, --record, --diff, --json

Before execution status/completion:
  <many checkpoints, process IDs and saved state JSON>
After:
  Round · <short ID>
  Recorded phase: completed
  Recorded progress: 2/2 tasks
  Knowledge publication: published
  Human adoption: not tracked by execution
  Next: mutagi harvest list
  More: --details / --json

Before error:
  usage: mutagi ...
  mutagi: error: <exception string>
After:
  Usage: mutagi harvest show [OPTIONS] REFERENCE
  Error: no registered result matches this reference
```

## Verification

Use CLI-level parsing/help/error/exit tests, read-only state checks, payload round trips, and Rich rendering at 40, 60, 80 and 120 columns. Verify literal source text containing brackets, Unicode and long paths; redirected output and `NO_COLOR`; stdout/stderr separation; installed entry points, internal workers and saved launchd invocation contracts. Validate list/show/prepare against the committed naedo-forest catalog without changing consumer Git state or adoption data.

## Verified consumer examples

These are real, read-only outputs from naedo-forest at `b299d982cee2ab340ce794c6181da6d6145f98d3`, rendered at 80 columns with color disabled.
The original Japanese findings and limitations are retained as evidence. The separate stderr notice reports older unmarked references.
All 11 marked registrations and saved records were checked, including raw-record equality and prompt generation. HEAD, references, index, catalog, and working tree were unchanged.
The same list was checked at 40, 60, 80, and 120 columns; narrow displays use stacked entries.

```text
Saved results · 11 matches
 Ref             Question                                        Observation
 b9aa4d73        誤り率と費用が異なる質問の順序を選ぶ            observed
 6e298acc        計画の一次提示を利用条件で使い分ける            observed
 8eda224e        推定した終端費用の期待性能と分布の違い          observed
 29ce36c7        回答不能と遅延があるときの待機期限              observed
 f48adac5        表示上限2件と条件判明後の再検索費用             observed
 b11fd223        候補生成の打ち切りと実行不能を区別する証拠      observed
 eafee8a0        少数の到着履歴から選ぶ待機期限                  observed
 743f532c        分割できない配送作業の能力修復                  refuted
 7893e7c7        追加能力の調達上限と修復判断                    observed
 5b29766b        準備時間と納期による作業順序の逆転              observed
 c34fae57        追加依頼の判明を待つ価値                        observed
Next
  mutagi harvest show REF
References identify saved experiments, not adoption decisions. More: --details
or --json.
```

`mutagi harvest show b9aa4d73`:

```text
誤り率と費用が異なる質問の順序を選ぶ
Reported observation      observed
Findings excerpt          36条件を比較し、利用状況によって安価な質問、高精度の質
                          問、質問なしが選ばれた。代表例では条件付きの再確認で期
                          待総費用が102.48から94.32へ減少。知識ノートと指示ハッ
                          シュを記録し、期限前に完了。
Limits excerpt            既知の確率と条件付き独立を仮定した自作例。人間の回答品
                          質や実運用への効果は未確認。human_decisionはunreviewed
                          。最終保存と登録は起動側が担当。
Candidate material        12 changed files; inspect before selecting
Catalog decision excerpt  unreviewed。登録当時は起動側が保存構造のみを確認し、観
                          測の再現は未実施だった。下記に後日の確認を追記
Scoped harvesting notes   0
Next
  mutagi harvest checkout b9aa4d73
  mutagi harvest prepare b9aa4d73 --purpose '<intended use>'
More: --details for metadata and checks; --record for the original record;
--diff for changes; --json for data.
```
