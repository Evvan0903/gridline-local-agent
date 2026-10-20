# Gridline V3

V3 adds a general local workflow controller alongside the preserved V2 chat and explicitly labelled assisted Clipboard benchmark. No new service is installed or started by the implementation. Restart the existing application normally when ready to load new backend routes; startup must not kill occupied ports.

## Modes and model metadata

Open Execution modes in the existing UI to find the V3 configuration. Choose **Single Model**, **Independent Comparison**, or **Multi-Agent**. Role assignments and mode can be saved. The registry is shared by backend/frontend (`GET /api/model-registry`); local availability is discovered through loopback Ollama with hosted/cloud tags excluded. Models are never downloaded, renamed or substituted. Unknown native limits and structured-output capabilities stay unknown. Cloud pricing is the existing versioned estimate, not a new live billing observation.

V3 cloud dispatch is deliberately **NOT RUN**, including Luna/Sol roles in a comparison or collaboration. The current development task permits no cloud inference. Turning Confidential Mode OFF does not authorize it. Existing V2 source-specific consent, privacy blocking, spend reservation and zero-retry controls remain unchanged. Future cloud integration must use a separately approved source/model scope and one aggregate ledger budget across every role/run; the current disabled V3 boundary must not be bypassed by a provider factory from an HTTP request.

## Generic tools and conditional plans

The default **General Analyst** profile contains only source/schema, quality, explicit join checks, safe SQL/binding, query evidence, reconciliation, generic formula-builder and PDF tools. No case adapter, arbitrary Python, file browsing, preselected Slack answer or oracle is exposed.

`set_task_plan` records proposed steps without deleting original objectives. `define_financial_plan(spec, query_id)` validates a restricted expression specification against a current source-backed query and the user's supplied governing text. It is a **conditional model proposal**, not business approval. The existing builder compiles formulas and checks saved numeric caches against Decimal. Source preparation still requires correct SQL and independently validated rule interpretation. Model assumptions cannot approve payroll.

`validate_sql` performs the existing read-only/source/function AST checks and a local DuckDB EXPLAIN bind. Missing/ambiguous columns and type errors return available-column feedback before executing the data query. Syntactic/binding success is separate from financial correctness. `validate_workbook` only changes finite numeric parameter cells in a validation copy of a generated artifact identified by its hash. `export_report` requires explicit PDF permission and a current source-dependent query; generated prose remains subject to independent review.

**Assisted Financial Workflow** remains a separately labelled, fixed original Clipboard workflow with predetermined rules/Slack/artifact logic and the historical 8/24 limits. **Deterministic Reference** requires the exact original source hash and full rule text; it computes locally, compares the existing independent conditional oracle, and makes no model-performance claim. Neither is available as a General Analyst tool. Other datasets cannot silently route into them.

## Controller and validation

Original objective IDs, requirements, dependencies, requested artifacts, evidence references, validation results and blocking reasons persist. Model `COMPLETED` assertions are held at IN_PROGRESS until deterministic checks pass. Unconfigured business semantics remain BLOCKED. A generated file alone cannot complete a multi-question task.

An optional reviewed scalar acceptance contract can verify simple single-table sum/count questions using independent pandas/Decimal calculations. It binds source filename, field, operation, unit, equality/date filters and scalar output column. Only exact supported AND filters/aggregate forms pass. For example:

```json
[{"objective_id":"o1","source_name":"fees.csv","operation":"sum","amount_column":"amount","output_column":"total_usd","unit":"USD","equals":{"status":"approved"}}]
```

This is not arbitrary rule execution or a universal language proof. CTE/multi-table business semantics, complex conditions and unconfigured objectives need additional independent validators. A SQL alias such as `COUNT(*) AS approved_commission` cannot satisfy a monetary SUM contract even if the numbers happen to agree.

Single Model and Multi-Agent use a shared maximum of eight model requests and 24 tool calls. Multi allocates one request to Planner, up to six to Analyst, one to Reviewer. All role calls count. The final available request of each role has no tools and explicitly requests truthful synthesis. There is no handoff loop or automatic rework. Cancellation and wall time are cooperative at request/tool boundaries; the existing local request timeout can delay cancellation. Three Qwen roles are one foundation model in three logical roles.

Independent Comparison receives equal per-model limits, fresh workspaces/providers/histories and artifact directories. Each output is frozen before comparison; neither model receives the other's work. The combined number of local requests can be twice the per-competitor limit. Only after both terminal results are available are side-by-side deterministic checks displayed. No winner is inferred from confidence or agreement.

## Persistence and APIs

- `GET /api/model-registry`
- `POST /api/workflows`: workspace, selected source IDs, original task/objectives/rules, mode/role models, explicit formats, profile and bounded limits.
- `GET /api/workflows?workspace_id=...`, `GET /api/workflows/{id}`: saved configuration, progress, role attribution, frozen answer/trace, validation and artifacts. Recovery displays saved evidence without inventing derived in-memory calculations.
- `GET /api/workflows/{id}/artifacts/{sha256}`: only frozen, matching-hash artifacts confined to that workflow directory.
- Existing `POST /api/runs/{id}/cancel`: safe-boundary cancellation.

The original cookie/CSRF/origin/path checks apply. Source IDs must belong to the selected workspace and still match loaded hashes. Settings merge partial updates so appearance changes do not reset saved role assignments or confidentiality.

## Reproducible offline/local evaluation

`examples/v3_benchmark.py` creates independent Orders XLSX, duplicate Customers CSV and incomplete FX CSV fixtures. The model sees source data and rules, never independent expected totals. It must investigate the duplicate join trap, missing FX and June recognition, produce SQL, a conditional generic financial specification, live Excel and a PDF. Independent checks reopen actual files and natively recalculate a changed scenario rate in copies. Mocked integration success is not a live model result.

`python -m examples.v3_local_eval` is the once-only entry for the specifically authorized local evaluation directory. It removes the OpenAI key from child environment, permits only loopback port 11434, disables OpenAI provider completion and records network/resource evidence. It refuses to overwrite an existing evaluation. It does not restart the prior Luna attempt. Heavy model configurations run sequentially; only caches loaded by this evaluation may be released. No model service is created or killed.

Current observed results, precise limitations and historical comparisons are reported separately in `reports/gridline_v3_multi_model_multi_agent.md`. Never combine historical assisted full-case scores with V3 generic synthetic scores as one accuracy ranking.

## Release versus frozen live observations

The A/B/C live runs are immutable evidence of the first frozen V3 tool contract. They all failed or blocked before delivering a workbook/report. Afterwards, offline-only fixes added the full `version`/`rule_ref` JSON schema, conditional-only proposal status, structured error feedback, exact evidence-reference checks, tool-markup rejection, remaining-time transport timeouts and explicit role ceilings. No live inference was repeated on that revised source. Do not claim those fixes improved measured model performance.

The default shared limit remains eight requests. Newly configured **local multi-role** tasks can choose an explicit total ceiling up to 32 with per-role ceilings whose sum fits that total; Planner/Reviewer remain one-pass roles and unused capacity is not silently redistributed. Single/comparison remain capped at eight per competitor. The evaluation used the original defaults, never the larger option. Tool/runtime/cancellation limits remain bounded/cooperative; no hard operating-system process deadline is promised.

A scripted integration example under `output/gridline_v3/scripted_generic_pipeline` creates and independently verifies a generic formula workbook without the Clipboard solver. It is **not a foundation-model performance result**. The separate formatted copy preserves all values/formulas while showing parameter precision accurately. Frozen raw traces and raw artifacts remain unchanged.

Simple acceptance contracts do not replace a reviewed rule system, complete period data, currency/ownership policy or payment evidence. New business semantics can remain BLOCKED. The historical V2 completion limitations are not retroactively declared fixed or re-scored.
