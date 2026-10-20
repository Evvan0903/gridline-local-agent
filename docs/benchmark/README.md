# Clipboard Health benchmark

This benchmark is isolated in `examples/clipboard/`. It is not the generic Agent's
schema or business policy. The authoritative inputs retain their original paths:

- [Original Excel workbook](../../data/Sales%20Compensation%20Analyst%20Case%20Study.xlsx)
- [Original case-study rules](../../Clipboard_Health_Case_Study_Rules.md), especially Section 07 and Appendix A
- [Explicit benchmark parameters/conditional assumptions](../../examples/clipboard/plan.json)

Reference documents: [schema/relationships](../../reports/workbook_relationships.md),
[Account/Shift revenue model](../../reports/revenue_data_model.md),
[root causes/open blockers](../../reports/root_cause_diagnosis.md).

Preserved deliverables: [Phase 3A manifest](../../derived/phase3a/manifest.json),
[benchmark manifest](../../output/clipboard_benchmark/run_manifest.json),
[Excel model](../../output/clipboard_benchmark/auditable_model.xlsx),
[three-page PDF](../../output/clipboard_benchmark/summary.pdf),
[exception ledger](../../output/clipboard_benchmark/exception_ledger.csv),
[historical acceptance evidence](../../output/final_acceptance/1792135171879295000/).

The 80-person calculation is conditional, not proof of actual paid compensation
or approved ambiguous treatments. Ledger completeness, July coverage and dynamic
Excel summary/scenario dependencies remain unresolved. Preserve supplied first-worked
dates and governing ownership/stage rules. Cleanup changes no rule or formula.

For generator options without modifying outputs:

```bash
.venv/bin/python -B workbook_discovery.py --help
.venv/bin/python -B revenue_data_model.py --help
.venv/bin/python -B -m examples.clipboard.benchmark --help
```

Use fresh explicit `--report`/`--output-dir`/`--output` paths for actual generation.
Default destinations contain preserved evidence. `execute(export=False)` is the
read-only calculation route used during cleanup verification.
