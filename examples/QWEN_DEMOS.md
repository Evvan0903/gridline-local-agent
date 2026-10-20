# Comparable local Ollama demonstrations

Model selection is centralized in root `ollama_config.json`. `model` selects the
normal Agent model, `test_model` selects the candidate, and `comparison_model`
selects the comparison baseline. Explicit `--model` overrides `OLLAMA_MODEL`,
which overrides the JSON setting. `LOCAL_ANALYST_CONFIG` can select another config
file. The generic CLI, Agent and legacy compatibility tools use this resolver.

Check metadata only (no data load, inference, download, or model substitution):

```bash
.venv/bin/python -m examples.demo_qwen --model qwen3.8:27b --check-only
```

The exact tag must appear in local `/api/tags`, and `/api/show` must confirm tools.
A missing tag or missing tool capability blocks the demonstrations. Model weights
are not downloaded automatically. Metadata availability does not prove that the
model fits available memory or will successfully complete inference.

Run the three original questions for one model:

```bash
.venv/bin/python -m examples.demo_qwen --model qwen3.8:27b
```

The three questions, dataset, tools, temperature (0), context size (16384), loop
bound (8), and existing evaluation checks are unchanged. Dataset, question and
engine hashes must match `model_comparison_protocol.json`; mismatches stop the comparison.
The existing evaluation checks successful SQL execution and expected count values
in the answer. It is not a complete semantic correctness evaluator.

Original files `output/qwen_demo.json` and `output/qwen_demo_workspace/audit.jsonl`
are preserved. An additional baseline archive is at
`output/qwen_demos/qwen3_8b_baseline/`. Future runs use separate model/timestamp
directories under `output/qwen_demos/`, retaining transcripts, audits and manifests.
The latest metadata-only check is in `output/qwen_demos/readiness.json`.

Run a fair comparison on the current common engine version (fresh output path):

```bash
.venv/bin/python -u -m examples.compare_models --output output/model_comparison/new_run
```

Both exact tags must pass preflight. The runner reruns 8B and the candidate on the
same workbook, questions, tool schemas, system prompt and generation settings.
The original 8B archive is never rewritten. The new common-version protocol
records the configuration-only changes since that archive.

Each run retains source/version hashes, incremental results, complete HTTP
requests/responses, partial histories on failure, workspace SQL audit, and memory
samples. Failures do not cause prompt edits, fallback models or rewritten tests.
Additional SQL-result/table-ID/citation checks are reported separately from the
unchanged original pass criteria. A separate first-query retry probe injects one
explicit transient tool failure for each model; it does not change the original
three questions or their scores. Only the selected benchmark models are unloaded
between runs. No financial model or existing Excel/PDF output is regenerated.

Memory numbers include sampled Ollama process-tree RSS and `/api/ps` allocation
metadata; these overlap and must not be summed. RSS is not a physical GPU-memory
measurement. First-question time includes cold loading, following questions are
warm. A single run of three questions is a small benchmark, not a general ranking.

Mock-only runner tests:

```bash
.venv/bin/python -m unittest -v test_qwen_demo_config test_model_comparison
```

The active runtime protocol is selected by `qwen_demo_config.json`; the reliability
release uses `model_comparison_protocol_reliability.json`. Older protocol files and
recorded scores remain unchanged. The stricter independent evaluation is
`python -B -m examples.reliability_audit`, documented in `docs/RELIABILITY.md`.
