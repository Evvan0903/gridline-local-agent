# Historical reports and unique evidence

Existing analytical Markdown stays at its original path. Some files are both a
historical record and a current reference; dates and scope matter more than whether
their tests were green. No FAIL finding is discarded merely because a newer suite passes.

| Document | How to use it now |
|---|---|
| [Workbook relationships](../reports/workbook_relationships.md) | Workbook schema/grain/key evidence; generated benchmark default path remains active |
| [Phase 3A revenue model](../reports/revenue_data_model.md) | Account/Shift/NR/GSV methodology and reconciliation; current preserved base-model reference |
| [Model comparison](../reports/model_comparison.md) | Historical measured simple-question runs; wording such as “current version” means its recorded version |
| [Root-cause diagnosis](../reports/root_cause_diagnosis.md) | Preserved failed behaviors, harder model comparison, exception/Excel/July/autonomy findings |
| [Query versioning release](../reports/query_versioning_and_analysis_mode.md) | Later stale-ID fix and answer-first tests; does not close unrelated financial blockers |
| [Cleanup verification](../reports/project_cleanup.md) | Inventory, moves, hash preservation and current smoke/test results |

Historical run batches live in `archive/runs/`. Original timestamp paths under
`output/` are relative symlinks so report links, JSON paths and hashes still work:

- [Acceptance evidence](../output/final_acceptance/1792135171879295000/)
- [Paired model comparison](../output/model_comparison/1792134174154974000/)
- [Root-cause diagnostics](../output/root_cause_diagnosis/1792193336929224000/)

These contain failed and initial attempts, exceptions, independent checks, original
HTTP logs and source snapshots. Do not edit them to represent later successes.
Snapshots are evidence, not supported application entry points. Known references
to disposable `/private/tmp` workspaces may no longer resolve; they predate cleanup
and were not rewritten. Reproduce supported workflows using current examples and
new output directories.

Two old Markdown files, `reports/final_acceptance_audit.md` and
`reports/final_acceptance_summary_zh.md`, were already absent before this cleanup;
the root-cause report records that gap. Their source audit evidence is preserved.
No replacement pretending to be the original report was fabricated.

The prior/current engine protocols remain in `examples/`; recorded hashes are
intentional reproducibility constraints. Existing comparison logs are unchanged.
