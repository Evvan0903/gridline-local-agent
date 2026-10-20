# Gridline - Local Excel/CSV Analyst Agent

A local-first, tool-using analyst for CSV and multi-sheet XLSX files. Ollama is the
default conversation provider; Python and read-only DuckDB tools inspect data and calculate results.
Clipboard Health is an isolated financial benchmark, not the application's schema.

This is a **source-only GitHub snapshot**. Private case workbooks, governing case
text, commission deliverables, conversations, past approvals and evaluation traces
remain local and are gitignored. Specialized case tests need those separately
supplied inputs; missing inputs are not a passing benchmark. Older references to
`data/`, `reports/`, `output/` and `archive/` describe the original local workspace.
See [release scope and privacy](docs/GITHUB_RELEASE.md).

Ordinary questions return an answer with query evidence. They do **not** automatically
create CSV, Excel or PDF files. Explicit exports validate dataset versions and complete
query results first. Financial rules require a user-reviewed structured plan.

## Start here

- [Project map](PROJECT_STRUCTURE.md)
- [Architecture and tool boundaries](docs/ARCHITECTURE.md)
- [Setup, loading, chat and exports](docs/USAGE.md)
- [Current validation and known limitations](docs/VALIDATION.md)
- [Reliability contracts and independent evaluation](docs/RELIABILITY.md)
- [Clipboard Health benchmark and governing rules](docs/benchmark/README.md)
- [Source-only release scope](docs/GITHUB_RELEASE.md)

## Setup

Run commands from the project root. The existing `.venv` is available locally; the
tested environment uses Python 3.14 and the versions in `requirements.txt`.

```bash
# For a new environment; do not recreate an existing environment unnecessarily.
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Ollama must be running locally with an already installed tool-capable model. The
default remains **`qwen3:8b`**; **`qwen3.8:27b`** is optional. Selection lives in
`ollama_config.json`: `--model` overrides `OLLAMA_MODEL`, which overrides JSON.
`LOCAL_ANALYST_CONFIG` can select a different config file. No model is downloaded
automatically. Tests and SQL do not require Ollama unless explicitly marked live.

Application startup loads the project-root `.env` with `python-dotenv`, including
`OPENAI_API_KEY` when configured. Existing environment variables take precedence.
The `.env` file stays gitignored; never print, log or include credentials in reports.
Loading credentials does not enable a hosted provider or change the local default.

Excel exports use the optional local Node Artifact Tool runtime when available,
otherwise the existing openpyxl fallback. PDF export uses reportlab. The optional
`local_analyst/assets/node_modules` symlink is machine-local, not a vendored dependency.

## Load data and ask questions

The direct application entry point is `local_analyst.cli.main()`:

```bash
.venv/bin/python -B -m local_analyst --help
.venv/bin/python -B -m local_analyst --workspace .analyst/demo --load /absolute/path/sales.csv --namespace sales
.venv/bin/python -B -m local_analyst --workspace .analyst/demo
```

In the same interactive chat:

```text
/tables
Inspect the schema. What was total revenue in June? State the date and amount fields used.
Which customers contributed the most in that month?
Export this result to CSV.
exit
```

Use `/load /absolute/path/another.xlsx` to add another file. The CLI also accepts
repeated `--load` arguments for multiple files; use distinct namespaces for files
with identical stems. Tables receive stable IDs; ask for schemas rather than guessing IDs.

An explicit one-shot export can be requested with:

```bash
.venv/bin/python -B -m local_analyst --workspace .analyst/demo --export-format csv \
  --ask "Calculate June revenue using the relevant schema, then export that result to CSV."
```

Repeat `--export-format` for `excel` and/or `pdf`. Output goes under the selected
workspace's `outputs/<name>/`, with SQL, parameters, versions and lineage. Existing
destinations are not overwritten. Export permission resets on the next question.
The generic Excel export is a **result snapshot**, not a dynamic financial model.

`python main.py` remains compatible and starts the same generic CLI. Its small size
is intentional: it forwards the CLI and preserves legacy tool imports used by tests.
The actual Agent loop, SQL engine and calculations live in `local_analyst/`.

## Tests and reproducible demonstrations

```bash
.venv/bin/python -B -m unittest discover -v

# Generic core tests without the private case workbook.
.venv/bin/python -B -m unittest test_local_analyst test_analysis_mode \
  test_environment_config test_model_comparison -v

# Scripted model responses; real Agent, SQL, financial model and export tools.
# Always choose a fresh output directory.
PYTHONPATH=. .venv/bin/python -B reports/validation/query_versioning/demo.py \
  --output /tmp/analyst_demo_fresh
```

The suite includes independent reliability regressions as well as the original tests;
current counts and live results are recorded in the reliability report.
The legacy live test is opt-in:
`LIVE_OLLAMA=1 .venv/bin/python -B -m unittest test_main -v`. The three fixed model
demonstrations and their version-locked protocol are documented in
[examples/QWEN_DEMOS.md](examples/QWEN_DEMOS.md); they are not needed for routine tests.

Reviewed financial plans can be registered/run with `--plan PATH`. Tier, date, currency,
hierarchy and scenario operations use deterministic tools, not LLM-generated Python.
See the [collections plan](examples/collections/plan.json) for a different schema.
Avoid running legacy generators with default destinations over preserved evidence.

## Verified boundaries

Automated coverage includes CSV/XLSX loading, discovery, read-only SQL and joins,
reconciliation, reviewed rules/scenarios, source lineage, explicit exports, follow-up
questions, and rejection of stale queries after 35 → 70 recalculation. A 100,005-row
regression verifies that complete exports do not use truncated model previews.
See [validation](docs/VALIDATION.md) for actual run evidence and live-test scope.

This is not a completed financial acceptance submission. Remaining issues include
exception-ledger completeness, July coverage checks, existing benchmark Excel
summary/scenario dependencies, and broad autonomous task completion. Prose rules
are not automatically approved executable plans. Derived tables and conversation
history are in-memory; unchanged raw inputs can be reopened, but derived models
must be rerun and queried with new IDs. Semantic interpretation still needs review.

Raw data and existing calculated deliverables are preserved. Historical FAIL reports
remain accessible; passing regression tests do not invalidate their unresolved findings.

Full table/row-count inventories use deterministic metadata without model inference
or SQL. Bounded completion checks return `incomplete` or `blocked` for unsupported
work. These checks do not prove arbitrary business semantics; see
[reliability contracts](docs/RELIABILITY.md).

## Browser workspace

Gridline adds a local React/TypeScript workspace over the existing Python Agent.
After installing `requirements.txt` and running `(cd web && npm ci)`, start:

```bash
./start-web.sh
```

Open **http://localhost:3000**. Create a workspace, attach a CSV/XLSX, select an
installed Ollama model, and inspect actual tools, SQL, evidence and explicit exports.
Dark/light themes and resizable panels are included. Ctrl-C stops this session's
frontend/backend; Ollama stays running. Ports 3000 and 3001 must be free.

Local Ollama remains the default. Optional V2/delivery OpenAI paths apply separate
consent, privacy and spending controls; loading `OPENAI_API_KEY` alone does not
authorize cloud data transmission or paid requests. V3 cloud dispatch remains disabled.
Conversations persist, but derived calculations require recomputation
after restart. See [web usage and security](docs/WEB_UI.md) and
[implementation/validation report](reports/web_ui_implementation.md).

## Gridline V2

OpenAI Responses with explicit cloud-data/spending authorization, a separate restricted
Financial Model Builder, and the Clipboard case benchmark are described in
[Gridline V2 usage and boundaries](docs/GRIDLINE_V2.md). Local mode remains the default.
Unexecuted cloud evaluations are NOT RUN; a conditional financial candidate is not
approved payroll or proof of autonomous case completion.

Gridline V3 adds **Execution modes** (Single Model / Independent Comparison / Multi-Agent), a shared model registry, generic tool profiles and independent acceptance checks. V3 cloud dispatch is disabled; mode OFF is not authorization. See [V3 usage](docs/GRIDLINE_V3.md) and [V3 audit and observed local results](reports/gridline_v3_multi_model_multi_agent.md). Existing services were not restarted; load the new backend normally when ready.
