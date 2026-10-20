# Current validation and limitations

The reliability audit and current acceptance decision are recorded in
[the final report](../reports/agent_reliability_final_report.md). Scripted component
tests, real local-model runs, and independent business acceptance are separate.
A green component suite is not approval of arbitrary financial answers.

The earlier cleanup verification is [reports/project_cleanup.md](../reports/project_cleanup.md),
with raw test/smoke evidence under `reports/validation/cleanup/`. This is a repository
organization check, not a new financial acceptance claim. That historical suite contained 89
tests, including one legacy live test skipped unless `LIVE_OLLAMA=1`. Its cleanup
run recorded **88 passed, 0 failed, 1 skipped**, plus **12 successful CLI/sample
commands** and a separate **Qwen3:8B live count question PASS**. All 80 baseline
and 80 scenario employee amounts matched the preserved conditional results.

| Capability | Confirmed scope |
|---|---|
| CSV/XLSX discovery and loading | Arbitrary local schemas, multiple tables/sheets, null/type/key candidates; structural, not business semantics |
| DuckDB querying and joins | Read-only SQL, parameters, explicit joins/cardinality warnings; composite/range/derived joins may need explicit review |
| Rule-driven calculations | Deterministic supported operations using user-registered JSON plans; ambiguous/missing rules block |
| Reconciliation and scenarios | Scalar evidence, duplicates/unmatched metrics, declared overrides and genuine dependency recalculation |
| Evidence/export safety | Versioned dependencies, original hashes, complete-result checks; stale 35 → 70 handle rejected |
| Answer-first/follow-ups | Scripted-message integration with real tools; no unrequested CSV/XLSX/PDF; export permission resets |
| Scale | 100,005-row regression; bounded LLM preview, full export verified; not unlimited/out-of-core execution |
| Clipboard results | Preserved conditional 80-person calculation and source lineage; not actual-paid approval |

## Still unresolved

- Exception ledger is not fully quantified/attributed for all material treatments.
- July coverage/preconditions and a complete verified July update are missing.
- Existing benchmark Excel summary/scenario dependencies are not fully dynamic.
- General autonomous task completion and semantic interpretation are not guaranteed.
- Prose-to-approved-model automation is unsupported; human rule/assumption approval remains necessary.
- Derived data/history are not restored as an entire conversation across processes.
  Raw load/evidence metadata persist; derived calculations need fresh runs/query IDs.
- Reconciliation parameter sharing and finite tolerance are now directly tested
  and repaired. Communication linkage/escalation still requires case-specific
  evidence review.
- Completion checks validate bounded actions, sources, handles, versions and a
  directly requested primary scalar. Free-text numeric tokens are advisory;
  correct numbers and SQL aliases do not prove financial or business meaning.
- Current exact-decimal CSV loading preserves lexical amounts within decimal
  precision; XLSX floating inputs need explicit casts for exact decimal arithmetic.

The stale-export bug documented in the older root-cause report was subsequently
fixed by the [versioning release](../reports/query_versioning_and_analysis_mode.md).
That does **not** close the other findings. Dedicated benchmark Excel/PDF files
and historical FAIL evidence were preserved, not silently regenerated.

## Model evidence

The existing configured default remains `qwen3:8b`; it was not changed by the
audit. See the final report for current paired results and task-specific use of
`qwen3.8:27b`. The historical three
simple questions passed for both; the later two harder controlled tasks completed
0/2 for 8B and 2/2 for 27B on their recorded common engine. Those are small historical
samples, not current-version universal reliability or performance guarantees.
See [history](HISTORY.md) for the measured reports and source transcripts. Those historical cleanup results did not change prompts, tools, models or
financial formulas; the later reliability audit has its own versioned evidence.
