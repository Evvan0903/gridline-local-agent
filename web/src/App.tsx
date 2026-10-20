import { Workflow } from "./components/Workflow";
import { Benchmark } from "./components/Benchmark";
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  CSSProperties,
  FormEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUp,
  Braces,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Columns3,
  FileSpreadsheet,
  Files,
  FolderOpen,
  Grid2X2,
  HelpCircle,
  History,
  LoaderCircle,
  MessageSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Square,
  SquareTerminal,
  Sun,
  Table2,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, bootstrap, download, post } from "./api";
import type {
  Artifact,
  Bootstrap,
  Conversation,
  ConversationView,
  Preview,
  Run,
  RunEvent,
  Settings,
  Table,
  Workspace,
} from "./types";
import { terminal } from "./types";
import { Activity } from "./components/Activity";
import { DataGrid } from "./components/DataGrid";
import { Sql } from "./components/Sql";
import { Modal } from "./components/ui/Modal";

type Inspection = {
  title: string;
  sql?: string;
  preview?: Preview;
  schema?: any;
  evidence?: any;
};
const defaults: Settings = {
  confidential_mode: false,
  theme: "dark",
  sidebar_width: 248,
  inspector_width: 380,
  sidebar_open: true,
  inspector_open: true,
};
const count = (n: number) => n.toLocaleString();
const errorText = (e: unknown) =>
  e instanceof Error ? e.message : "The local operation failed.";
const size = (n: number) =>
  n > 1024 * 1024
    ? `${(n / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(n / 1024))} KB`;

export default function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(null),
    [workspaces, setWorkspaces] = useState<{ id: string; name: string }[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null),
    [conversation, setConversation] = useState<ConversationView | null>(null);
  const [settings, setSettings] = useState<Settings>(() => ({
      ...defaults,
      sidebar_open: window.innerWidth > 760,
      inspector_open: window.innerWidth > 1150,
    })),
    [model, setModel] = useState("");
  const [prompt, setPrompt] = useState(""),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [uploading, setUploading] = useState(false);
  const [run, setRun] = useState<Run | null>(null),
    [events, setEvents] = useState<Record<string, RunEvent[]>>({});
  const [inspection, setInspection] = useState<Inspection | null>(null),
    [tab, setTab] = useState<
      "data" | "schema" | "sql" | "evidence" | "artifacts"
    >("data");
  const [artifacts, setArtifacts] = useState<Artifact[]>([]),
    [filter, setFilter] = useState(""),
    [dragging, setDragging] = useState(false);
  const [modal, setModal] = useState<
      "workspace" | "settings" | "import" | "cloud" | "benchmark" | null
    >(null),
    [workspaceName, setWorkspaceName] = useState(""),
    [path, setPath] = useState(""),
    [planReviewed, setPlanReviewed] = useState(false);
  const filesRef = useRef<HTMLInputElement>(null),
    planRef = useRef<HTMLInputElement>(null),
    composerRef = useRef<HTMLTextAreaElement>(null),
    bottomRef = useRef<HTMLDivElement>(null);
  const settingsReady = useRef(false),
    selection = useRef(""),
    workspaceKey = useRef("");
  const [cloudData, setCloudData] = useState(false),
    [cloudPaid, setCloudPaid] = useState(false);
  const [cloudCap, setCloudCap] = useState("0.25"),
    [reasoning, setReasoning] = useState("low");
  const [approval, setApproval] = useState<{
    id: string;
    conversation: string;
    model: string;
  } | null>(null);
  const [spending, setSpending] = useState<any>(null);
  const cloud =
    boot?.providers.models.find((m) => m.id === model)?.provider === "openai";
  const spendModel = spending?.models?.[model];
  const active = !!run && !terminal(run.status);
  const busy = active || uploading || loading;
  const setOption = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setSettings((s) => ({ ...s, [key]: value }));

  const loadWorkspace = useCallback(async (id: string) => {
    const data = await api<Workspace>(`/workspaces/${id}`);
    const outputs = await api<Artifact[]>(`/workspaces/${id}/artifacts`);
    if (workspaceKey.current === id) {
      setWorkspace(data);
      setArtifacts(outputs);
    }
    return data;
  }, []);
  const selectWorkspace = useCallback(
    async (id: string) => {
      setError("");
      setLoading(true);
      workspaceKey.current = id;
      selection.current = "";
      setConversation(null);
      setRun(null);
      setEvents({});
      setInspection(null);
      try {
        const w = await loadWorkspace(id);
        if (w.active_run) {
          const c = await api<ConversationView>(
            `/conversations/${w.active_run.conversation_id}`,
          );
          selection.current = c.id;
          setConversation(c);
          setEvents(c.events);
          setRun(w.active_run);
        }
      } catch (e) {
        setError(errorText(e));
      } finally {
        setLoading(false);
      }
    },
    [loadWorkspace],
  );
  useEffect(() => {
    let alive = true;
    bootstrap()
      .then(async (b) => {
        if (!alive) return;
        setBoot(b);
        setWorkspaces(b.workspaces);
        setModel(
          b.providers.models.find((m) => m.id === b.providers.default_model)
            ?.id ||
            b.providers.models[0]?.id ||
            "",
        );
        const saved = b.settings.find((s) => s.id === "ui");
        if (saved) setSettings(saved);
        settingsReady.current = true;
        if (b.workspaces.length) {
          await selectWorkspace(b.workspaces[0].id);
        }
      })
      .catch((e) => {
        if (alive) setError(errorText(e));
      });
    return () => {
      alive = false;
    };
  }, [selectWorkspace]);
  useEffect(() => {
    const theme =
      settings.theme === "system"
        ? matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light"
        : settings.theme;
    document.documentElement.dataset.theme = theme;
    if (!settingsReady.current) return;
    const timer = setTimeout(() => {
      api("/settings", { method: "PUT", body: JSON.stringify(settings) }).catch(
        () => {},
      );
    }, 400);
    return () => clearTimeout(timer);
  }, [settings]);
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [conversation?.messages.length, active]);
  useEffect(() => {
    if (!run || terminal(run.status)) return;
    let mounted = true;
    const id = run.id,
      source = new EventSource(
        `/api/runs/${id}/events?after=${(events[id] || []).at(-1)?.seq || 0}`,
      );
    const receive = async (message: Event) => {
      if (!mounted) return;
      const event = JSON.parse((message as MessageEvent).data) as RunEvent;
      setEvents((previous) => ({
        ...previous,
        [id]: [
          ...(previous[id] || []).filter((e) => e.seq !== event.seq),
          event,
        ].sort((a, b) => a.seq - b.seq),
      }));
      if (event.kind === "run_started")
        setRun((r) => (r?.id === id ? { ...r, status: "running" } : r));
      if (event.kind === "run_cancellation_requested")
        setRun((r) => (r?.id === id ? { ...r, status: "cancelling" } : r));
      if (
        [
          "run_completed",
          "run_incomplete",
          "run_blocked",
          "run_failed",
          "run_cancelled",
        ].includes(event.kind)
      ) {
        source.close();
        try {
          const finished = await api<Run>(`/runs/${id}`);
          if (!mounted) return;
          setRun((r) => (r?.id === id ? finished : r));
          const c = await api<ConversationView>(
            `/conversations/${run.conversation_id}`,
          );
          if (selection.current === c.id) {
            setConversation(c);
            setEvents(c.events);
          }
          await loadWorkspace(run.workspace_id);
        } catch (e) {
          setError(errorText(e));
        }
      }
    };
    source.addEventListener("run_event", receive);
    source.onerror = async () => {
      try {
        const state = await api<Run>(`/runs/${id}`);
        if (!mounted) return;
        if (terminal(state.status)) {
          source.close();
          setRun(state);
          const c = await api<ConversationView>(
            `/conversations/${state.conversation_id}`,
          );
          if (selection.current === c.id) {
            setConversation(c);
            setEvents(c.events);
          }
        }
      } catch {
        setError(
          "Execution stream disconnected. Reopen this analysis to recover its saved status.",
        );
        source.close();
      }
    };
    return () => {
      mounted = false;
      source.close();
    };
    // One EventSource per run; the server honors reconnect cursors.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id, loadWorkspace]);

  async function createWorkspace(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const w = await post<{ id: string; name: string }>("/workspaces", {
        name: workspaceName.trim(),
      });
      setWorkspaces((s) => [...s, w]);
      setModal(null);
      setWorkspaceName("");
      await selectWorkspace(w.id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }
  async function openConversation(c: Conversation) {
    setError("");
    try {
      const value = await api<ConversationView>(`/conversations/${c.id}`);
      if (boot?.providers.models.some((m) => m.id === value.model))
        setModel(value.model!);
      selection.current = c.id;
      setConversation(value);
      setEvents(value.events);
      setRun(
        value.runs.find((r) => !terminal(r.status)) ||
          value.runs.at(-1) ||
          null,
      );
    } catch (e) {
      setError(errorText(e));
    }
  }
  function newAnalysis() {
    if (active) return;
    selection.current = "";
    setConversation(null);
    setRun(null);
    setEvents({});
    setPrompt("");
    setInspection(null);
    composerRef.current?.focus();
  }
  async function send(e?: FormEvent) {
    e?.preventDefault();
    if (!workspace || !prompt.trim() || busy) return;
    setError("");
    setLoading(true);
    try {
      let c = conversation;
      if (!c) {
        const created = await post<Conversation>(
          `/workspaces/${workspace.id}/conversations`,
          {},
        );
        c = {
          ...created,
          messages: [],
          runs: [],
          events: {},
          recovery_notice: "",
        };
        selection.current = c.id;
      }
      if (
        cloud &&
        (!approval ||
          approval.conversation !== c.id ||
          approval.model !== model)
      ) {
        setConversation(c);
        setCloudData(false);
        setCloudPaid(false);
        setSpending(await api("/spending"));
        setModal("cloud");
        return;
      }
      const text = prompt.trim(),
        r = await post<Run>(`/conversations/${c.id}/messages`, {
          content: text,
          provider: cloud ? "openai" : "ollama",
          approval_id: cloud ? approval?.id : undefined,
          reasoning,
          confidential_mode: !!settings.confidential_mode,
          model: model || undefined,
        });
      setConversation({
        ...c,
        messages: [
          ...c.messages,
          {
            id: r.id + "-user",
            role: "user",
            content: text,
            status: "sent",
            run_id: r.id,
            created_at: r.created_at,
          },
        ],
      });
      setPrompt("");
      setRun(r);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }
  async function upload(files: FileList | File[], reviewed = false) {
    if (!workspace || busy) return;
    setUploading(true);
    setError("");
    try {
      for (const file of Array.from(files)) {
        if (file.size > 50 * 1024 * 1024)
          throw new Error("Files must be 50 MB or smaller.");
        const body = new FormData();
        body.append("file", file);
        if (reviewed) body.append("reviewed_plan", "true");
        await api(`/workspaces/${workspace.id}/upload`, {
          method: "POST",
          body,
        });
      }
      const w = await loadWorkspace(workspace.id);
      if (w.tables.length && !reviewed)
        await inspectTable(w.tables.at(-1)!, w.id);
      if (reviewed) setModal(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setUploading(false);
      if (filesRef.current) filesRef.current.value = "";
      if (planRef.current) planRef.current.value = "";
    }
  }
  async function importPath(e: FormEvent) {
    e.preventDefault();
    if (!workspace) return;
    setUploading(true);
    setError("");
    try {
      await post(`/workspaces/${workspace.id}/load`, { path });
      await loadWorkspace(workspace.id);
      setModal(null);
      setPath("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setUploading(false);
    }
  }
  async function inspectTable(table: Table, workspaceId = workspace?.id) {
    if (!workspaceId) return;
    setError("");
    try {
      const value = await api<{ schema: any; preview: Preview }>(
        `/workspaces/${workspaceId}/tables/${encodeURIComponent(table.table_id)}`,
      );
      setInspection({
        title: table.label,
        schema: value.schema,
        preview: value.preview,
        evidence: value.schema,
      });
      setTab("data");
      setOption("inspector_open", true);
    } catch (e) {
      setError(errorText(e));
    }
  }
  function inspectQuery(query: {
    sql: string;
    preview: Preview;
    evidence?: unknown;
  }) {
    setInspection({ title: "Query result", ...query });
    setTab("data");
    setOption("inspector_open", true);
  }
  async function inspectEvidence(id: string) {
    if (!workspace) return;
    setError("");
    try {
      const value = await api<any>(
        `/workspaces/${workspace.id}/evidence/${id}`,
      );
      setInspection({
        title: "Query evidence",
        sql: value.sql,
        preview: {
          ...value.result,
          query_id: id,
          result_digest: value.result_digest,
          dataset_versions: Object.fromEntries(
            Object.entries(value.dependencies).map(([t, m]: [string, any]) => [
              t,
              m.version_id,
            ]),
          ),
        },
        evidence: value,
      });
      setTab("evidence");
      setOption("inspector_open", true);
    } catch (e) {
      setError(errorText(e));
    }
  }
  function resize(side: "sidebar" | "inspector", e: ReactPointerEvent) {
    e.preventDefault();
    const start = e.clientX,
      initial =
        side === "sidebar" ? settings.sidebar_width : settings.inspector_width;
    const move = (event: PointerEvent) =>
      setOption(
        side === "sidebar" ? "sidebar_width" : "inspector_width",
        Math.min(
          side === "sidebar" ? 360 : 600,
          Math.max(
            side === "sidebar" ? 200 : 300,
            initial + (event.clientX - start) * (side === "sidebar" ? 1 : -1),
          ),
        ),
      );
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }
  const tableList = (workspace?.tables || []).filter((t) =>
    (t.label + " " + t.table_id).toLowerCase().includes(filter.toLowerCase()),
  );
  const recent = (workspace?.conversations || []).slice().reverse();
  const latestQueries = (run ? events[run.id] || [] : []).filter(
    (e) => e.kind === "sql_executed",
  );
  const style = {
    "--sidebar-width": `${settings.sidebar_width}px`,
    "--inspector-width": `${settings.inspector_width}px`,
  } as CSSProperties;
  const renderMarkdown = (text: string) => (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        img: () => null,
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );

  return (
    <div
      className={`app ${settings.sidebar_open ? "" : "sidebar-hidden"} ${settings.inspector_open ? "" : "inspector-hidden"}`}
      style={style}
    >
      <aside className="sidebar" aria-label="Workspace sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Grid2X2 size={19} />
          </span>
          <span>
            gridline<span className="brand-dot">.</span>
          </span>
          <span className="local-badge">{cloud ? "CLOUD" : "LOCAL"}</span>
          <button
            className="icon-button sidebar-close"
            aria-label="Collapse sidebar"
            onClick={() => setOption("sidebar_open", false)}
          >
            <PanelLeftClose size={16} />
          </button>
        </div>
        <div className="workspace-selector">
          <FolderOpen size={15} />
          <select
            aria-label="Workspace"
            value={workspace?.id || ""}
            onChange={(e) => selectWorkspace(e.target.value)}
          >
            <option value="" disabled>
              Select workspace
            </option>
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
          <ChevronDown size={12} />
          <button
            className="icon-button"
            title="Create workspace"
            aria-label="Create workspace"
            onClick={() => setModal("workspace")}
          >
            <Plus size={15} />
          </button>
        </div>
        <button
          className="new-analysis"
          disabled={!workspace || busy}
          onClick={newAnalysis}
        >
          <Plus size={16} />
          New analysis<span className="key-hint">↵</span>
        </button>
        <div className="sidebar-scroll">
          <div className="section-label">
            <span>DATA SOURCES</span>
            <button
              className="icon-button"
              aria-label="Add data source"
              disabled={!workspace || busy}
              onClick={() => filesRef.current?.click()}
            >
              <Plus size={14} />
            </button>
          </div>
          <button
            className="file-explorer-row"
            disabled={!workspace || busy}
            onClick={() => filesRef.current?.click()}
          >
            <FolderOpen size={15} />
            <span>Files</span>
            <span className="count-badge">{workspace?.files.length || 0}</span>
          </button>
          {workspace?.files.map((f) => (
            <div className="source-file" key={f.id}>
              <FileSpreadsheet size={14} />
              <span title={f.name}>{f.name}</span>
              <span>{size(f.size)}</span>
            </div>
          ))}
          {tableList.length > 4 && (
            <label className="table-search">
              <Search size={13} />
              <input
                aria-label="Filter tables"
                placeholder="Filter tables…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </label>
          )}
          <div className="table-list">
            {tableList.map((t) => (
              <button
                key={t.table_id}
                className={`table-entry ${inspection?.title === t.label ? "selected" : ""}`}
                disabled={active}
                onClick={() => inspectTable(t)}
                title={t.table_id}
              >
                <Table2 size={14} />
                <span>{t.label}</span>
                <span className="table-count">{count(t.row_count)}</span>
              </button>
            ))}
            {!workspace?.tables.length && (
              <div className="sidebar-empty">
                Load a workbook or CSV.
                <br />
                Its tables will appear here.
              </div>
            )}
          </div>
          <div className="section-label history-label">
            <span>RECENT ANALYSES</span>
            <History size={13} />
          </div>
          {recent.map((c) => (
            <button
              key={c.id}
              className={`conversation-entry ${conversation?.id === c.id ? "selected" : ""}`}
              onClick={() => openConversation(c)}
            >
              <MessageSquare size={14} />
              <span>{c.title}</span>
            </button>
          ))}
          {!recent.length && (
            <div className="sidebar-empty">Your next insight starts here.</div>
          )}
        </div>
        <div className="sidebar-footer">
          <span
            className={`connection-dot ${boot?.providers.available ? "online" : ""}`}
          />
          <span>
            {boot?.providers.available ? "Ollama connected" : "Local workspace"}
          </span>
          <button
            className="icon-button"
            aria-label="Open settings"
            onClick={() => setModal("settings")}
          >
            <Settings2 size={16} />
          </button>
        </div>
        <div
          className="resize-handle"
          role="separator"
          aria-label="Resize sidebar"
          aria-orientation="vertical"
          aria-valuenow={settings.sidebar_width}
          aria-valuemin={200}
          aria-valuemax={360}
          tabIndex={0}
          onPointerDown={(e) => resize("sidebar", e)}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
              e.preventDefault();
              setOption(
                "sidebar_width",
                Math.max(
                  200,
                  Math.min(
                    360,
                    settings.sidebar_width +
                      (e.key === "ArrowRight" ? 10 : -10),
                  ),
                ),
              );
            }
          }}
        />
      </aside>

      <div className="workspace-main">
        <header className="topbar">
          <div className="topbar-left">
            {!settings.sidebar_open && (
              <button
                className="icon-button"
                aria-label="Show sidebar"
                onClick={() => setOption("sidebar_open", true)}
              >
                <PanelLeftOpen size={17} />
              </button>
            )}
            <span className="breadcrumb-workspace">
              {workspace?.name || "Your workspace"}
            </span>
            <ChevronRight size={13} />
            <span className="breadcrumb-analysis">
              {conversation ? "Analysis" : "New analysis"}
            </span>
          </div>
          <div className="topbar-right">
            <div className="model-selector">
              <span
                className={`connection-dot ${boot?.providers.available ? "online" : ""}`}
              />
              <select
                aria-label="Model"
                value={model}
                disabled={active}
                onChange={(e) => {
                  setModel(e.target.value);
                  setApproval(null);
                }}
              >
                {!boot?.providers.models.length && (
                  <option value="">Ollama offline</option>
                )}
                {boot?.providers.models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.local ? "LOCAL · " : "CLOUD · "}
                    {m.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={12} />
            </div>
            {cloud && <span role="status">{settings.confidential_mode ? "Confidential Mode: ON — cloud blocked until strict minimization is implemented." : "Cloud analysis may send source data, including employee identifiers, financial records and relevant messages, to the selected API provider."}</span>}
            <span className="spend-chip" title={boot?.providers.usage.reason}>
              <ShieldCheck size={13} />
              {cloud
                ? `Cloud · ${reasoning} · ${approval?.model === model ? "authorized" : "consent required"}`
                : "On device"}
            </span>
            {cloud && (
              <button
                className="secondary-button"
                onClick={async () => {
                  setSpending(await api("/spending"));
                  setModal("cloud");
                }}
              >
                Usage / consent
              </button>
            )}
            <button
              className="secondary-button"
              onClick={() => setModal("benchmark")}
            >
              Execution modes
            </button>
            <span className="topbar-divider" />
            <button
              className="icon-button"
              aria-label={
                settings.theme === "light"
                  ? "Switch to dark theme"
                  : "Switch to light theme"
              }
              onClick={() =>
                setOption(
                  "theme",
                  settings.theme === "light" ? "dark" : "light",
                )
              }
            >
              {settings.theme === "light" ? (
                <Moon size={16} />
              ) : (
                <Sun size={16} />
              )}
            </button>
            <button
              className="icon-button"
              aria-label={
                settings.inspector_open
                  ? "Collapse inspector"
                  : "Show inspector"
              }
              onClick={() =>
                setOption("inspector_open", !settings.inspector_open)
              }
            >
              {settings.inspector_open ? (
                <PanelRightClose size={17} />
              ) : (
                <PanelRightOpen size={17} />
              )}
            </button>
          </div>
        </header>
        <div className="analysis-layout">
          <main
            className={`center-panel ${dragging ? "drag-active" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node))
                setDragging(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              upload(e.dataTransfer.files);
            }}
          >
            {dragging && (
              <div className="drop-overlay">
                <FileSpreadsheet size={28} />
                <strong>Drop your data here</strong>
                <span>CSV or Excel · stays on this machine</span>
              </div>
            )}
            <div className="conversation-scroll">
              {!conversation?.messages.length ? (
                <div className="welcome">
                  <div className="welcome-eyebrow">
                    <span className="tiny-grid">
                      <Grid2X2 size={14} />
                    </span>
                    YOUR LOCAL DATA WORKSPACE
                  </div>
                  <h1>
                    Your data.
                    <br />
                    <span>A clearer answer.</span>
                  </h1>
                  <p>
                    Explore workbooks, follow every calculation,
                    <br className="desktop-break" /> and turn questions into
                    evidence.
                  </p>
                  <div className="welcome-cards">
                    <button
                      onClick={() => {
                        setPrompt(
                          "List all registered tables and their row counts. Do not run SQL.",
                        );
                        composerRef.current?.focus();
                      }}
                    >
                      <Table2 size={18} />
                      <strong>Map the workbook</strong>
                      <span>
                        Discover tables, columns
                        <br />
                        and exact row counts.
                      </span>
                      <ArrowRight size={14} className="card-arrow" />
                    </button>
                    <button
                      onClick={() => {
                        setPrompt(
                          "Inspect the available schemas, identify missing values and duplicate keys, and explain any data-quality limitations.",
                        );
                        composerRef.current?.focus();
                      }}
                    >
                      <Search size={18} />
                      <strong>Find the signal</strong>
                      <span>
                        Explore patterns and check
                        <br />
                        the quality of your data.
                      </span>
                      <ArrowRight size={14} className="card-arrow" />
                    </button>
                    <button
                      onClick={() => {
                        setPrompt(
                          "Export the previous verified query result to CSV. Cite its query evidence ID.",
                        );
                        composerRef.current?.focus();
                      }}
                    >
                      <ArrowDownToLine size={18} />
                      <strong>Make it shareable</strong>
                      <span>
                        Export verified results
                        <br />
                        with their provenance.
                      </span>
                      <ArrowRight size={14} className="card-arrow" />
                    </button>
                  </div>
                  {!workspace ? (
                    <button
                      className="primary-button create-first"
                      onClick={() => setModal("workspace")}
                    >
                      <Plus size={15} />
                      Create a workspace
                    </button>
                  ) : (
                    <button
                      className="welcome-load"
                      disabled={busy}
                      onClick={() => filesRef.current?.click()}
                    >
                      <Plus size={14} />
                      Load a CSV or Excel file<span>to get started</span>
                    </button>
                  )}
                  <div className="privacy-note">
                    <ShieldCheck size={13} />
                    <span>
                      Dataset execution stays local. Cloud inference requires
                      explicit consent.
                    </span>
                  </div>
                </div>
              ) : (
                <div className="messages">
                  {conversation.recovery_notice && (
                    <div className="session-notice" role="status">
                      <History size={13} />
                      {conversation.recovery_notice}
                    </div>
                  )}
                  {conversation.messages.map((m) => (
                    <article key={m.id} className={`message ${m.role}`}>
                      <div className="message-heading">
                        <span
                          className={`avatar ${m.role === "assistant" ? "agent-avatar" : ""}`}
                        >
                          {m.role === "assistant" ? <Grid2X2 size={13} /> : "Y"}
                        </span>
                        <strong>
                          {m.role === "assistant" ? "Gridline" : "You"}
                        </strong>
                        {m.role === "assistant" && (
                          <span
                            className={`status-pill ${m.status}`}
                            title="Bounded controller checks; business meaning is not universally verified."
                          >
                            {m.status === "completed"
                              ? "Checks passed"
                              : m.status}
                          </span>
                        )}
                      </div>
                      {m.role === "assistant" && (
                        <Activity
                          events={events[m.run_id] || []}
                          running={false}
                          inspect={inspectQuery}
                        />
                      )}
                      <div className="markdown">
                        {renderMarkdown(m.content)}
                      </div>
                      {m.role === "assistant" &&
                        Array.from(
                          new Set(
                            (events[m.run_id] || [])
                              .filter((e) => e.kind === "sql_executed")
                              .map((e) => e.payload.query_id),
                          ),
                        ).map((id) => (
                          <button
                            key={id}
                            className="evidence-chip mono"
                            onClick={() => inspectEvidence(id)}
                          >
                            <Braces size={12} />
                            {id}
                            <ChevronRight size={12} />
                          </button>
                        ))}
                    </article>
                  ))}
                  {active && run && (
                    <article className="message assistant pending">
                      <div className="message-heading">
                        <span className="avatar agent-avatar">
                          <Grid2X2 size={13} />
                        </span>
                        <strong>Gridline</strong>
                        <span className="status-pill running">
                          {run.status === "cancelling"
                            ? "Stopping safely"
                            : run.status === "queued"
                              ? "Queued"
                              : "Running locally"}
                        </span>
                      </div>
                      <Activity
                        events={events[run.id] || []}
                        running
                        inspect={inspectQuery}
                      />
                      <div className="working-caption">
                        {run.status === "cancelling"
                          ? "The current request or tool must finish before cancellation is confirmed."
                          : "Execution updates are live. The answer appears after completion checks."}
                      </div>
                    </article>
                  )}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>
            <div className="composer-area">
              {error && !modal && (
                <div className="error-banner" role="alert">
                  <CircleAlert size={16} />
                  <span>{error}</span>
                  <button
                    className="icon-button"
                    aria-label="Dismiss error"
                    onClick={() => setError("")}
                  >
                    <X size={14} />
                  </button>
                </div>
              )}
              {uploading && (
                <div className="upload-status" role="status">
                  <LoaderCircle size={14} className="spin" />
                  Loading and registering your file…
                </div>
              )}
              {workspace?.recovery.errors.map((e) => (
                <div className="error-banner" role="alert" key={e}>
                  {e}
                </div>
              ))}
              <form className="composer" onSubmit={send}>
                <textarea
                  ref={composerRef}
                  aria-label="Message the agent"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={
                    workspace
                      ? "Ask a question about your data…"
                      : "Create a workspace to start exploring…"
                  }
                  disabled={!workspace}
                  rows={2}
                  maxLength={20000}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                />
                <div className="composer-bottom">
                  <div className="composer-tools">
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Attach CSV or Excel"
                      disabled={!workspace || busy}
                      onClick={() => filesRef.current?.click()}
                    >
                      <Paperclip size={17} />
                    </button>
                    <span className="composer-mode">
                      <span className="connection-dot online" />
                      Local analysis
                    </span>
                  </div>
                  {active ? (
                    <button
                      type="button"
                      className="stop-button"
                      aria-label="Cancel active run"
                      disabled={run?.status === "cancelling"}
                      onClick={() => {
                        if (run)
                          post<Run>(`/runs/${run.id}/cancel`, {})
                            .then(setRun)
                            .catch((e) => setError(errorText(e)));
                      }}
                    >
                      <Square size={12} fill="currentColor" />
                      {run?.status === "cancelling" ? "Stopping" : "Stop"}
                    </button>
                  ) : (
                    <button
                      type="submit"
                      className="send-button"
                      aria-label="Send message"
                      disabled={!workspace || !prompt.trim() || busy}
                    >
                      {loading ? (
                        <LoaderCircle size={17} className="spin" />
                      ) : (
                        <ArrowUp size={19} />
                      )}
                    </button>
                  )}
                </div>
              </form>
              <div className="composer-hint">
                <span>
                  <ShieldCheck size={11} />
                  Read-only analysis · explicit exports only
                </span>
                <span>
                  ↵ Send <span className="hint-separator">·</span> ⇧↵ New line
                </span>
              </div>
              {boot && !boot.providers.available && (
                <div className="offline-note">
                  Ollama is offline. Metadata inventories still work; other
                  analyses require an installed local model.
                </div>
              )}
            </div>
          </main>

          <aside className="inspector" aria-label="Data inspector">
            <div
              className="resize-handle left"
              role="separator"
              aria-label="Resize inspector"
              aria-orientation="vertical"
              aria-valuenow={settings.inspector_width}
              aria-valuemin={300}
              aria-valuemax={600}
              tabIndex={0}
              onPointerDown={(e) => resize("inspector", e)}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                  e.preventDefault();
                  setOption(
                    "inspector_width",
                    Math.max(
                      300,
                      Math.min(
                        600,
                        settings.inspector_width +
                          (e.key === "ArrowLeft" ? 10 : -10),
                      ),
                    ),
                  );
                }
              }}
            />
            <div className="inspector-heading">
              <span>
                <Columns3 size={15} />
                Inspector
              </span>
              <button
                className="icon-button"
                aria-label="Close inspector"
                onClick={() => setOption("inspector_open", false)}
              >
                <X size={15} />
              </button>
            </div>
            <div
              className="inspector-tabs"
              role="tablist"
              aria-label="Inspector views"
            >
              {(
                ["data", "schema", "sql", "evidence", "artifacts"] as const
              ).map((t) => (
                <button
                  role="tab"
                  aria-selected={tab === t}
                  key={t}
                  className={tab === t ? "active" : ""}
                  onClick={() => setTab(t)}
                  onKeyDown={(e) => {
                    const tabs = [
                      "data",
                      "schema",
                      "sql",
                      "evidence",
                      "artifacts",
                    ] as const;
                    let index = tabs.indexOf(t);
                    if (e.key === "ArrowRight")
                      index = (index + 1) % tabs.length;
                    else if (e.key === "ArrowLeft")
                      index = (index + tabs.length - 1) % tabs.length;
                    else if (e.key === "Home") index = 0;
                    else if (e.key === "End") index = tabs.length - 1;
                    else return;
                    e.preventDefault();
                    setTab(tabs[index]);
                    (
                      e.currentTarget.parentElement?.querySelectorAll("button")[
                        index
                      ] as HTMLButtonElement
                    )?.focus();
                  }}
                >
                  {t === "sql" ? "SQL" : t[0].toUpperCase() + t.slice(1)}
                  {t === "artifacts" && artifacts.length > 0 && (
                    <span>{artifacts.length}</span>
                  )}
                </button>
              ))}
            </div>
            <div
              className="inspector-content"
              role="tabpanel"
              aria-label={`${tab} inspector`}
            >
              {tab === "artifacts" ? (
                <div className="artifacts-view">
                  <div className="inspector-section-title">
                    EXPORTED RESULTS
                  </div>
                  {artifacts.length ? (
                    artifacts.map((a) => (
                      <div className="artifact-card" key={a.id}>
                        <div className="artifact-icon">
                          <Files size={18} />
                        </div>
                        <div>
                          <strong>{a.name}</strong>
                          <span>
                            {a.format.toUpperCase()} · {size(a.size)} · snapshot
                          </span>
                          <code>{a.query_id}</code>
                        </div>
                        <button
                          className="icon-button"
                          aria-label={`Download ${a.name}`}
                          title="Validate evidence and download"
                          onClick={() =>
                            download(a.id, a.name).catch((e) =>
                              setError(errorText(e)),
                            )
                          }
                        >
                          <ArrowDownToLine size={16} />
                        </button>
                      </div>
                    ))
                  ) : (
                    <div className="inspector-empty">
                      <Files size={26} />
                      <h3>Your outputs, with proof.</h3>
                      <p>
                        Ask the agent to export a verified result to CSV, Excel,
                        or PDF.
                      </p>
                      <span>Exports are never automatic.</span>
                    </div>
                  )}
                </div>
              ) : !inspection ? (
                <div className="inspector-empty">
                  <div className="inspector-illustration">
                    <Table2 size={30} />
                    <span className="illustration-code">
                      <Braces size={16} />
                    </span>
                  </div>
                  <h3>A closer look.</h3>
                  <p>
                    Select a table or a query result
                    <br />
                    to inspect the data behind an answer.
                  </p>
                  <div className="inspector-empty-features">
                    <span>
                      <Table2 size={13} />
                      Bounded data previews
                    </span>
                    <span>
                      <Braces size={13} />
                      SQL & versioned evidence
                    </span>
                    <span>
                      <ArrowDownToLine size={13} />
                      Verified result exports
                    </span>
                  </div>
                </div>
              ) : (
                <>
                  <div className="inspection-title">
                    <div>
                      <Table2 size={15} />
                      <strong>{inspection.title}</strong>
                    </div>
                    {inspection.preview?.query_id && (
                      <button
                        className="mono query-id"
                        onClick={() =>
                          inspectEvidence(inspection.preview!.query_id!)
                        }
                      >
                        {inspection.preview.query_id}
                      </button>
                    )}
                  </div>
                  {tab === "data" && inspection.preview && (
                    <DataGrid preview={inspection.preview} />
                  )}{" "}
                  {tab === "schema" &&
                    (inspection.schema ? (
                      <div className="schema-view">
                        <div className="inspector-section-title">
                          COLUMN SCHEMA
                        </div>
                        {(inspection.schema.columns || []).map(
                          (c: any, i: number) => (
                            <div key={i} className="schema-row">
                              <span className="column-type">
                                {String(c.dtype || c.type || "").includes("int")
                                  ? "#"
                                  : "Aa"}
                              </span>
                              <strong>{c.column || c.name}</strong>
                              <span>{c.dtype || c.type}</span>
                            </div>
                          ),
                        )}
                        <div className="schema-stats">
                          <span>Registered rows</span>
                          <strong>
                            {count(inspection.schema.row_count || 0)}
                          </strong>
                        </div>
                        <pre className="json-view">
                          {JSON.stringify(inspection.schema, null, 2)}
                        </pre>
                      </div>
                    ) : (
                      <div className="small-empty">
                        Select a source table to inspect its schema.
                      </div>
                    ))}
                  {tab === "sql" &&
                    (inspection.sql ? (
                      <>
                        <div className="sql-toolbar">
                          <span>DUCKDB · READ ONLY</span>
                          <button
                            className="text-button"
                            onClick={() =>
                              navigator.clipboard.writeText(inspection.sql!)
                            }
                          >
                            Copy SQL
                          </button>
                        </div>
                        <Sql sql={inspection.sql} />
                      </>
                    ) : (
                      <div className="small-empty">
                        SQL appears here when you inspect an executed query.
                      </div>
                    ))}
                  {tab === "evidence" && (
                    <>
                      <div className="evidence-notice">
                        <ShieldCheck size={15} />
                        <span>
                          Version validity is checked when evidence is opened or
                          an artifact is downloaded.
                        </span>
                      </div>
                      <pre className="json-view">
                        {JSON.stringify(inspection.evidence || {}, null, 2)}
                      </pre>
                    </>
                  )}
                </>
              )}
              {latestQueries.length > 0 && tab === "data" && (
                <div className="recent-queries">
                  <div className="inspector-section-title">THIS RUN</div>
                  {latestQueries.map((q) => (
                    <button
                      key={q.seq}
                      onClick={() =>
                        inspectQuery({
                          sql: q.payload.sql,
                          preview: {
                            ...q.payload.result,
                            query_id: q.payload.query_id,
                            result_digest: q.payload.result_digest,
                            dataset_versions: q.payload.dataset_versions,
                          },
                          evidence: q.payload,
                        })
                      }
                    >
                      <SquareTerminal size={14} />
                      <span className="mono">{q.payload.query_id}</span>
                      <span>{q.payload.result_digest.row_count} rows</span>
                      <ChevronRight size={12} />
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="inspector-footer">
              <ShieldCheck size={12} />
              <span>Source versions preserved</span>
              <span title="Current evidence is version checked. Business meaning still requires approved rules.">
                <HelpCircle size={12} />
              </span>
            </div>
          </aside>
        </div>
      </div>
      <input
        ref={filesRef}
        type="file"
        accept=".csv,.xlsx"
        multiple
        hidden
        aria-label="Upload data files"
        onChange={(e) => {
          if (e.target.files) upload(e.target.files);
        }}
      />
      <input
        ref={planRef}
        type="file"
        accept=".json"
        hidden
        aria-label="Upload reviewed plan"
        onChange={(e) => {
          if (e.target.files && planReviewed) upload(e.target.files, true);
        }}
      />
      <Modal
        open={modal === "cloud"}
        onOpenChange={(v) => !v && setModal(null)}
        title="Cloud data and spending authorization"
        description="Prompts, selected schema, tool results and necessary source excerpts may be sent to OpenAI. Raw workbook files remain local. store=false disables response storage; it does not enable Zero Data Retention or eliminate abuse-monitoring retention."
        error={error}
      >
        <p>
          Selected model: <strong>{model}</strong>. No automatic fallback or
          repeat trial.
        </p>
        <label>
          Reasoning effort{" "}
          <select
            aria-label="Reasoning effort"
            value={reasoning}
            onChange={(e) => setReasoning(e.target.value)}
          >
            <option>low</option>
            <option>medium</option>
            <option>high</option>
          </select>
        </label>
        <label>
          Maximum per-run USD{" "}
          <input
            aria-label="Cloud run budget"
            type="number"
            min="0.01"
            max="1"
            step="0.01"
            value={cloudCap}
            onChange={(e) => setCloudCap(e.target.value)}
          />
        </label>
        <p>
          Session and application caps: $2.00. Separate Sol cap: $1.00.
          Application limits cannot guarantee a provider billing ceiling. Actual
          invoice cost: unavailable.
        </p>
        <p>
          Requests: {spendModel?.requests ?? "unavailable"}; input / output
          tokens: {spendModel?.input_tokens ?? "unavailable"} /{" "}
          {spendModel?.output_tokens ?? "unavailable"}; usage-priced estimate: $
          {spendModel?.observed_estimate_usd ?? "unavailable"}; committed: $
          {spendModel?.committed_usd ?? "unavailable"}; remaining application
          budget: $
          {Math.max(
            0,
            2 -
              Object.values(spending?.models || {}).reduce(
                (s: number, m: any) => s + Number(m.committed_usd),
                0,
              ),
          ).toFixed(4)}
          .
        </p>
        <label>
          <input
            type="checkbox"
            checked={cloudData}
            onChange={(e) => setCloudData(e.target.checked)}
          />{" "}
          I authorize source-derived business/employee content for this
          conversation and current loaded sources.
        </label>
        <label>
          <input
            type="checkbox"
            checked={cloudPaid}
            onChange={(e) => setCloudPaid(e.target.checked)}
          />{" "}
          I authorize paid requests for the selected model within these caps.
        </label>
        {model === "gpt-6.1-sol" && (
          <p role="status">
            Sol is gated until local/Luna acceptance and independent validators
            are ready. No Sol call will be made.
          </p>
        )}
        <button
          className="primary-button"
          disabled={
            !cloudData || !cloudPaid || !conversation || model === "gpt-6.1-sol"
          }
          onClick={async () => {
            try {
              const a = await post<any>(
                `/conversations/${conversation!.id}/cloud-approval`,
                {
                  model,
                  confidential_mode: !!settings.confidential_mode,
                  paid_authorized: cloudPaid,
                  data_authorized: cloudData,
                  run_cap: cloudCap,
                },
              );
              setApproval({ id: a.id, conversation: conversation!.id, model });
              setModal(null);
              setError("");
            } catch (e) {
              setError(e instanceof Error ? e.message : "Authorization failed");
            }
          }}
        >
          Authorize this model and source scope
        </button>
      </Modal>
      <Modal
        open={modal === "benchmark"}
        onOpenChange={(v) => !v && setModal(null)}
        title="Financial Analyst — V3 execution"
        description="Each result is independently validated. Unexecuted and blocked tests never count as passed."
        error={error}
      >
        <Workflow onSettingsSaved={setSettings} workspaceId={workspace?.id} onError={setError} confidentialMode={!!settings.confidential_mode} />
        <details><summary>Historical assisted Clipboard benchmark — specialized tools</summary><Benchmark workspaceId={workspace?.id} onError={setError} confidentialMode={!!settings.confidential_mode} /></details>
      </Modal>
      <Modal
        error={error}
        open={modal === "workspace"}
        onOpenChange={(v) => !v && setModal(null)}
        title="Create a workspace"
        description="Keep your data, conversations, and evidence together on this machine."
      >
        <form onSubmit={createWorkspace}>
          <label className="field-label" htmlFor="workspace-name">
            Workspace name
          </label>
          <input
            autoFocus
            id="workspace-name"
            className="text-input"
            value={workspaceName}
            maxLength={100}
            placeholder="e.g. Revenue review"
            onChange={(e) => setWorkspaceName(e.target.value)}
          />
          <div className="dialog-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={() => setModal(null)}
            >
              Cancel
            </button>
            <button
              className="primary-button"
              disabled={!workspaceName.trim() || loading}
            >
              Create workspace
            </button>
          </div>
        </form>
      </Modal>
      <Modal
        error={error}
        open={modal === "import"}
        onOpenChange={(v) => !v && setModal(null)}
        title="Load a local path"
        description="Paths are restricted to authorized import folders. Use the file picker for a file elsewhere."
      >
        <form onSubmit={importPath}>
          <label htmlFor="source-path" className="field-label">
            CSV or Excel path
          </label>
          <input
            id="source-path"
            className="text-input mono"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/absolute/path/to/file.csv"
          />
          <div className="allowed-roots">
            {boot?.import_roots.map((p) => (
              <code key={p}>{p}</code>
            ))}
          </div>
          <div className="dialog-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={() => setModal(null)}
            >
              Cancel
            </button>
            <button className="primary-button" disabled={!path || busy}>
              Load file
            </button>
          </div>
        </form>
      </Modal>
      <Modal
        error={error}
        open={modal === "settings"}
        onOpenChange={(v) => !v && setModal(null)}
        title="Workspace settings"
        description="Local by design. Credentials stay on the backend."
      >
        <label><input type="checkbox" aria-label="Confidential Mode" checked={!!settings.confidential_mode} onChange={(e) => {setOption("confidential_mode", e.target.checked); setApproval(null);}} /> Confidential Mode: {settings.confidential_mode ? "ON" : "OFF"}</label>
        <p>{settings.confidential_mode ? "Strict minimization is incomplete. Cloud requests are blocked; local analysis remains available." : "Cloud analysis may send source data, including employee identifiers, financial records and relevant messages, to the selected API provider."}</p>
        <div className="setting-row">
          <span>Appearance</span>
          <select
            aria-label="Theme"
            className="text-input compact"
            value={settings.theme}
            onChange={(e) =>
              setOption("theme", e.target.value as Settings["theme"])
            }
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="system">System</option>
          </select>
        </div>
        <div className="settings-info">
          <ShieldCheck size={18} />
          <div>
            <strong>
              {cloud
                ? `${model} · cloud inference`
                : "Ollama · on-device inference"}
            </strong>
            <p>
              Local Ollama is the default. OpenAI Responses uses source-bound
              consent, per-request cost reservations and zero automatic retries.
              No model escalation is automatic.
            </p>
          </div>
        </div>
        <div className="setting-row">
          <span>API spend / remaining budget</span>
          <span className="muted">
            {cloud ? "See Usage / consent" : "Local inference: no API spend"}
          </span>
        </div>
        <p className="settings-note">
          Saved messages persist after restart. Derived calculations require
          recomputation; saved evidence is checked against current source
          versions.
        </p>
        <div className="setting-row">
          <span>Import from an authorized folder</span>
          <button
            className="secondary-button"
            disabled={!workspace || busy}
            onClick={() => setModal("import")}
          >
            Load path
          </button>
        </div>
        <div className="review-plan">
          <label>
            <input
              type="checkbox"
              checked={planReviewed}
              onChange={(e) => setPlanReviewed(e.target.checked)}
            />
            I have reviewed and approved this structured financial plan.
          </label>
          <button
            className="secondary-button"
            disabled={!planReviewed || !workspace || busy}
            onClick={() => planRef.current?.click()}
          >
            Register reviewed JSON plan
          </button>
        </div>
      </Modal>
    </div>
  );
}
