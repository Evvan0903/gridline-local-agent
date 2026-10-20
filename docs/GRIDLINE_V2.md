# Gridline V2

Start from the project root: install `requirements.txt`, run `npm ci` in `web/`, then `./start-web.sh`. Default inference stays local. Restart an already running backend to load V2; startup never kills an occupied port.

## OpenAI and authorization

The official Python SDK implements Responses for `gpt-6-luna` and `gpt-6.1-sol`. No other cloud model or automatic fallback is enabled. `OPENAI_API_KEY` loads from the project-root `.env` with `override=False`; an existing environment value, including an empty one, takes precedence. `.env` stays ignored and the backend never returns the key. Do not put a key in a prompt or a reviewed plan.

Selecting cloud does not send inference requests. Sending without approval opens a disclosure. Both paid execution and source-derived content authorization are required, bound to the conversation, model, source versions and reviewed plans. Reauthorize after source changes. Raw source files remain local; prompts, schemas, bounded tool results and necessary excerpts can leave the machine after authorization. No hosted execution, file search or file upload tools are configured.

Requests set `store=False`, request encrypted reasoning continuity, and replay original response items and matching function-call IDs. This disables stored response application state; it does **not** enable Zero Data Retention or eliminate abuse-monitoring retention or prompt-cache retention. See [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data) and [reasoning continuity](https://developers.openai.com/api/docs/guides/reasoning).

Responses function tools use the flattened API shape and explicit `strict=False`. Open-ended SQL parameter/override maps cannot use a closed strict schema. The existing local tools validate every invocation; optional fields remain optional. See [function calling](https://developers.openai.com/api/docs/guides/function-calling).

SDK automatic retries are disabled, request timeout is 90 seconds, maximum generated tokens (including reasoning) is 4,096, reasoning is low by default and service tier is Standard (`default`). Local Ollama keeps its existing 180-second timeout, context, tool/step budgets and CLI behavior. Stop is cooperative at request/tool boundaries.

## Cost accounting

Persistent SQLite ledger: `.analyst_web/spend.sqlite3`, private file permissions. Every Responses request reserves a conservative UTF-8-byte input bound plus protocol allowance and maximum output. Input/cache writes use the higher cache-write rate for reservation. Requests over the conservative 200k input bound are rejected, so long-context price uplifts are outside this application mode.

Reported input, cached, cache-write, output and reasoning tokens are recorded. Reasoning tokens are part of output and are not charged twice. SDK field `input_tokens_details.cache_write_tokens` uses the cache-write rate. Usage-priced costs are estimates under a versioned tariff, not invoices. Raw sanitized request/response/error data are retained locally in the ledger for diagnosis; authorization headers are not recorded. API error text is not exposed to the browser.

Pricing version `2026-10-17-standard-text-v1`, USD per million tokens:

| Model | Input | Cached | Cache write | Output |
|---|---:|---:|---:|---:|
| [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) | 0.10 | 0.01 | 0.125 | 0.50 |
| [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) | 2.00 | 0.10 | 2.50 | 10.00 |

Default per-run cap $0.25; session/application caps $2.00; separate Sol total cap $1.00. The smallest applicable remaining cap controls the next reservation. Eight model requests maximum per run. Unknown/failed requests retain their full reserve and block further cloud requests until independently reconciled. Restarting does not clear spending or orphaned reservations. **Application caps are not guaranteed provider billing ceilings.** A provider invoice is not available in the application.

Sol is restricted to the full benchmark, requires current offline readiness plus prior actual local and authorized Luna attempts, and receives separate explicit paid/data approval. An existing Sol request blocks a new Sol benchmark attempt; there is no automatic rerun or escalation. No connectivity request is necessary merely to test SDK plumbing. A budget rejection reports `BUDGET_EXCEEDED` before sending another request.

## Financial Model Builder

`local_analyst/financial_model.py` compiles validated structured expression trees. Supported operations: numeric constants, input field references, declared parameters, add/subtract/multiply/divide, min/max and explicit half-up rounding. Named calculations must reference earlier calculations; unknown fields, forward references, arbitrary formulas, duplicate/missing source keys, unresolved assumptions, nonfinite values and arithmetic errors reject the build.

A user-registered plan can contain `financial_model` with `version:1`, `rule_ref`, `source_key`, `parameters`, ordered `calculations` and `totals`. The Agent's `build_financial_model(plan_id, query_id, name)` requires explicit Excel export permission and a current source-dependent query from an earlier turn. Static `export_result` is preserved and remains a snapshot.

The builder separates Inputs, Parameters, Build, Summary and Reconciliation. It records expression dependencies, query/version/source evidence and a spec hash; reopens the XLSX and compares saved formula results to an independent Decimal evaluator. The source code and requirements accompany the model. The bundled Artifact Tool runtime supplies formula calculation; an absent calculator cannot be silently replaced by formula strings without numeric verification.

Parameters update native Excel detail and summary formulas. Source imports, revenue preparation, ownership, employment/ramp and management schedules are explicitly Python snapshots: edit the original source only through its normal workflow, obtain complete coverage, rerun the reviewed calculation, and export to a **new** directory. Changing period labels alone is not a refresh. The Clipboard adapter rejects July because the necessary worked-period rows are absent; source presence in June is not an attestation of completeness or payroll approval.

## Benchmark

The Benchmark UI selects a loaded XLSX and uses the complete original Clipboard rules document. Only this original full-case benchmark is configured; arbitrary alternative rules are not interpreted automatically. The A-J task list and selected models are visible. Without both paid and case-data approval, cloud selections are recorded NOT RUN with zero requests; local selections still run. The confirmation displays model/data exposure and budget caps. Inference runs sequentially, with one attempt per selected model. Stop prevents further model/tools at safe boundaries.

Actual events and SQL/tool evidence persist in the report. Excel/PDF downloads exist only for artifacts a model actually produced; downloads verify source and artifact hashes. A deterministic candidate generated outside a model attempt is separate evidence, not model success. Task status and independent accuracy are separate; numerical agreement alone cannot pass the four-question challenge.

CLI, no paid cloud execution:

```bash
.venv/bin/python -B -m local_analyst.benchmarking \
  --workbook 'data/Sales Compensation Analyst Case Study.xlsx' \
  --rules Clipboard_Health_Case_Study_Rules.md \
  --output output/gridline_v2/comparison_NEW \
  --models qwen3:8b qwen3.8:27b gpt-6-luna gpt-6.1-sol
```

Cloud entries in this CLI command remain NOT RUN. Paid full-case execution uses the explicit browser authorization controls. Never select Sol for routine debugging. One subsequent human-approved Luna synthetic-only attempt has a $0.25 cap; current preflight is blocked by missing credentials. No case-data or Sol authorization has been granted.

Create the deterministic conditional case candidate independently:

```bash
.venv/bin/python -B -m examples.clipboard.v2 \
  --workbook 'data/Sales Compensation Analyst Case Study.xlsx' \
  --rules Clipboard_Health_Case_Study_Rules.md \
  --output output/gridline_v2/candidate_NEW --month 2026-06-01
```

This produces an auditable conditional workbook, source schedules/code, three-page PDF and separate question validation. Actual payment evidence and unapproved interpretations remain explicit blockers. Exception counterfactuals are named, isolated and non-additive; an ordered waterfall reconciles interactions. Missing-owner opportunity cost is not silently zero.

Tests:

```bash
.venv/bin/python -B -m unittest discover -q
npm --prefix web test -- --run
npm --prefix web run build
```

Neither test command makes paid model requests. The SDK tests use `httpx.MockTransport` and dummy credentials. A real authorized Luna run and any Sol trial are separate NOT RUN items until executed.

## Confidential Mode

Workspace settings now expose Confidential Mode, OFF by default. OFF permits authorized source-derived records and relevant messages; it does not authorize cloud inference or anonymize data. The selected provider/model, source versions and budget still require separate consent. No raw workbook is uploaded by the function-only transport.

ON currently blocks all cloud requests because strict minimization is incomplete. This is enforced by the backend and provider before SDK invocation/reservation, including a persisted ON setting even if a client sends OFF. Local analysis remains available. Mode and cloud authorization are independent. Application secrets from credential-like environment variables are redacted from context/audits; changing privacy mode does not relax read-only SQL, filesystem, versioning, export, completion or cost controls.
