// Phase 10B — API client for the /assembly/runs/* endpoints.
// Older Phase-7 client functions live alongside (postSimulation/etc.)
// for the legacy /simulations/* surface; do not call them from new
// 10B pages.

import {
  Brief,
  ReportResult,
  SimulationCreated,
  SimulationStatus,
  briefSchema,
  simulationCreatedSchema,
  simulationReportSchema,
  simulationStatusSchema,
} from "./schema";
import type {
  CohortsPayload,
  CreateRunRequest,
  CreateRunResponse,
  DiscussionPayload,
  DiscussionTranscriptPayload,
  FounderReport,
  IntentPayload,
  PersonasPayload,
  RunStatusResponse,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_ASSEMBLY_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
    public kind: string | undefined,
  ) {
    super(buildApiErrorMessage(status, detail, kind));
  }
}

/** Build a human-readable error message that surfaces FastAPI 422
 *  validation errors instead of just "api error: 422". */
function buildApiErrorMessage(
  status: number,
  detail: unknown,
  kind: string | undefined,
): string {
  const head = `api error: ${status}${kind ? ` (${kind})` : ""}`;
  // FastAPI 422 detail shape: array of {loc, msg, type}.
  if (status === 422 && Array.isArray(detail)) {
    const parts = (detail as Array<Record<string, unknown>>).slice(0, 3).map(
      (d) => {
        const loc = Array.isArray(d.loc)
          ? (d.loc as unknown[])
              .filter((p) => p !== "body" && p !== "brief")
              .join(".")
          : "";
        const msg = typeof d.msg === "string" ? d.msg : "invalid";
        return loc ? `${loc} — ${msg}` : msg;
      },
    );
    return `${head}: ${parts.join("; ")}`;
  }
  // Plain string detail (e.g. our HTTPException(detail="..."))
  if (typeof detail === "string" && detail) {
    return `${head}: ${detail}`;
  }
  // Object with "detail" string
  if (
    detail &&
    typeof detail === "object" &&
    typeof (detail as Record<string, unknown>).detail === "string"
  ) {
    return `${head}: ${(detail as Record<string, string>).detail}`;
  }
  return head;
}

async function jsonRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<{ status: number; body: T | unknown }> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  return { status: res.status, body: body as T | unknown };
}

// ---- Phase 10B: /assembly/runs/* ---------------------------------

export async function createAssemblyRun(
  payload: CreateRunRequest,
): Promise<CreateRunResponse> {
  const { status, body } = await jsonRequest<CreateRunResponse>(
    "/assembly/runs",
    { method: "POST", body: JSON.stringify(payload) },
  );
  if (status !== 202 && status !== 200) {
    const detail =
      isObject(body) && "detail" in body
        ? (body as Record<string, unknown>).detail
        : body;
    throw new ApiError(status, detail, undefined);
  }
  return body as CreateRunResponse;
}

export async function getAssemblyRun(
  runId: string,
): Promise<RunStatusResponse> {
  const { status, body } = await jsonRequest<RunStatusResponse>(
    `/assembly/runs/${runId}`,
  );
  if (status === 404) {
    throw new ApiError(404, body, "run_not_found");
  }
  if (status !== 200) {
    throw new ApiError(status, body, undefined);
  }
  return body as RunStatusResponse;
}

export async function getAssemblyReport(
  runId: string,
): Promise<FounderReport> {
  const { status, body } = await jsonRequest<FounderReport>(
    `/assembly/runs/${runId}/report`,
  );
  if (status !== 200) {
    throw new ApiError(status, body, undefined);
  }
  return body as FounderReport;
}

export async function getAssemblyReportMarkdown(
  runId: string,
): Promise<string> {
  const res = await fetch(
    `${API_BASE}/assembly/runs/${runId}/report.md`,
  );
  if (res.status !== 200) {
    throw new ApiError(res.status, await res.text(), undefined);
  }
  return res.text();
}

export async function getAssemblyPersonas(
  runId: string,
): Promise<PersonasPayload> {
  const { status, body } = await jsonRequest<PersonasPayload>(
    `/assembly/runs/${runId}/personas`,
  );
  if (status !== 200) {
    throw new ApiError(status, body, undefined);
  }
  return body as PersonasPayload;
}

export async function getAssemblyCohorts(
  runId: string,
): Promise<CohortsPayload> {
  const { status, body } = await jsonRequest<CohortsPayload>(
    `/assembly/runs/${runId}/cohorts`,
  );
  if (status !== 200) {
    throw new ApiError(status, body, undefined);
  }
  return body as CohortsPayload;
}

export async function getAssemblyDiscussion(
  runId: string,
): Promise<DiscussionPayload> {
  const { status, body } = await jsonRequest<DiscussionPayload>(
    `/assembly/runs/${runId}/discussion`,
  );
  if (status !== 200) {
    throw new ApiError(status, body, undefined);
  }
  return body as DiscussionPayload;
}

export async function getAssemblyDiscussionTurns(
  runId: string,
): Promise<DiscussionTranscriptPayload> {
  const { status, body } = await jsonRequest<DiscussionTranscriptPayload>(
    `/assembly/runs/${runId}/discussion/turns`,
  );
  if (status !== 200) {
    throw new ApiError(status, body, undefined);
  }
  return body as DiscussionTranscriptPayload;
}

/** Returns the per-run audit aggregate (evidence_quality,
 *  evidence_signals, persona_quality_gates, …). Used by the
 *  evidence-base indicator card. The endpoint is internal/dev so
 *  swallow errors silently and let the caller fall back to the
 *  data already on the page. */
export async function getAssemblyAudit(
  runId: string,
): Promise<Record<string, unknown> | null> {
  try {
    const { status, body } = await jsonRequest<Record<string, unknown>>(
      `/assembly/runs/${runId}/audit`,
    );
    if (status !== 200) return null;
    return body as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function getAssemblyIntent(
  runId: string,
): Promise<IntentPayload> {
  const { status, body } = await jsonRequest<IntentPayload>(
    `/assembly/runs/${runId}/intent`,
  );
  if (status !== 200) {
    throw new ApiError(status, body, undefined);
  }
  return body as IntentPayload;
}

// ---- Legacy Phase-7 /simulations/* (kept for backward-compat) ---

export async function postSimulation(
  brief: Brief,
): Promise<SimulationCreated> {
  briefSchema.parse(brief);
  const { status, body } = await jsonRequest<SimulationCreated>(
    "/simulations",
    { method: "POST", body: JSON.stringify(brief) },
  );
  if (status !== 202) {
    const detail =
      isObject(body) && "detail" in body
        ? (body as Record<string, unknown>).detail
        : body;
    const kind =
      isObject(detail) &&
      typeof (detail as Record<string, unknown>).kind === "string"
        ? ((detail as Record<string, unknown>).kind as string)
        : undefined;
    throw new ApiError(status, detail, kind);
  }
  return simulationCreatedSchema.parse(body);
}

export async function getSimulationStatus(
  id: string,
): Promise<SimulationStatus> {
  const { status, body } = await jsonRequest<SimulationStatus>(
    `/simulations/${id}/status`,
  );
  if (status === 404) {
    throw new ApiError(404, body, "simulation_not_found");
  }
  if (status !== 200) {
    throw new ApiError(status, body, undefined);
  }
  return simulationStatusSchema.parse(body);
}

export async function getSimulationReport(
  id: string,
): Promise<ReportResult> {
  const { status, body } = await jsonRequest(`/simulations/${id}/report`);
  if (status === 200) {
    return { kind: "ready", report: simulationReportSchema.parse(body) };
  }
  if (status === 409) {
    const detail =
      (isObject(body) && (body as Record<string, unknown>).detail) || {};
    const detailObj = isObject(detail) ? (detail as Record<string, unknown>) : {};
    return {
      kind: "report_not_ready",
      current_status:
        typeof detailObj.current_status === "string"
          ? detailObj.current_status
          : "unknown",
      guidance:
        typeof detailObj.guidance === "string"
          ? detailObj.guidance
          : "Report not yet ready.",
    };
  }
  if (status === 404) {
    throw new ApiError(404, body, "simulation_not_found");
  }
  throw new ApiError(status, body, undefined);
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
