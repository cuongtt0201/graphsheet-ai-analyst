const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    credentials: "include",
    headers:
      options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : undefined,
    ...options,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export interface FileProfile {
  source_id: string;
  filename: string;
  sheet: string;
  columns: string[];
  dtypes: Record<string, string>;
  sample_rows: Record<string, string>[];
  row_count: number;
  /** Faithful raw sheet matrix (exact cells, header=None) for display. Present
   * only for the initial sheet; others are fetched lazily via api.sheet(). */
  grid?: (string | number)[][] | null;
  grid_rows?: number;
  has_data?: boolean;
  /** Smart header/table detection result, for the "sửa dòng tiêu đề" override. */
  detection?: {
    header_row: number;
    confidence: number;
    totals_dropped: number;
    low_confidence: boolean;
    manual?: boolean;
    /** Two rows were combined into one header (merged group + sub-label row),
     * e.g. "SỐ HÓA ĐƠN" spanning "Đầu kỳ"/"Cuối kỳ" -> "SỐ HÓA ĐƠN - Đầu kỳ". */
    two_level_header?: boolean;
    llm?: boolean;
    /** LLM proposed a different header row than the heuristic, and the
     * heuristic was confident - shown as a "check this" hint, not applied. */
    llm_suggested_row?: number;
    llm_confirmed?: boolean;
  };
  /** Deterministic data-quality warnings from the profiler (mostly-empty
   * columns, IDs that must not be summed, duplicate rows kept...). */
  flags?: string[];
  /** What the data MEANS, established once at upload (see data/semantics.py). */
  semantics?: {
    grain_type: string;
    grain_description?: string;
    dedup_safe?: boolean;
    domain?: string;
    sheet_role?: "fact" | "dimension" | "unknown";
    primary_measure?: string;
    measure_unit?: string;
    caveats?: string[];
  };
}

/** Shape of the join plan the backend proposes. The interactive confirmation
 * screen for it was never built, but agent.py still emits `need_join_confirm`,
 * so the type stays: deleting it would hide a message the server can still send.
 * Joins are currently applied automatically — see app/agent/sub_agents.py. */
export interface JoinProposal {
  joins: {
    left_file: string;
    left_column: string;
    right_file: string;
    right_column: string;
    confidence: "high" | "medium" | "low";
  }[];
}

// --- Chat-over-your-file flow ------------------------------------------------

export interface TableResult {
  columns: string[];
  rows: (string | number)[][];
  total_rows: number;
  truncated: boolean;
}

export interface ChartSeries {
  name: string;
  values: number[];
}

export interface ChartPoint {
  x: number;
  y: number;
  label?: string;
  size?: number;
}

export type ChartType =
  | "bar"
  | "horizontal-bar"
  | "line"
  | "area"
  | "sparkline"
  | "pie"
  | "donut"
  | "lollipop"
  | "dot-plot"
  | "funnel"
  | "pyramid"
  | "waterfall"
  | "gauge"
  | "bullet"
  | "progress"
  | "radial-bar"
  | "stacked-bar"
  | "grouped-bar"
  | "stacked-area"
  | "multi-line"
  | "combo"
  | "radar"
  | "scatter"
  | "bubble"
  | "heatmap"
  | "vega";

export interface ChartSpec {
  type: ChartType;
  title: string;
  labels: string[];
  values: number[];
  vegaLiteSpec?: Record<string, any>;
  /** Multi-series charts: stacked-bar, grouped-bar, stacked-area, multi-line, combo, radar. */
  series?: ChartSeries[];
  /** scatter / bubble: one point per item (size only used by bubble). */
  points?: ChartPoint[];
  /** heatmap: matrix[row][col], paired with `labels` (columns) and `rowLabels`. */
  matrix?: number[][];
  rowLabels?: string[];
  /** gauge / bullet / progress: current value is values[0]. */
  target?: number;
  max?: number;
}

/** Live events from POST /api/chat (NDJSON). "step" carries stage lines and
 * the model's own thought summaries (prefixed 💭); "reason" is the planner's
 * one-line explanation of what it is about to do. */
export type ChatStreamEvent =
  | { type: "step"; message: string }
  | { type: "reason"; message: string }
  | { type: "error"; message: string }
  | ({ type: "done" } & ChatReply);

export interface ChatReply {
  answer: string | null;
  code: string | null;
  table: TableResult | null;
  chart: ChartSpec | null;
  /** Set when the computed result is a single number - rendered as a KPI card. */
  scalar: number | string | null;
  error: string | null;
  /** AI-generated next-question suggestions grounded in this file's schema/result. */
  follow_up?: string[];
  /** True when this turn synthesized new sheets - the client should re-fetch /api/tables. */
  generated?: boolean;
  /** The planner's one-line explanation of the approach it chose. */
  reason?: string;
  /** True when the AI asked the user to disambiguate instead of guessing;
   * `answer` holds the question and `follow_up` holds the clickable choices. */
  clarify?: boolean;
  /** Structural problems found in a merge the generated code performed — a
   * repeated key multiplying rows, or a coarser table whose numbers now repeat.
   * Neither raises an error in pandas, so this is the only way the user learns
   * a total is inflated. */
  join_warnings?: string[];
  /** Present when the question warranted digging: the chain of findings the
   * bounded investigation loop produced, each verified against real numbers. */
  investigation?: {
    findings: string[];
    conclusion?: string;
    rounds?: number;
  } | null;
}

/** AI comprehension pass returned by /api/upload. */
export interface UploadInsights {
  summary: string;
  suggestions: string[];
}

/** A finding the system produced on its own at upload — proposed from measured
 * statistical signals, executed in the sandbox, and number-verified before it
 * was allowed through. Not a suggestion for the user to go and check. */
export interface Discovery {
  title: string;
  detail: string;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  /** What kind of reply this was (assistant turns only) - lets the backend
   * render history as a compact tagged line (BabelTele-style) instead of
   * replaying the full prose every turn. */
  kind?: "table" | "chart" | "scalar" | "text" | "error";
}

// --- Live dashboard (Code Interpreter, /api/agent/run_code) ---

export interface LiveKpi {
  name: string;
  value: number | string;
  status: string;
  unit?: string;
  /** Same measure over the previous comparable period, computed in pandas.
   * Absent when the data has no usable date column. */
  compare_value?: number;
  compare_label?: string;
}

export interface LiveChartPoint {
  label: string;
  value: number;
}

/** Where a chart belongs in the overview→detail reading order, and how much
 * room it gets. Assigned by the AI, which is the only party that knows what
 * each chart MEANS — position can't be derived from the chart type alone. */
export interface DashboardFilters {
  filters: {
    /** Datetime column the time range applies to; null when the data has none. */
    time_column: string | null;
    time_ranges: { key: string; label: string }[];
    dimensions: { column: string; options: string[] }[];
  };
  /** False until a dashboard has been built — there is no script to re-run. */
  can_filter: boolean;
}

export type ChartRole = "trend" | "analysis" | "breakdown" | "detail";
export type ChartSize = "sm" | "md" | "lg";

export interface LiveChart {
  title: string;
  type: ChartType;
  status: string;
  role?: ChartRole;
  size?: ChartSize;
  data?: LiveChartPoint[];
  vegaLiteSpec?: Record<string, any>;
  labels?: string[];
  series?: ChartSeries[];
  points?: ChartPoint[];
  matrix?: number[][];
  rowLabels?: string[];
  target?: number;
  max?: number;
}

export interface LiveDashboardResult {
  url: string;
  kpis: LiveKpi[];
  charts: LiveChart[];
  insights: string[];
  /** AI's pick of presentation layout/palette based on what the KPIs/charts
   * are actually about (not just their count) - frontend applies it as the
   * new default, the user can still override via the dashboard dropdowns. */
  suggested_layout?: string | null;
  suggested_palette?: string | null;
  /** Structural problems in the joins this build performed — a repeated key
   * multiplying rows, or a coarser table whose numbers now repeat. Same guard
   * the chat path reports; shown next to the dashboard it affects. */
  join_warnings?: string[];
  non_additive?: string[];
}

/** Machine-room telemetry from the AI pool: which model is answering, what the
 *  answer cost, and whether the pool had to re-route. Emitted alongside `step`,
 *  not instead of it — one is state to display, the other is a line to read. */
export interface EngineEvent {
  type: "engine";
  /** "asking" | "busy" | "ok" */
  state: string;
  model: string;
  provider?: string;
  tokens_in?: number | null;
  tokens_out?: number | null;
  secs?: number;
  attempt?: number;
}

export type LiveAgentEvent =
  /** `stage` indexes the progress rail; `kind` labels what the message is
   *  ("thought" | "model" | "busy" | "escalate"). Both come from the backend so
   *  the UI never has to infer either from the message text. */
  | { type: "step"; message: string; stage?: number; kind?: string }
  | EngineEvent
  | { type: "need_join_confirm"; proposal: JoinProposal["joins"]; tables: FileProfile[] }
  | ({ type: "done" } & LiveDashboardResult)
  | { type: "error"; message: string };

export interface ExecutiveReport {
  executive_summary: string;
  key_findings: string[];
  anomalies: string[];
  recommendations: string[];
  /** Session-artifact id assigned when the report was saved server-side. */
  id?: string;
}

export interface SavedReport {
  id: string;
  title: string;
  created_at: number;
  report: ExecutiveReport;
}

export const api = {
  me: () => request<{ authenticated: boolean; email?: string }>("/auth/me"),
  loginUrl: () => `${BASE_URL}/auth/login`,
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  mockLogin: (email: string) =>
    request<{ authenticated: boolean; email: string }>("/auth/mock-login", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  deleteFile: (filename: string) =>
    request<{ status: string; remaining_files: number }>(`/api/files/${encodeURIComponent(filename)}`, { method: "DELETE" }),

  upload: async (
    files: File[],
    /** Receives the whole event, not just its text: the waiting screen needs the
     *  stage index and the engine telemetry too, and re-deriving those from the
     *  message would put the guesswork back in the UI. */
    onEvent: (e: LiveAgentEvent) => void,
    isSample?: boolean,
  ): Promise<{
    files: FileProfile[];
    active?: string | null;
    insights?: UploadInsights | null;
    discoveries?: Discovery[];
  }> => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    if (isSample) form.append("is_sample", "true");

    const url = isSample ? `${BASE_URL}/api/upload?sample=true` : `${BASE_URL}/api/upload`;
    const res = await fetch(url, {
      method: "POST",
      body: form,
      credentials: "include",
    });

    if (!res.ok || !res.body) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Lỗi tải lên: ${res.status} ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalResult: {
      files: FileProfile[];
      active?: string | null;
      insights?: UploadInsights | null;
      discoveries?: Discovery[];
    } | null = null;

    const handleLine = (line: string) => {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.type === "step" || event.type === "engine") {
        onEvent(event);
      } else if (event.type === "done") {
        finalResult = {
          files: event.files,
          active: event.active,
          insights: event.insights,
          discoveries: event.discoveries,
        };
      } else if (event.type === "error") {
        throw new Error(event.message);
      }
    };

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) handleLine(line);
    }
    if (buffer.trim()) handleLine(buffer);

    if (!finalResult) {
      throw new Error("Không nhận được kết quả hoàn thành từ server.");
    }

    return finalResult;
  },

  tables: () => request<{ tables: FileProfile[] }>("/api/tables"),
  sheet: (sourceId: string) =>
    request<{ source_id: string; grid: (string | number)[][] }>("/api/sheet", {
      method: "POST",
      body: JSON.stringify({ source_id: sourceId }),
    }),
  /** Streams the chat pipeline's progress (which model is being asked, live
   * thought summaries, which stage is running) and finally the reply itself.
   * A turn takes ~10s across 3 stages; without this the user stared at a
   * spinner while the backend already knew exactly what it was doing. */
  chat: async (
    message: string,
    history: ChatTurn[],
    onEvent: (e: ChatStreamEvent) => void,
    selectedSources?: string[],
  ): Promise<void> => {
    const res = await fetch(`${BASE_URL}/api/chat`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, selected_sources: selectedSources }),
    });
    if (!res.ok || !res.body) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `${res.status} ${res.statusText}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (line.trim()) onEvent(JSON.parse(line) as ChatStreamEvent);
      }
    }
    if (buffer.trim()) onEvent(JSON.parse(buffer) as ChatStreamEvent);
  },

  buildDashboardAuto: async (prompt: string, onEvent: (e: LiveAgentEvent) => void, selectedSources?: string[]): Promise<void> => {
    const res = await fetch(`${BASE_URL}/api/agent/run_code`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, selected_sources: selectedSources }),
    });
    if (!res.ok || !res.body) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `${res.status} ${res.statusText}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (line.trim()) onEvent(JSON.parse(line) as LiveAgentEvent);
      }
    }
    if (buffer.trim()) onEvent(JSON.parse(buffer) as LiveAgentEvent);
  },

  /** Executive "report-to-boss" narrative for whatever is currently pinned in
   * the Dashboard tab (built via buildDashboardAuto, or pinned one item at a
   * time from chat results - both end up in the same `items` list). */
  report: (items: any[]) =>
    request<ExecutiveReport>("/api/agent/report", {
      method: "POST",
      body: JSON.stringify({ items }),
    }),

  /** Which dimensions this dataset can be sliced by (Grafana-style time range
   * + template variables), plus whether a stored script exists to re-run. */
  dashboardFilters: () => request<DashboardFilters>("/api/agent/filters"),

  /** Re-run the stored layout script on a filtered subset. No LLM involved —
   * pandas recomputes, so every panel moves together and numbers never drift. */
  refilterDashboard: (body: {
    time_column?: string | null;
    time_range?: string | null;
    dimensions?: Record<string, string[]>;
  }) =>
    request<{ kpis: LiveKpi[]; charts: LiveChart[]; rows: number }>("/api/agent/refilter", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Report artifacts saved in this session (newest first). */
  reportsList: () => request<{ reports: SavedReport[] }>("/api/agent/reports"),

  downloadReport: async (id: string): Promise<Blob> => {
    const res = await fetch(`${BASE_URL}/api/agent/reports/${id}/download`, {
      credentials: "include",
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Lỗi tải báo cáo: ${res.status}`);
    }
    return res.blob();
  },

  exportExcel: async (items: any[], palette?: string): Promise<Blob> => {
    const res = await fetch(`${BASE_URL}/api/export`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, palette }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Lỗi xuất file: ${res.status}`);
    }
    return res.blob();
  },

  diagnosticsMemory: () => request<any>("/api/diagnostics/memory"),

  deleteBehaviors: () =>
    request<{ deleted: number }>("/api/diagnostics/behaviors", { method: "DELETE" }),

  /** Re-parse one sheet using a user-chosen header row (0-based into the raw grid). */
  reparse: (source_id: string, header_row: number) =>
    request<{ source_id: string; profile: FileProfile }>("/api/reparse", {
      method: "POST",
      body: JSON.stringify({ source_id, header_row }),
    }),

  /** Whatever is pinned to the Dashboard tab, persisted server-side so it
   * survives a page refresh (not just the auto-build flow's result). */
  getDashboardItems: () => request<{ items: any[] }>("/api/dashboard/items"),
  saveDashboardItems: (items: any[]) =>
    request<{ ok: boolean; count: number }>("/api/dashboard/items", {
      method: "PUT",
      body: JSON.stringify({ items }),
    }),
};
