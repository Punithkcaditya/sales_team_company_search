import type { Report, ReportSummary, UsageStatus } from "../types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Every backend error has the shape {"message": "..."}; anything else means
 *  we never reached the backend at all. */
async function readError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json();
    if (typeof body?.message === "string") return new ApiError(body.message, response.status);
  } catch {
    /* fall through to the generic message */
  }
  return new ApiError("The server returned an unexpected response.", response.status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError("Can't reach the server. Is the backend running?", 0);
  }
  if (!response.ok) throw await readError(response);
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const listReports = () => request<ReportSummary[]>("/api/reports");

export const getUsage = () => request<UsageStatus>("/api/usage");

export const getReport = (id: number) => request<Report>(`/api/reports/${id}`);

export const deleteReport = (id: number) => request<void>(`/api/reports/${id}`, { method: "DELETE" });

export { BASE as API_BASE };
