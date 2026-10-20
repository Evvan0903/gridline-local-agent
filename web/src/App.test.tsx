import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { DataGrid } from "./components/DataGrid";
import { Activity } from "./components/Activity";
import { api, bootstrap, download, post } from "./api";
import type { Bootstrap, RunEvent } from "./types";
vi.mock("./api", () => ({
  api: vi.fn(),
  bootstrap: vi.fn(),
  post: vi.fn(),
  download: vi.fn(),
}));
class Events extends EventTarget {
  static instances: Events[] = [];
  close = vi.fn();
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    super();
    Events.instances.push(this);
  }
  emit(event: RunEvent) {
    this.dispatchEvent(
      new MessageEvent("run_event", { data: JSON.stringify(event) }),
    );
  }
}
const provider = {
  provider: "ollama",
  available: true,
  models: [
    { id: "qwen3:8b", label: "qwen3:8b", provider: "ollama", local: true },
    {
      id: "qwen3.8:27b",
      label: "qwen3.8:27b",
      provider: "ollama",
      local: true,
    },
  ],
  error: null,
  cloud: { implemented: false, credentials_configured: false },
  response_streaming: false,
  usage: {
    supported: false,
    api_cost_usd: null,
    remaining_budget_usd: null,
    reason: "Only local Ollama is implemented.",
  },
};
let workspace: any, conversation: any, artifacts: any[];
const event = (
  seq: number,
  kind: string,
  payload: Record<string, any> = {},
): RunEvent => ({
  seq,
  run_id: "r",
  kind,
  created_at: "2026-10-17T12:00:00Z",
  payload,
});
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(download).mockResolvedValue(undefined);
  Events.instances = [];
  vi.stubGlobal("EventSource", Events);
  Object.defineProperty(window, "innerWidth", { value: 1440, writable: true });
  workspace = {
    id: "w",
    name: "Test workspace",
    tables: [],
    files: [],
    conversations: [],
    active_run: null,
    recovery: {
      derived_state_restored: false,
      message: "Recompute derived data.",
      errors: [],
    },
  };
  conversation = {
    id: "c",
    workspace_id: "w",
    title: "Analysis",
    messages: [],
    runs: [],
    events: {},
    recovery_notice: "Recompute after restart.",
  };
  artifacts = [];
  vi.mocked(bootstrap).mockResolvedValue({
    csrf_token: "dummy-csrf",
    workspaces: [{ id: "w", name: "Test workspace" }],
    providers: provider,
    settings: [],
    import_roots: ["/allowed"],
  } as Bootstrap);
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/workspaces/w") return structuredClone(workspace);
    if (path.endsWith("/artifacts")) return structuredClone(artifacts);
    if (path === "/conversations/c") return structuredClone(conversation);
    if (path === "/runs/r")
      return {
        id: "r",
        status: "completed",
        workspace_id: "w",
        conversation_id: "c",
        model: "qwen3:8b",
        created_at: "",
      };
    if (path.includes("/tables/"))
      return {
        schema: {
          row_count: 2,
          columns: [{ name: "amount", dtype: "Decimal" }],
        },
        preview: {
          columns: ["amount"],
          rows: [[10.1], [20.2]],
          truncated: false,
          preview_limit: 20,
        },
      };
    return {};
  });
  vi.mocked(post).mockImplementation(async (path) =>
    path.endsWith("/conversations")
      ? { id: "c", workspace_id: "w", title: "New analysis", created_at: "" }
      : path.endsWith("/messages")
        ? {
            id: "r",
            workspace_id: "w",
            conversation_id: "c",
            status: "queued",
            model: "qwen3:8b",
            created_at: "",
          }
        : path.endsWith("/cancel")
          ? {
              id: "r",
              workspace_id: "w",
              conversation_id: "c",
              status: "cancelling",
              model: "qwen3:8b",
              created_at: "",
            }
          : { id: "new", name: "Created workspace" },
  );
});
async function ready() {
  render(<App />);
  await screen.findByText("Test workspace", {
    selector: ".breadcrumb-workspace",
  });
}
describe("workspace UI contracts (mock HTTP; not model inference)", () => {
  it("shows local models, empty states and unavailable cloud costs", async () => {
    await ready();
    expect(
      screen.getByRole("heading", { name: /Your data/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Model" })).toHaveValue(
      "qwen3:8b",
    );
    expect(
      screen.queryByRole("option", { name: /GPT/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Open settings" }),
    );
    expect(
      screen.getByText("Local inference: no API spend"),
    ).toBeInTheDocument();
  });
  it("opens cloud consent without sending a paid message", async () => {
    vi.mocked(bootstrap).mockResolvedValueOnce({
      csrf_token: "dummy",
      workspaces: [{ id: "w", name: "Test workspace" }],
      providers: {
        ...provider,
        cloud: { implemented: true, credentials_configured: false },
        models: [
          ...provider.models,
          {
            id: "gpt-6-luna",
            label: "GPT-6 Luna",
            provider: "openai",
            local: false,
          },
        ],
      },
      settings: [],
      import_roots: [],
    } as Bootstrap);
    await ready();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Model" }),
      "gpt-6-luna",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Message the agent" }),
      "Synthetic task",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(
      await screen.findByRole("heading", {
        name: "Cloud data and spending authorization",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Authorize this model and source scope",
      }),
    ).toBeDisabled();
    expect(
      vi.mocked(post).mock.calls.some(([path]) => path.endsWith("/messages")),
    ).toBe(false);
    expect(
      screen.getByText(/does not enable Zero Data Retention/),
    ).toBeInTheDocument();
  });
  it("submits the chosen provider/model and renders a terminal SSE answer", async () => {
    await ready();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Model" }),
      "qwen3.8:27b",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Message the agent" }),
      "Count records",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(Events.instances.length).toBe(1));
    expect(post).toHaveBeenLastCalledWith("/conversations/c/messages", {
      confidential_mode: false,
      content: "Count records",
      provider: "ollama",
      approval_id: undefined,
      reasoning: "low",
      model: "qwen3.8:27b",
    });
    conversation.messages = [
      {
        id: "m",
        role: "assistant",
        content: "There are 2 records.",
        status: "completed",
        run_id: "r",
        created_at: "",
      },
    ];
    await act(async () =>
      Events.instances[0].emit(
        event(5, "run_completed", {
          result: { answer: "There are 2 records." },
        }),
      ),
    );
    expect(await screen.findByText("There are 2 records.")).toBeInTheDocument();
    expect(Events.instances[0].close).toHaveBeenCalled();
  });
  it("uploads a selected source and displays its registered table and preview", async () => {
    await ready();
    vi.mocked(api).mockImplementation(async (path, init) => {
      if (path.endsWith("/upload")) {
        expect(init?.body).toBeInstanceOf(FormData);
        workspace.tables = [
          { table_id: "t", label: "Sheet A", row_count: 2, version_id: "v" },
        ];
        return { tables: workspace.tables };
      }
      if (path === "/workspaces/w") return structuredClone(workspace);
      if (path.endsWith("/artifacts")) return [];
      if (path.includes("/tables/t"))
        return {
          schema: { row_count: 2 },
          preview: {
            columns: ["amount"],
            rows: [[10.1], [20.2]],
            truncated: false,
            preview_limit: 20,
          },
        };
      return {};
    });
    await userEvent.upload(
      screen.getByLabelText("Upload data files"),
      new File(["id,amount\n1,10.1"], "new.csv", { type: "text/csv" }),
    );
    expect(
      await screen.findByRole("button", { name: /Sheet A/ }),
    ).toBeInTheDocument();
    expect(await screen.findByText("20.2")).toBeInTheDocument();
  });
  it("acknowledges cancellation without claiming it is already terminal", async () => {
    await ready();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Message the agent" }),
      "Calculate total",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Cancel active run" }),
    );
    expect(post).toHaveBeenLastCalledWith("/runs/r/cancel", {});
    expect(await screen.findByText("Stopping safely")).toBeInTheDocument();
  });
  it("persists appearance controls and explains restart limitations", async () => {
    await ready();
    await userEvent.click(
      screen.getByRole("button", { name: "Switch to light theme" }),
    );
    expect(document.documentElement.dataset.theme).toBe("light");
    await userEvent.click(
      screen.getByRole("button", { name: "Collapse inspector" }),
    );
    expect(
      screen.getByRole("button", { name: "Show inspector" }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Open settings" }),
    );
    expect(
      screen.getByText(/Derived calculations require recomputation/),
    ).toBeInTheDocument();
  });
  it("uses authorized download IDs and displays submission errors", async () => {
    artifacts = [
      { id: "a", name: "result.csv", query_id: "q", format: "csv", size: 100 },
    ];
    await ready();
    await userEvent.click(screen.getByRole("tab", { name: /Artifacts/ }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Download result.csv" }),
    );
    expect(download).toHaveBeenCalledWith("a", "result.csv");
    vi.mocked(post).mockRejectedValueOnce(
      new Error("No installed local model"),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Message the agent" }),
      "Question",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No installed local model",
    );
  });
  it("defaults confidentiality OFF and visibly blocks cloud when ON", async () => {
    await ready();
    await userEvent.click(screen.getByRole("button", {name:"Open settings"}));
    const toggle=screen.getByRole("checkbox", {name:"Confidential Mode"});
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(screen.getByText("Strict minimization is incomplete. Cloud requests are blocked; local analysis remains available.")).toBeInTheDocument();
  });
  it("requires explicit reviewed-plan acknowledgement", async () => {
    await ready();
    await userEvent.click(
      screen.getByRole("button", { name: "Open settings" }),
    );
    const button = screen.getByRole("button", {
      name: "Register reviewed JSON plan",
    });
    expect(button).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", {name:"I have reviewed and approved this structured financial plan."}));
    expect(button).toBeEnabled();
  });
  it("distinguishes source-free result rows, nulls and preview bounds", () => {
    render(
      <DataGrid
        preview={{
          columns: ["amount"],
          rows: [[null], [10]],
          truncated: true,
          preview_limit: 2,
          result_digest: { row_count: 30, sha256: "digest" },
        }}
      />,
    );
    expect(screen.getByText("null")).toBeInTheDocument();
    expect(screen.getByText("30 result rows")).toBeInTheDocument();
    expect(screen.getByText("Preview · first 2")).toBeInTheDocument();
  });
  it("shows correlated actual SQL events and result inspection", async () => {
    const inspect = vi.fn(),
      events = [
        event(1, "tool_call_started", {
          call_id: "call_1",
          tool: "query_sql",
          arguments: { sql: "SELECT COUNT(*) FROM t" },
        }),
        event(2, "sql_executed", {
          call_id: "call_1",
          query_id: "q",
          sql: "SELECT COUNT(*) FROM t",
          result: { columns: ["count"], rows: [[2]], truncated: false },
          result_digest: { row_count: 1, sha256: "digest" },
        }),
        event(3, "tool_result_received", {
          call_id: "call_1",
          tool: "query_sql",
          result: { result_digest: { row_count: 1 } },
        }),
      ];
    render(<Activity events={events} running={false} inspect={inspect} />);
    await userEvent.click(screen.getByText("1 tool call"));
    await userEvent.click(screen.getByText("query_sql"));
    await userEvent.click(
      screen.getByRole("button", { name: /Inspect result/ }),
    );
    expect(inspect).toHaveBeenCalledWith(
      expect.objectContaining({ sql: "SELECT COUNT(*) FROM t" }),
    );
  });
});
