// Single place that knows the backend's shapes and base URL. Every page
// imports from here rather than calling fetch() directly, so a backend
// contract change (see backend/docs/API_SPEC.md) touches one file.

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Thrown when a request is deliberately aborted (e.g. a newer request
// superseded it) — callers should treat this as "ignore, don't show an
// error", not as a real failure. See AbortController usage in sandbox/page.tsx.
export class RequestAbortedError extends Error {
  constructor() {
    super("Request was aborted");
    this.name = "RequestAbortedError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new RequestAbortedError();
    }
    throw err;
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response wasn't JSON — fall back to statusText
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export interface BacktestResult {
  run_id: string;
  ticker: string;
  as_of: string;
  horizon_days: number;
  price_at_as_of: number;
  predicted_price: number;
  predicted_return_pct: number;
  actual_price: number;
  actual_return_pct: number;
  absolute_pct_error: number;
  directional_hit: boolean;
  self_check_passed: boolean;
  self_check_detail: string;
}

export interface BacktestRequest {
  ticker: string;
  as_of: string; // YYYY-MM-DD
  horizon_days: number;
}

export interface BacktestSuiteRequest {
  ticker: string;
  start_date: string;
  end_date: string;
  horizon_days: number;
  step_days: number;
}

export async function runBacktest(
  req: BacktestRequest,
  signal?: AbortSignal,
): Promise<BacktestResult> {
  return request<BacktestResult>("/sandbox/backtest", {
    method: "POST",
    body: JSON.stringify(req),
    signal,
  });
}

export async function runBacktestSuite(
  req: BacktestSuiteRequest,
  signal?: AbortSignal,
): Promise<BacktestResult[]> {
  return request<BacktestResult[]>("/sandbox/backtest-suite", {
    method: "POST",
    body: JSON.stringify(req),
    signal,
  });
}

export async function getBacktestHistory(
  params: { ticker?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<BacktestResult[]> {
  const query = new URLSearchParams();
  if (params.ticker) query.set("ticker", params.ticker);
  if (params.limit) query.set("limit", String(params.limit));
  const qs = query.toString();
  return request<BacktestResult[]>(`/sandbox/history${qs ? `?${qs}` : ""}`, { signal });
}

export async function checkHealth(): Promise<{ status: string }> {
  return request<{ status: string }>("/health");
}
