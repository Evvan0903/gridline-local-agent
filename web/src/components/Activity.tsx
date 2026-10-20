import {
  Check,
  ChevronRight,
  CircleAlert,
  Database,
  LoaderCircle,
  SquareTerminal,
  Table2,
} from "lucide-react";
import type { RunEvent, Preview } from "../types";
import { Sql } from "./Sql";
export function Activity({
  events,
  running,
  inspect,
}: {
  events: RunEvent[];
  running: boolean;
  inspect: (query: {
    sql: string;
    preview: Preview;
    evidence?: unknown;
  }) => void;
}) {
  const starts = events.filter((e) => e.kind === "tool_call_started");
  const requests = events.filter(
    (e) => e.kind === "model_request_started",
  ).length;
  if (!events.length) return null;
  return (
    <details className="activity" open={running}>
      <summary>
        <span className="activity-icon">
          {running ? (
            <LoaderCircle size={14} className="spin" />
          ) : (
            <Check size={14} />
          )}
        </span>
        <span>
          {running
            ? "Working through your analysis"
            : `${starts.length} tool ${starts.length === 1 ? "call" : "calls"}`}
        </span>
        <span className="activity-meta">
          {requests > 0
            ? `${requests} model ${requests === 1 ? "request" : "requests"}`
            : "metadata only"}
        </span>
        <ChevronRight size={13} className="details-chevron" />
      </summary>
      <div className="activity-body">
        {starts.map((start) => {
          const result = events.find(
            (e) =>
              e.kind === "tool_result_received" &&
              e.payload.call_id === start.payload.call_id,
          );
          const query = events.find(
            (e) =>
              e.kind === "sql_executed" &&
              e.payload.call_id === start.payload.call_id,
          );
          const failed = result?.payload.result?.status === "error";
          const p = result?.payload;
          return (
            <details className="tool-row" key={start.seq}>
              <summary>
                {result ? (
                  failed ? (
                    <CircleAlert size={14} className="danger" />
                  ) : (
                    <Check size={14} className="success" />
                  )
                ) : (
                  <LoaderCircle size={14} className="spin" />
                )}
                {start.payload.tool === "query_sql" ? (
                  <SquareTerminal size={14} />
                ) : start.payload.tool === "inspect_schema" ? (
                  <Table2 size={14} />
                ) : (
                  <Database size={14} />
                )}
                <span className="mono">{start.payload.tool}</span>
                <span className="tool-outcome">
                  {result
                    ? failed
                      ? "Error"
                      : p?.result?.result_digest?.row_count != null
                        ? `${p.result.result_digest.row_count} result rows`
                        : "Complete"
                    : "Running"}
                </span>
                <ChevronRight size={12} className="details-chevron" />
              </summary>
              <div className="tool-body">
                {typeof start.payload.arguments?.sql === "string" ? (
                  <Sql sql={start.payload.arguments.sql} />
                ) : (
                  <pre>{JSON.stringify(start.payload.arguments, null, 2)}</pre>
                )}
                {failed ? <p className="danger">{p?.result.error}</p> : null}
                {query && (
                  <button
                    className="text-button"
                    onClick={() =>
                      inspect({
                        sql: query.payload.sql,
                        preview: {
                          ...query.payload.result,
                          query_id: query.payload.query_id,
                          result_digest: query.payload.result_digest,
                          dataset_versions: query.payload.dataset_versions,
                        },
                        evidence: query.payload,
                      })
                    }
                  >
                    Inspect result <ChevronRight size={12} />
                  </button>
                )}
                {result && (
                  <details className="raw-result">
                    <summary>Tool response</summary>
                    <pre>{JSON.stringify(p?.result, null, 2)}</pre>
                  </details>
                )}
              </div>
            </details>
          );
        })}
        {running && (
          <div className="live-note">
            <span className="pulse-dot" />
            {events.at(-1)?.kind === "run_cancellation_requested"
              ? "Stopping after the current operation…"
              : events.at(-1)?.kind === "model_request_started"
                ? "Waiting for the local model…"
                : "Waiting for the next execution event…"}
          </div>
        )}
      </div>
    </details>
  );
}
