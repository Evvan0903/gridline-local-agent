export type Row = (string | number | boolean | null)[];
export interface Preview {
  columns: string[];
  rows: Row[];
  truncated: boolean;
  preview_limit: number;
  query_id?: string;
  result_digest?: { row_count: number; sha256: string };
  dataset_versions?: Record<string, string>;
}
export interface Table {
  table_id: string;
  label: string;
  row_count: number;
  version_id: string;
  source_path?: string;
}
export interface Source {
  id: string;
  name: string;
  namespace: string;
  size: number;
  sha256: string;
}
export interface Conversation {
  id: string;
  title: string;
  workspace_id: string;
  created_at: string;
  model?: string;
}
export interface Run {
  id: string;
  workspace_id: string;
  conversation_id: string;
  status: string;
  model: string;
  created_at: string;
  result?: {
    answer: string;
    status: string;
    completion?: { scope?: string; reasons?: string[] };
  };
}
export interface Workspace {
  id: string;
  name: string;
  created_at: string;
  tables: Table[];
  files: Source[];
  conversations: Conversation[];
  active_run: Run | null;
  recovery: {
    message: string;
    errors: string[];
    derived_state_restored: boolean;
  };
}
export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: string;
  run_id: string;
  created_at: string;
}
export interface RunEvent {
  seq: number;
  run_id: string;
  kind: string;
  created_at: string;
  payload: Record<string, any>;
}
export interface ConversationView extends Conversation {
  messages: Message[];
  runs: Run[];
  events: Record<string, RunEvent[]>;
  recovery_notice: string;
}
export interface Providers {
  default_model?: string;
  available: boolean;
  provider: string;
  models: { id: string; label: string; provider: string; local: boolean }[];
  error: string | null;
  cloud: { implemented: boolean; credentials_configured: boolean };
  response_streaming: boolean;
  usage: {
    supported: boolean;
    api_cost_usd: number | null;
    remaining_budget_usd: number | null;
    reason: string;
  };
}
export interface Settings {
  execution_mode?: "single" | "comparison" | "multi";
  workflow_models?: Record<string,string>;
  confidential_mode?: boolean;
  theme: "dark" | "light" | "system";
  sidebar_width: number;
  inspector_width: number;
  sidebar_open: boolean;
  inspector_open: boolean;
}
export interface Bootstrap {
  csrf_token: string;
  workspaces: { id: string; name: string }[];
  providers: Providers;
  settings: (Settings & { id: string })[];
  import_roots: string[];
}
export interface Artifact {
  id: string;
  query_id: string;
  name: string;
  format: string;
  size: number;
  sha256: string;
  created_at: string;
  run_id: string;
}
export const terminal = (s: string) =>
  ["completed", "incomplete", "blocked", "failed", "cancelled"].includes(s);
