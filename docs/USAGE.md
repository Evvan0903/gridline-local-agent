# Setup and usage

Use the existing `.venv` or install `requirements.txt` into a new one. Run commands
from the project root. Ollama and an installed tool-capable model are needed only
for actual chat/live demonstrations. The default remains `qwen3:8b`.

## Files and sessions

```bash
.venv/bin/python -B -m local_analyst --workspace .analyst/work --load /absolute/path/customers.csv --namespace customers
.venv/bin/python -B -m local_analyst --workspace .analyst/work --load /absolute/path/orders.xlsx --namespace orders
.venv/bin/python -B -m local_analyst --workspace .analyst/work --tables
.venv/bin/python -B -m local_analyst --workspace .analyst/work
```

The interactive commands are `/load PATH`, `/tables`, `exit` and natural-language
questions. Keep a single interactive session for follow-up context. With an absolute
path containing spaces, quote the shell's `--load` argument; enter the plain full
path after interactive `/load`. Files with the same stem need explicit distinct
namespaces. Inspect exact table IDs/column names; filenames and sheet labels are
not SQL identifiers.

Ask the Agent to inspect tables, explain grain, test explicit keys, aggregate or
reconcile data. Column names alone do not authorize business definitions. The
SQL tool allows one validated SELECT/WITH with bound parameters; it blocks writes,
external file/table functions and implicit cross joins. An explicit ON clause does
not prove semantic validity or prevent every multiplicity problem; inspect warnings.

For deterministic CLI queries, copy an actual ID from `--tables`:

```bash
.venv/bin/python -B -m local_analyst --workspace .analyst/work \
  --sql 'SELECT COUNT(*) AS row_count FROM "ACTUAL_REGISTERED_TABLE_ID"'
```

## Analysis versus exports

Ordinary questions answer in chat with evidence. Persistent audit JSON is internal
metadata, not an automatic CSV/XLSX/PDF deliverable. Only an explicit request such
as `Export this result to CSV.` enables that format this turn. For complex phrasing,
use `--export-format csv` (repeat for excel/pdf) or Python
`agent.ask(question, export_formats=["csv"])`.

Exports go to `<workspace>/outputs/<new-name>/`, with lineage/report sidecars.
Never reuse an old query after a dependency changes: recalculate, query again and
use the newly returned handle. Existing export directories are protected. Generic
Excel query exports are static result snapshots. A requested dynamic/auditable
model needs a dedicated, verified formula/refresh exporter; the Agent reports this
limitation instead of silently creating a snapshot.

## Reviewed models and demonstrations

`--plan examples/collections/plan.json` registers/runs an explicit JSON policy; first
load its CSV using namespace `collections`. The printed `plan_id` can be passed to
Agent model/scenario tools. Only declared overrides are allowed. Free text from
`inspect_rules` is evidence, not an automatically approved calculation plan.

The current deterministic end-to-end example is:

```bash
PYTHONPATH=. .venv/bin/python -B reports/validation/query_versioning/demo.py \
  --output /tmp/unique_analysis_demo
```

It scripts the model messages but runs real tools, validates 230, 35 → 70 and stale-ID
rejection, and produces explicit CSV evidence. It does not claim live Qwen autonomy.
The collections sample runner has a fixed historical output location; the cleanup
smoke harness runs an exact copy in an isolated layout so preserved results cannot
be overwritten. Benchmark generator commands must use fresh output/report paths.

[Live model harness instructions](../examples/QWEN_DEMOS.md) preserve fixed questions
and engine hashes. Its historical wording about the older configuration-only release
is dated; consult the current protocol's `version_note` for the newer versioning release.
No model download is part of these workflows.

Full registered-table/row-count requests use metadata directly without SQL or model
inference. `inspect_schema({})` also supplies the counts when doing other work.
Check the API result status and `completion.reasons`: `answered` means bounded
checks passed, while `incomplete` and `blocked` explicitly withhold unsupported
completion. See [reliability contracts](RELIABILITY.md) for covered checks and limits.
