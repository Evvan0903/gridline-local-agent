# V3 reliability hotfix

Offline engineering repair only. Historical model traces, sources, business rules and scores remain immutable. No new real model run is authorized by this change.

## Financial DSL diagnostics

`FinancialPlanError` is a `ValueError` with an `errors` array. Each issue carries an error code, JSON path, expected/received type and actionable guidance. Duplicate IDs additionally identify the conflicting name and its namespace. Fields use `{"field":"amount"}`; parameters use `{"param":"rate"}`. `{"mul":["amount","rate"]}` is invalid because bare strings are numeric literals, never references. Numeric strings remain supported. Input fields and previous calculation IDs share one calculation namespace; forward references, unknown references, unsupported operators and invalid arity are rejected before compilation. No proposal is rewritten.

## Reviewed payable/currency contracts

Python `execute(..., financial_contracts=[...])` accepts explicit contracts. The existing web workflow's `acceptance_contracts` array can contain the same `kind: financial_rowset` objects, alongside existing scalar sum/count contracts. No new UI or Agent role is introduced.

Example (field names and status meanings must come from reviewed source rules):

```json
{
  "id": "payable_rows",
  "objective_id": "eligibility",
  "kind": "financial_rowset",
  "rule_ref": "Reviewed eligibility and unit definitions",
  "source_name": "records.csv",
  "source_key": "record_id",
  "status_column": "review_state",
  "status_map": {"released": "payable", "pending": "held", "contested": "disputed"},
  "require_payable_only": true,
  "monetary_columns": {
    "fee_usd": {"fixed_currency": "USD"},
    "fee_local": {"currency_column": "currency", "source_currency_column": "currency"}
  },
  "totals": ["fee_usd"]
}
```

The source binds uniquely to a loaded raw file; its key must be unique. Optional `equals` and `date_column/start/end` define an explicit closed-open scope. Unknown classifications are BLOCKED. Payable output must exactly cover payable source keys; held/disputed keys cannot enter outputs, and extra/missing/duplicate keys fail. Full classified source records are preserved in a hashed workspace audit JSON. Source/query versions are checked. Aggregated-only queries without row IDs cannot prove row eligibility.

Call `validate_financial_rows(query_id, contract_id)` for evidence. If contracts are configured, definition, build, snapshot export and PDF export cannot bypass eligibility. Input coverage is checked before calculation; currency checks apply to the evaluated output. A workflow currently needs one unambiguous output contract; split different scopes rather than guessing which applies. No-contract proposals remain conditional with `semantic_validation.status=BLOCKED`; they are not eligibility approval, and unconfigured semantic objectives remain BLOCKED at completion.

Each requested total needs a currency binding. Global sums across unlike currencies fail. `aggregation: per_currency` permits separate audit subtotals, but never authorizes the existing workbook compiler's global SUM of that mixed column. The DSL/workbook arithmetic engine is unchanged. Explicit conversion requires nonempty `rule_ref`, `reporting_currency`, `source_amount_column`, `source_currency_column`, `rate_column`, `operation` (`multiply`/`divide`) under the amount's `conversion` field. Rates and amounts are checked against actual source records. Output currency relabeling without conversion fails.

These checks prove row-set/currency conditions, not all financial mathematics: results explicitly return `numerical_correctness: NOT_EVALUATED`. Use a separate numerical contract/verifier. Natural-language eligibility, derived multi-source classifications and arbitrary conversion formulas are not inferred; unsupported evidence is BLOCKED and needs review.

## Deadline, progress and context

Exactly Qwen3.8:27B extended-local remains 3600 seconds, max 600 seconds per request and no request/tool count ceilings. Before another tool-enabled request the controller requires time for a full 600-second request plus a conservative 600-second final response window. At 1200 seconds remaining or less it requests no-tools final synthesis. This can finish an incomplete attempt before the absolute deadline; it does not extend the deadline or guarantee a model response. Fixed profiles keep their existing limits and 180-second synthesis threshold. The once-only runner's POSIX deadline/cancellation controls remain unchanged.

A failed/timed-out synthesis yields a structured partial result containing current query/version/digest evidence, original objective verdicts, observed artifacts and missing formats. Query validation is explicitly not financial correctness. Existing generated files are not automatically accepted deliverables.

Stagnation fingerprints use tool + structured error code + JSON path. At three repetitions a targeted diagnostic grants one correction opportunity; an unresolved recurrence after the next model turn stops. Calls already batched in the same assistant turn do not consume an opportunity to read that diagnostic. Removing a defect or obtaining new source-bound executed evidence counts as progress. Structural-only and source-free probes do not erase an unresolved DSL defect. Cached schemas and changed descriptions alone do not. A separate repeated-action guard remains; it is not a blanket tool/request ceiling.

Schema cache hits revalidate source dependency fingerprints before reuse. Only exact repeated large tool payloads are replaced in outbound model context by a hash/reference to the first still-present payload. Query/evidence IDs, source versions, objectives, rules and assumptions remain. Full original history/events stay durable. Nothing is inferred about Ollama context truncation from token counters.
