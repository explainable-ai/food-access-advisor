// Thin fetch wrapper for the FastAPI backend (api/main.py). Types here are
// hand-mirrored against api/schemas.py -- not generated from the OpenAPI
// schema, since that's a nice-to-have, not required for a first version.

import { getStaffAccessToken } from "./auth";

export type AdvisorResponse = {
  answer: string;
};

export type FlaggedTractStatus = "pending" | "possible_change" | "still_needed" | "resource_found";

export type VerificationChoice = "verified_open" | "planned_not_open" | "incorrect_record" | "unrelated";

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
  possible_change: number;
  resolved: number;
  median_days_to_resolution: number | null;
  tracts: FlaggedTract[];
};

export type ImpactMetrics = {
  urban: RegionMetrics;
  rural: RegionMetrics;
};

// Matches tools/gap_scorer.py's score_gaps() output exactly.
export type RankedTract = {
  tract_fips: string;
  population: number | null;
  low_access_half_mile: number | null;
  low_access_one_mile: number | null;
  centroid_lat: number | null;
  centroid_lon: number | null;
  need_score: number;
  rank: number;
  score_components: Record<string, number | null>;
  score_contributions: Record<string, number>;
  weights_used: Record<string, number>;
  missing_components: string[];
  score_explanation: string;
  sensitivity: { percent: number; score_min: number; score_max: number; rank_best: number; rank_worst: number; rank_stable: boolean };
  nearest_resource_kind: string | null;
  nearest_resource_miles: number | null;
  nearest_resource_minutes: number | null;
  households_no_vehicle?: number | null;
};

// Matches tools/existing_resources.py's row shape exactly.
export type ExistingResource = {
  kind: string;
  name: string;
  lat: number;
  lon: number;
};

export type RouteCandidate = {
  stop_id: string; lat: number; lon: number; demand: number; need_score: number;
  population?: number | null; tract_fips?: string | null;
  currently_served?: boolean; required?: boolean;
};

export type RouteOptimizationRequest = {
  candidates: RouteCandidate[];
  depot: { lat: number; lon: number };
  max_route_minutes: number; vehicle_capacity: number; max_stops: number;
  service_minutes?: number; travel_time_matrix?: number[][]; average_speed_mph?: number;
  travel_time_provider?: "amazon_location" | "estimate";
};

export type RouteOptimizationResponse = {
  status: "optimal" | "infeasible"; reason?: string | null; travel_time_source: string;
  route_minutes?: number; capacity_used?: number; capacity_remaining?: number;
  selected_stops: Array<RouteCandidate & { sequence: number }>;
  unselected_stops: Array<(RouteCandidate & { reason?: string }) | string>;
  coverage_change?: { gained: string[]; lost: string[]; still_uncovered: string[] };
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
// The evidence endpoints call write_evidence_brief/write_route_brief
// directly -- one Bedrock call, not the multi-tool agent loop -- so they
// get a timeout between the two.
const EVIDENCE_TIMEOUT_MS = 30_000;

class ApiError extends Error {}

async function request<T>(path: string, init: RequestInit, timeoutMs: number, staffAuth = false): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = new Headers(init.headers);
    if (staffAuth) headers.set("Authorization", `Bearer ${await getStaffAccessToken()}`);
    const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, signal: controller.signal });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}) as Record<string, unknown>);
      const detail = response.status === 401
        ? "Staff sign-in required."
        : response.status === 403
          ? "Your account does not have staff access."
          : typeof body.detail === "string"
            ? body.detail
            : `${response.status} ${response.statusText}`;
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

// Deterministic ranking (no LLM call) -- the same score_gaps() output the
// corresponding Advisor agent computes internally, exposed directly so the
// ranked table can populate instantly on page load.
export function getSiteRankedTracts(topN = 3): Promise<RankedTract[]> {
  return request<RankedTract[]>(`/api/site-advisor/ranked-tracts?top_n=${topN}`, {}, READ_TIMEOUT_MS);
}

export function getRouteRankedTracts(topN = 3): Promise<RankedTract[]> {
  return request<RankedTract[]>(`/api/route-advisor/ranked-tracts?top_n=${topN}`, {}, READ_TIMEOUT_MS);
}

export function getSiteResources(): Promise<ExistingResource[]> {
  return request<ExistingResource[]>("/api/site-advisor/resources", {}, READ_TIMEOUT_MS);
}

export function getRouteResources(): Promise<ExistingResource[]> {
  return request<ExistingResource[]>("/api/route-advisor/resources", {}, READ_TIMEOUT_MS);
}

export function optimizeRoute(requestBody: RouteOptimizationRequest): Promise<RouteOptimizationResponse> {
  return request<RouteOptimizationResponse>(
    "/api/route-advisor/optimize",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestBody) },
    READ_TIMEOUT_MS,
  );
}

// A single Bedrock call (write_evidence_brief/write_route_brief directly),
// not the full agent loop -- fast enough to call when a user clicks one
// ranked-table row.
export function getSiteEvidence(tract: RankedTract): Promise<{ brief: string }> {
  return request<{ brief: string }>(
    "/api/site-advisor/evidence",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tract }) },
    EVIDENCE_TIMEOUT_MS,
  );
}

export function getRouteEvidence(tract: RankedTract): Promise<{ brief: string }> {
  return request<{ brief: string }>(
    "/api/route-advisor/evidence",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tract }) },
    EVIDENCE_TIMEOUT_MS,
  );
}

export function verifyFlaggedTract(
  tractFips: string,
  recommendationType: string,
  verification: VerificationChoice,
  note?: string,
): Promise<FlaggedTract> {
  return request<FlaggedTract>(
    "/api/flagged-tracts/verify",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tract_fips: tractFips,
        recommendation_type: recommendationType,
        verification,
        note: note ?? "",
      }),
    },
    READ_TIMEOUT_MS,
    true,
  );
}
