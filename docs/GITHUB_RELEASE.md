# Source-only GitHub snapshot

This repository contains the Gridline application, tools, UI, tests and synthetic
examples. Private case workbooks, governing case text, prepared business data,
commission models/PDFs, conversations, source-specific approvals, spending databases
and historical raw evaluation records are intentionally excluded.

The original local project keeps those files. A GitHub checkout does not contain
the private Clipboard Health inputs. Specialized case profiles and tests need
those separately supplied, authorized inputs; source absence is not a completed
benchmark. References to `reports/`, `output/`, `archive/` and `data/` in older
development documentation refer to that local workspace, not bundled evidence.

The general CSV/XLSX analyst and synthetic examples do not require the private case.
The default model runs locally through Ollama. Optional OpenAI calls require the
implemented consent/privacy/budget checks; a key in `.env` is not authorization.
Previous evaluation approvals are not included or transferable to a new run.

Confidential Mode blocks cloud where minimization is unfinished. Selected cloud
context, explicit consent, key redaction, application spending/request limits and
`store=False` do not guarantee anonymization, zero retention or provider billing
ceilings. General financial interpretation and payout approval require review.

No third-party model/runtime binaries are vendored. The optional Artifact Tool
dependency is machine-local; openpyxl is the application fallback for Excel export.
The supplied scripts and tests do not establish broad autonomous financial accuracy.
