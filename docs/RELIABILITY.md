# Reliability contracts and evaluation

The audit extends the existing local Agent while preserving financial formulas,
original inputs and historical benchmark deliverables. Actual acceptance results
and separate live results are in `reports/agent_reliability_final_report.md`.

## Metadata and tools

`inspect_schema({})` returns every registered table's ID, label, exact row_count
and version_id, plus table_count and an inventory evidence ID, without SQL.
Single-table inspection adds types, null/duplicate counts, key candidates and up to
three sample rows. Versions are checked and metadata is defensively copied.
Duplicate CSV headers require explicit renaming. Fractional CSV columns retain
finite lexical decimal values when they fit DuckDB decimal precision (38 digits);
exact calculations over floating XLSX fields must cast before arithmetic.
Changed parsing produces new data versions; obtain new query IDs rather than
reusing pre-change CSV evidence.
Persisted schema evidence is reusable only after dependency versions are checked.

Unambiguous full table/row-count inventories use this function directly and render
all entries without Ollama. Additional analyses or exports use the normal tool
loop. The current registry is refreshed each turn.

`query_sql.max_rows` is an integer 1..100, default 30. Zero, booleans, floats and
strings are rejected. It bounds the preview, not the full-result digest or export.
`result_digest.row_count` counts query-result rows, not source rows. Constant SQL
is allowed but cannot prove a source table's contents.

Reviewed models return output schemas. Scenario metrics must identify a real step
and column before the alternative runs; results identify the measure and versions.
AST parameter extraction ignores dollars inside SQL strings/comments.
Reconciliation binds parameters per statement, rejects unused names, and requires
a finite, nonnegative tolerance.

## Completion and recovery

Results retain `status=answered|incomplete|blocked` and add a completion scope and
reasons. Inventory uses `deterministic_metadata_inventory`; model answers use
`bounded_structural_and_primary_results`. Checks cover current source evidence,
a directly provable primary scalar result, known handles, explicit supported task
checks, exports and preview coverage. Extra prose numeric tokens are advisory,
not a semantic proof or a reason on their own to block a correct answer. Blocked calculations cannot be called completed, and an
undelivered tool result does not count as model-visible evidence.

These are bounded checks, not proof of SQL/business meaning or arbitrary task
decomposition. Matching numbers does not prove their meaning; primary scalar presentation checks
allow 0.005 rounding tolerance. Independent oracles and approved rules remain
necessary for financial acceptance. See the final report for measured limitations.

Dependent reads wait for changed source/model versions; exports wait for actual
query handles from a previous assistant turn. Independent reads may be batched.
Explicit instructions not to run SQL disable SQL-bearing tools for that turn.
Two identical tool errors stop no-progress retries. Limits remain eight model
steps, 24 calls per turn, eight calls per response and 180 seconds per HTTP request.
Transport/malformed-response failures return incomplete with preserved trace/error.
No cloud fallback, downloads, unlimited retries or background monitors exist.

## Reproduction

```bash
# Always choose fresh output directories.
.venv/bin/python -B -m examples.reliability_audit --output /tmp/analyst_units_fresh --unit
.venv/bin/python -B -m examples.reliability_audit --output /tmp/analyst_live_fresh --fixtures /tmp/analyst_shared_fixtures --live
```

The live suite uses exact installed tags sequentially, paired initialization and
the same prompts/tools/context/limits. It retains HTTP/tool traces, actual SQL,
artifacts, first-payload hashes, runtime and memory observations. Inventory cases
are marked `inference_executed=false`, not counted as model reasoning. Scripted
component results are reported separately. Oracles check executed rows, field
mappings, source versions, Decimal amounts and actual artifact contents. Historical
substring scoring is preserved only for historical comparison.

CSV/Excel exports contain complete validated snapshots. PDF tables explicitly
preview at most 15 rows. Dynamic financial workbooks need a dedicated verified
exporter. Historical exception attribution, July coverage/approval and dynamic
Excel dependencies remain separate findings unless directly closed by new tests.
