import { useEffect, useState } from "react";
import { getImpactMetrics, type ImpactMetrics } from "../lib/api";
import { FlaggedTractTable } from "../components/FlaggedTractTable";

/**
 * Mirrors dashboard.py/templates/dashboard.html's existing per-region
 * breakdown, in React: /api/impact-metrics already returns exactly what
 * this page needs (unclosed/resolved/total counts plus the raw flagged
 * rows), split by region, not pooled -- reused as-is, no separate
 * per-status fetching needed.
 */
export function FollowUpPage() {
  const [metrics, setMetrics] = useState<ImpactMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getImpactMetrics()
      .then(setMetrics)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) return <p className="chat-error">{error}</p>;
  if (!metrics) return <p className="empty">Loading...</p>;

  return (
    <div className="follow-up-page">
      <h1>Follow-up</h1>
      <p className="page-sub">
        Every tract either Advisor has flagged, and what the Watchdog found the last time it rechecked --
        tracked per region, not pooled, so a rural improvement can never mask a stalled urban one or vice versa.
      </p>

      <div className="regions">
        {(["urban", "rural"] as const).map((region) => (
          <div key={region} className="card">
            <h2>{region}</h2>
            <p className="region-note">
              {region === "urban" ? "Site Advisor recommendations" : "Route Advisor recommendations"}
            </p>
            <div className="stat-row">
              <div className="stat unclosed">
                <div className="n">{metrics[region].unclosed}</div>
                <div className="l">Unclosed gaps</div>
              </div>
              <div className="stat resolved">
                <div className="n">{metrics[region].resolved}</div>
                <div className="l">Resolved</div>
              </div>
              <div className="stat">
                <div className="n">{metrics[region].total_flagged}</div>
                <div className="l">Total flagged</div>
              </div>
            </div>
            <p className="median">
              Median days to resolution:{" "}
              <b>{metrics[region].median_days_to_resolution ?? "—"}</b>
            </p>
            <FlaggedTractTable tracts={metrics[region].tracts} />
          </div>
        ))}
      </div>
    </div>
  );
}
