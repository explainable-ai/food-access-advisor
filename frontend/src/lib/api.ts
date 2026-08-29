// Thin fetch wrapper for the FastAPI backend (api/main.py). Types here are
// hand-mirrored against api/schemas.py -- not generated from the OpenAPI
// schema, since that's a nice-to-have, not required for a first version.

export type AdvisorResponse = {
  answer: string;
};

export type FlaggedTractStatus = "pending" | "resource_found" | "still_needed";

export type FlaggedTract = {
  id: number | null;
  tract_fips: string;
  recommendation_type: string;
  source_agent: string;
  population: number | null;
  centroid_lat: number | null;
  centroid_lon: number | null;
  flagged_date: string;
  status: FlaggedTractStatus;
  last_checked_date: string | null;
  note: string | null;
};

export type RegionMetrics = {
  total_flagged: number;
  unclosed: number;
  resolved: number;
  median_days_to_resolution: number | null;
  tracts: FlaggedTract[];
};

export type ImpactMetrics = {
  urban: RegionMetrics;
  rural: RegionMetrics;
};

export type TractBoundaryFeature = {
  type: "Feature";
  properties: { tract_fips: string };
  geometry: { type: string; coordinates: unknown };
};

export type TractBoundaries = {
  type: "FeatureCollection";
  features: TractBoundaryFeature[];
};

const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8000";

// Advisor calls run the full agent tool-calling loop plus at least one
// Bedrock round trip -- realistically several seconds to over a minute
// (see api/main.py's module docstring). Reads (flagged tracts, impact
// metrics, tract boundaries) are plain synchronous DB/file reads and stay
// fast, so they get a much shorter timeout.
const ADVISOR_TIMEOUT_MS = 120_000;
const READ_TIMEOUT_MS = 15_000;

class ApiError extends Error {}

async function request<T>(path: string, init: RequestInit, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { ...init, signal: controller.signal });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}) as Record<string, unknown>);
      const detail = typeof body.detail === "string" ? body.detail : `${response.status} ${response.statusText}`;
      throw new ApiError(detail);
    }
    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(`Request to ${path} timed out after ${timeoutMs / 1000}s`);
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

export function askSiteAdvisor(question: string): Promise<AdvisorResponse> {
  return request<AdvisorResponse>(
    "/api/site-advisor",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    },
    ADVISOR_TIMEOUT_MS,
  );
}

export function askRouteAdvisor(question: string): Promise<AdvisorResponse> {
  return request<AdvisorResponse>(
    "/api/route-advisor",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    },
    ADVISOR_TIMEOUT_MS,
  );
}

export function getFlaggedTracts(status: FlaggedTractStatus): Promise<FlaggedTract[]> {
  return request<FlaggedTract[]>(`/api/flagged-tracts?status=${encodeURIComponent(status)}`, {}, READ_TIMEOUT_MS);
}

export function getImpactMetrics(): Promise<ImpactMetrics> {
  return request<ImpactMetrics>("/api/impact-metrics", {}, READ_TIMEOUT_MS);
}

export function getTractBoundaries(countyFips: string): Promise<TractBoundaries> {
  return request<TractBoundaries>(`/api/tract-boundaries?county=${encodeURIComponent(countyFips)}`, {}, READ_TIMEOUT_MS);
}
