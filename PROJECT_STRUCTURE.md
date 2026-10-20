# Project structure

```text
local-excel-agent/
├── README.md                         Start here
├── PROJECT_STRUCTURE.md              This map
├── main.py                          Generic CLI + legacy import compatibility
├── workbook_discovery.py            Compatibility wrapper for benchmark discovery
├── revenue_data_model.py            Compatibility wrapper for benchmark revenue
├── local_analyst/                    General-purpose implementation
│   ├── __main__.py, cli.py           python -m local_analyst / argument handling
│   ├── agent.py                     Bounded Ollama tool loop and export authorization
│   ├── completion.py                Bounded completion/evidence checks; semantic limits
│   ├── config.py                    Shared model/config resolution
│   ├── workspace.py                 Load/register/version data; read-only DuckDB/evidence
│   ├── quality.py                   Schema/key/join/exception primitives
│   ├── modeling.py, finance.py       Reviewed plans, deterministic rules/scenarios
│   ├── communications.py            Evidence-linking helpers with known limitations
│   ├── exporting.py                 Explicit CSV/Excel/PDF exports
│   └── assets/export.mjs             Optional local workbook exporter
├── examples/
│   ├── clipboard/                   Dataset-specific discovery/revenue/benchmark/legacy
│   ├── collections/                 Different CSV schema + explicit plan + sample runner
│   ├── demo_qwen.py, compare_models.py  Fixed historical/live model harnesses
│   ├── reliability_audit.py          Independent deterministic and paired live evaluation
│   └── *.json, QWEN_DEMOS.md         Config, current/prior engine protocols, instructions
├── test_*.py                        Deterministic and opt-in live suites; retained at root
├── test                             Historical extensionless roster experiment; not a test suite
├── data/                            Original workbook; do not edit/move
├── Clipboard_Health_Case_Study_Rules.md  Original governing text; retained path
├── derived/phase3a/                 Preserved Account/Shift model and reconciliation manifests
├── output/                          Preserved benchmark/example deliverables + historical aliases
│   ├── clipboard_benchmark/         Conditional financial results, Excel/PDF, source lineage
│   ├── collections/, synthetic/     Cross-schema example results and synthetic fixture
│   ├── query_versioning_demo/       Validated direct-answer/export evidence
│   ├── qwen_demos/, qwen_demo*       Original model baselines and transcripts
│   └── {final_acceptance,model_comparison,root_cause_diagnosis}/
│       └── <old-timestamp> -> ../../archive/runs/<group>/<old-timestamp>
├── reports/                         Original reports kept at stable referenced paths
│   ├── query_versioning_and_analysis_mode.md  Versioning release evidence
│   ├── root_cause_diagnosis.md       Historical causes; several blockers remain
│   ├── model_comparison.md          Historical measured model comparison
│   ├── workbook_relationships.md, revenue_data_model.md  Benchmark reference reports
│   ├── agent_reliability_final_report.md  Current bounded audit and acceptance
│   ├── project_cleanup.md           Cleanup decisions and verification
│   └── validation/                 Retained test logs, demos, integrity evidence
├── docs/                            Current navigation, architecture, usage and limitations
│   └── benchmark/                   Clipboard Health documentation index
├── archive/
│   ├── runs/                        Three historical batches, unchanged bytes and FAIL evidence
│   ├── experiments/loop_practice.py Standalone learning exercise
│   ├── caches/2026-10-16/           Preserved project bytecode; not runtime dependencies
│   └── cleanup_2026-10-16/          Inventory, hashes, moves, rollback and final verification
├── ollama_config.json, requirements.txt  Required configuration/dependencies
└── .venv/                           Existing local environment; unchanged, not portable source
```

`main.py` is short because it delegates. Running it calls `local_analyst.cli.main()`;
importing it exposes `examples.clipboard.legacy` names for compatibility with
`test_main.py` and prior callers. Likewise the two benchmark wrappers retain their
original CLI/import contracts. They are not disposable duplicate implementations.

Raw inputs remain at their original paths. Historical aliases preserve absolute
paths inside logs/manifests and Markdown links; do not replace symlinks with empty
folders or edit archived evidence. Future runs should use a **new** timestamp/name
in the real `output/<group>/` directory, never an archived timestamp alias.
