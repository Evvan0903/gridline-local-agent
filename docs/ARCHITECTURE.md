# Current architecture

Entry points: `python -m local_analyst` → `local_analyst.__main__` → `cli.main`.
`python main.py` forwards to the same CLI while retaining legacy imports.

The CLI opens a `Workspace`, loads explicitly supplied files and creates an
`AnalystAgent` for chat. The Agent sends bounded schemas/results to local Ollama;
Qwen selects tools, and deterministic Python/DuckDB tools own data and numbers.
`ask()` retains conversation history in that Agent instance. No arbitrary Python
execution tool is exposed.

| Layer | Responsibility |
|---|---|
| `workspace.py` | CSV/multi-region XLSX loading, stable IDs, versions/parents/hashes, read-only SQL, evidence, reconciliation |
| `quality.py` | Types, nulls, key candidates, explicit-key joins/cardinality and anomaly selection |
| `modeling.py` + `finance.py` | User-reviewed JSON plans, SQL/tiers/currency/hierarchy operations, declared scenario parameters |
| `agent.py` | Bounded local chat/tool loop, explicit tool errors, actual query handles, answer-first/export gating |
| `completion.py` | Deterministic inventory intent and bounded source/numeric/evidence/task checks; not a universal semantic validator |
| `exporting.py` | Version/result validation and auditable result snapshots; explicit formula API for dedicated exporters |
| `communications.py` | Evidence linkage helpers; automatic business interpretation/escalation remains limited |
| `config.py` | Exact configurable Ollama tags, CLI/environment/JSON precedence |

Queries store deep-copied dependency versions, SQL/parameters, bounded previews and
a complete-result checksum. Export revalidates the entire parent chain and result.
Stale or unversioned handles require a new query. Audit events, raw load configuration
and registered plan metadata persist locally; derived tables do not persist across
sessions. Source files are read, not rewritten.

Dataset-specific schemas, compensation rules and benchmark assembly belong to
`examples/clipboard/`. That package depends on the generic core. The generic CLI
entry point avoids loading the legacy benchmark wrapper; the core does not import
Clipboard Health rules. Root compatibility wrappers remain for tests/old imports.

Similar discovery implementations serve different contracts: core discovery is
generic, benchmark discovery implements its recorded workbook audit. Phase 3A
revenue and benchmark financial assembly also have distinct grain/lineage duties.
Removing either as a duplicate would require a separate compatibility project.

See [usage](USAGE.md), [validation](VALIDATION.md), [project map](../PROJECT_STRUCTURE.md)
and the unmodified [versioning implementation report](../reports/query_versioning_and_analysis_mode.md).
