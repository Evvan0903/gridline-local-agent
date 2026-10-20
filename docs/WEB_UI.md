# Gridline local browser workspace

Gridline is a React + TypeScript + Vite frontend with Tailwind and Radix accessible
dialogs, connected to a thin FastAPI adapter over the existing Agent and Workspace.
Financial calculations, read-only DuckDB, reviewed plans, versions, completion
checks and exports remain in `local_analyst`; they are not reimplemented in HTTP.

## Start and stop

From the project root, using Node 20.19+ or 22.12+ and the existing Python environment:

```bash
.venv/bin/python -m pip install -r requirements.txt
(cd web && npm ci)
./start-web.sh
```

Open **http://localhost:3000**. Vite serves the UI on loopback port 3000 and proxies
`/api` to FastAPI on loopback port 3001. Both ports must be free. The script refuses
port conflicts and never kills a pre-existing service. It installs nothing at
startup. Ctrl-C stops the two processes owned by this foreground session. Active
Agent calls stop at their next safe boundary, so shutdown can wait for the current
local HTTP timeout (180 seconds) or deterministic tool to finish. Ollama is separate
and is not stopped. Python changes require restarting the script; frontend changes
use Vite hot reload. No launch agents, cron jobs or other persistent services exist.

## Use

1. Create or select a workspace in the sidebar.
2. Attach/drag a CSV or XLSX (50 MB maximum; XLSX expanded archive limit 250 MB).
   Files are copied into a unique local workspace upload directory; originals are
   not overwritten. The sidebar shows actual registered logical tables and row counts.
3. Click a table for its schema and bounded 20-row preview. Previews are read-only
   queries; result-row counts and source row counts are labelled separately.
4. Choose an installed, tool-capable Ollama model, then ask a question. The activity
   panel receives actual events over SSE, including tool attempts, tool results,
   executed SQL, evidence and the final completed/incomplete/blocked/failed state.
5. Open a result/evidence chip to inspect SQL, rows, dataset versions and digest.
6. Explicitly ask for CSV, Excel or PDF if needed. The Artifacts tab lists actual
   generated files. Download rechecks current query dependencies, full result digest
   and file hash. A stale file is rejected and requires recomputation/re-export.

The composer uses Enter to send, Shift-Enter for a new line. Both side panels can
collapse and resize with pointer or arrow keys on their separators. Inspector tabs
support arrow-key navigation. Theme, widths and panel settings persist locally.
At small widths panels become overlays; close them to return to the conversation.

Settings also allows importing paths from the authorized `data/` and
`examples/collections/` folders. Use the file picker for other files. Paths and
symlinks resolving outside authorized roots are rejected. Only CSV/XLSX may be
loaded; an LLM cannot read `.env`, arbitrary rule documents or other user files.
A reviewed structured JSON plan may be uploaded only through the explicit review
acknowledgement control. Its bindings must reference this workspace's real table
IDs or namespaces, available in the inspector; no rules are inferred or approved
by the model.

## Models, usage and privacy

The implemented provider is **Ollama only**. Discovery uses its local tags and tools
capability metadata. Declared hosted/remote Ollama tags are excluded. The configured model remains the normal default when installed.
This repository does not have an OpenAI transport, cloud consent workflow or spending
limit implementation. Therefore GPT-6 Luna/GPT-6.1 Sol are not offered, even if a
backend `OPENAI_API_KEY` exists. Keys stay on the backend; they are never returned to
JavaScript or written to reports. No cloud inference or raw-workbook upload occurs.

Ollama token counts, when supplied, are captured in actual model-response events.
Cloud API spend and remaining budget are explicitly unavailable, not invented as
prices or budgets. Models currently return non-streaming responses in the existing
controller: tool activity streams live, and answer text appears after completion
checks. There is no simulated token animation or speculative answer presentation.
Checks remain bounded; a completed run does not prove arbitrary business meaning.
The prior reliability report's financial/semantic limitations still apply.

## Persistence and cancellation

Private local app state is under gitignored `.analyst_web/`:

- `sessions.sqlite3`: workspace/conversation/message metadata, structured runs/events,
  artifact references and UI settings; no credential store or workbook blobs.
- `workspaces/<id>/uploads/`: the explicitly selected source copies and reviewed plans.
- Per-workspace core audit/inputs/plan metadata and `outputs/` result snapshots.

On restart, authorized raw sources reload with fresh version validation. Visible
conversation context persists. **In-memory derived datasets are not restored.**
The UI identifies recovered conversations; recompute derived calculations. Old
raw-source evidence can remain valid if source bytes/parsing are unchanged; derived
or changed-source exports are blocked. Interrupted runs become incomplete.

There is one active run per workspace and one global inference worker, avoiding
concurrent large-model runs in this application. Stop sets `cancelling` and prevents
new tool calls at the next boundary. Only after the current request/tool returns
is `cancelled` published. Already completed tool results and authorized files remain
in the audit. A request in flight is not falsely described as immediately stopped.

## Local HTTP boundary

- Both services bind to `127.0.0.1`; trusted hosts are localhost/127.0.0.1 only.
- API bootstrap establishes an HttpOnly SameSite=Strict local session cookie.
  Mutations additionally require the session's CSRF header; browser origins are
  restricted to the two local services. Cross-site and rebinding hosts are rejected.
- Vite serves frontend files only and denies `.env`/Git files. Credentials are not
  Vite public variables. Source Markdown uses no raw HTML or external image loads.
- File input is bounded, restricted by suffix/root and symlink resolution. Downloads
  accept an opaque recorded artifact ID, never a caller-supplied filesystem path.
- There is no shell/Python execution tool, direct write-SQL API, or remote model URL.

This is a single-user local development interface, not a hardened Internet service.
Do not bind it publicly or deploy it as a multi-user application without a separate
security design. The API's cookie/CSRF values are local request tokens, not provider
API keys.

## API operations and tests

Bootstrap/providers; create/list/get workspaces; upload/load authorized sources;
get table schema/preview; create/get conversations; submit messages; get/cancel
runs; SSE events with sequence IDs and Last-Event-ID replay; query evidence;
list artifacts and validate/download; persist UI settings. Python request schemas
use Pydantic, run/event envelopes and core structured evidence retain correlation IDs.

```bash
.venv/bin/python -B -m unittest test_web_api -v
.venv/bin/python -B -m unittest discover -v
(cd web && npm test)
(cd web && npm run build)
```

Synthetic HTTP tests use the real Agent/tools with scripted model responses; they
are not live model-performance evidence. Frontend component tests use mocked HTTP.
Actual browser/local-Ollama validation and its precise coverage are documented in
`reports/web_ui_implementation.md` with evidence under `reports/validation/web_ui/`.

## V2 update

The Ollama-only limitations above describe the preserved V1 implementation. Current
provider, cloud-consent/budget, Financial Model Builder and benchmark behavior is
specified in [Gridline V2](GRIDLINE_V2.md). Paid cloud tests require explicit approval;
Sol additionally requires the local/Luna prerequisite gate. Existing result snapshots
remain available separately from dynamic models.
