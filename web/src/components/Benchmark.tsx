import { Activity } from "./Activity";
import type { RunEvent } from "../types";
import { useEffect, useState } from "react";
import { api, post } from "../api";
export function Benchmark({
  workspaceId,
  onError,
  confidentialMode = false,
}: {
  confidentialMode?: boolean;
  workspaceId?: string;
  onError: (v: string) => void;
}) {
  const [liveEvents, setLiveEvents] = useState<RunEvent[]>([]),
    [queryView, setQueryView] = useState<unknown>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [catalog, setCatalog] = useState<any>(null),
    [result, setResult] = useState<any>(null);
  const [paid, setPaid] = useState(false),
    [exposure, setExposure] = useState(false),
    [cap, setCap] = useState("0.25");
  const [models, setModels] = useState<string[]>(["qwen3:8b"]),
    [source, setSource] = useState("");
  useEffect(() => {
    let alive = true;
    api<any>(
      `/benchmarks/catalog${workspaceId ? `?workspace_id=${workspaceId}` : ""}`,
    )
      .then((value) => {
        if (alive) setCatalog(value);
      })
      .catch((e) => onError(String(e)));
    return () => {
      alive = false;
    };
  }, [workspaceId, onError]);
  useEffect(() => {
    let alive = true;
    api<any[]>(
      `/benchmarks${workspaceId ? `?workspace_id=${workspaceId}` : ""}`,
    )
      .then((rows) => {
        if (alive) setHistory(rows || []);
      })
      .catch((e) => onError(String(e)));
    return () => {
      alive = false;
    };
  }, [workspaceId, result?.id, result?.status, onError]);
  useEffect(() => {
    if (
      !result?.id ||
      ["completed", "failed", "blocked", "cancelled"].includes(result.status)
    )
      return;
    const timer = setInterval(
      () =>
        api<any>(`/benchmarks/${result.id}`)
          .then(setResult)
          .catch((e) => onError(String(e))),
      1500,
    );
    return () => clearInterval(timer);
  }, [result?.id, result?.status, onError]);
  useEffect(() => {
    if (!result?.run_id || result.status !== "running") return;
    setLiveEvents([]);
    const stream = new EventSource(`/api/runs/${result.run_id}/events`);
    const receive = (event: MessageEvent) => {
      const value = JSON.parse(event.data) as RunEvent;
      setLiveEvents((previous) =>
        previous.some((e) => e.seq === value.seq)
          ? previous
          : [...previous, value],
      );
    };
    stream.addEventListener("run_event", receive as EventListener);
    return () => stream.close();
  }, [result?.run_id, result?.status]);
  return (
    <div className="benchmark-panel">
      {history.length > 0 && (
        <label>
          Saved comparisons{" "}
          <select
            aria-label="Saved benchmark"
            value={result?.id || ""}
            onChange={async (e) => {
              if (e.target.value)
                setResult(await api(`/benchmarks/${e.target.value}`));
            }}
          >
            <option value="">Choose comparison</option>
            {history.map((b) => (
              <option key={b.id} value={b.id}>
                {b.created_at} · {b.models?.join(", ")} · {b.status}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Loaded dataset{" "}
        <select
          aria-label="Benchmark dataset"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        >
          <option value="">Choose source</option>
          {catalog?.sources?.map((f: any) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </label>
      <p>
        Rules: {catalog?.rules_name || "Original rules unavailable"}. Case
        questions: June commissions, exception impacts, accelerator scenario,
        three Slack escalations and a response under 250 words.
      </p>
      <p>Tests: {catalog?.tests?.join("; ")}</p>
      {catalog?.models?.map((m: string) => (
        <label key={m} style={{ display: "block" }}>
          <input
            type="checkbox"
            checked={models.includes(m)}
            onChange={(e) =>
              setModels((v) =>
                e.target.checked ? [...v, m] : v.filter((x) => x !== m),
              )
            }
          />
          {m}
        </label>
      ))}
      <p>Confidential Mode: {confidentialMode ? "ON — cloud blocked: strict minimization is incomplete." : "OFF"}</p>
      {models.some(m => m.startsWith("gpt-")) && !confidentialMode && <p>Cloud analysis may send source data, including employee identifiers, financial records and relevant messages, to the selected API provider.</p>}
      <p>
        Cloud benchmarks require separate spending and actual-case exposure
        authorization. Sol proposed cap $1.00. Without both authorizations,
        cloud selections record NOT RUN and send no data. Sol also requires
        current offline checks and prior local/Luna attempts. Local models run
        sequentially, once each, without retries.
      </p>
      <label style={{ display: "block" }}>
        Per-run maximum USD{" "}
        <input
          aria-label="Benchmark run budget"
          type="number"
          min="0.01"
          max="1"
          step="0.01"
          value={cap}
          onChange={(e) => setCap(e.target.value)}
        />
      </label>
      <p>
        Session/application maximum: $2.00; Sol maximum: $1.00. Usage-priced
        estimates are not provider invoices; client limits cannot guarantee a
        billing ceiling. Model reasoning: low. Raw workbook files stay local;
        necessary schemas, prompts, tool results and case excerpts may be sent
        to OpenAI.
      </p>
      <label style={{ display: "block" }}>
        <input
          type="checkbox"
          checked={exposure}
          onChange={(e) => setExposure(e.target.checked)}
        />
        I authorize this selected dataset and original rules content to OpenAI.
      </label>
      <label style={{ display: "block" }}>
        <input
          type="checkbox"
          checked={paid}
          onChange={(e) => setPaid(e.target.checked)}
        />
        I authorize the selected cloud models and displayed spending caps.
      </label>
      <p>
        Sol prerequisites:{" "}
        {catalog?.sol_ready
          ? "ready for a separately approved single attempt"
          : "BLOCKED; no Sol request will be sent"}
        .
      </p>
      <button
        className="primary-button"
        disabled={
          !workspaceId ||
          !source ||
          !models.length ||
          result?.status === "running"
        }
        onClick={async () => {
          try {
            setResult(
              await post("/benchmarks", {
                confidential_mode: confidentialMode,
                workspace_id: workspaceId,
                source_id: source,
                models,
                paid_authorized: paid,
                data_authorized: exposure,
                run_cap: cap,
              }),
            );
          } catch (e) {
            onError(String(e));
          }
        }}
      >
        {paid && exposure
          ? "Start explicitly authorized comparison"
          : "Start local comparison / record cloud NOT RUN"}
      </button>
      {result?.status === "running" && result?.run_id && (
        <button
          className="secondary-button"
          onClick={() =>
            post(`/runs/${result.run_id}/cancel`, {}).catch((e) =>
              onError(String(e)),
            )
          }
        >
          Stop benchmark safely
        </button>
      )}
      <Activity
        events={liveEvents}
        running={result?.status === "running"}
        inspect={setQueryView}
      />
      {queryView !== null && (
        <pre style={{ maxHeight: 240, overflow: "auto" }}>
          {JSON.stringify(queryView, null, 2)}
        </pre>
      )}
      {result && (
        <>
          <p role="status">{result.status}</p>
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Result</th>
                <th>Seconds</th>
                <th>Requests / tokens / USD</th>
              </tr>
            </thead>
            <tbody>
              {result.results?.map((r: any) => (
                <tr key={r.model}>
                  <td>{r.model}</td>
                  <td>
                    {r.status}
                    {r.candidate && (
                      <>
                        <br />
                        <a
                          href={`/api/benchmarks/${result.id}/artifacts/${encodeURIComponent(r.model)}/excel`}
                        >
                          Excel candidate
                        </a>{" "}
                        ·{" "}
                        <a
                          href={`/api/benchmarks/${result.id}/artifacts/${encodeURIComponent(r.model)}/pdf`}
                        >
                          PDF candidate
                        </a>
                      </>
                    )}
                  </td>
                  <td>{r.seconds ?? "—"}</td>
                  <td>
                    {r.api_requests ?? 0} /{" "}
                    {r.tokens ?? (r.api_requests === 0 ? 0 : "unavailable")} /{" "}
                    {r.api_usd ?? "0"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <a href={`/api/benchmarks/${result.id}/report`} download>
            Download complete trace and report JSON
          </a>
          <pre
            style={{ maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap" }}
          >
            {JSON.stringify(result.results, null, 2)}
          </pre>
        </>
      )}
    </div>
  );
}
