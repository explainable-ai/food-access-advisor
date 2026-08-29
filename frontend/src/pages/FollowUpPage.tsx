import { Fragment, useEffect, useState } from "react";
import { getImpactMetrics, verifyFlaggedTract, type FlaggedTract, type ImpactMetrics, type VerificationChoice } from "../lib/api";
import { StatusPill } from "../components/StatusPill";

const REGION_LABEL: Record<"urban" | "rural", string> = {
  urban: "Chicago",
  rural: "Alexander Co.",
};

const VERIFY_ACTIONS: { choice: VerificationChoice; label: string }[] = [
  { choice: "verified_open", label: "verified open" },
  { choice: "planned_not_open", label: "planned, not open" },
  { choice: "incorrect_record", label: "incorrect record" },
  { choice: "unrelated", label: "unrelated" },
];

type Row = FlaggedTract & { region: "urban" | "rural" };

// last_checked_date is stored as a full ISO datetime (see
// tools/flagged_tracts.py); flagged_date is already a bare date. Trim to
// just the date part for display -- no date library needed for that.
function dateOnly(value: string | null): string {
  return value ? value.split("T")[0] : "—";
}

/**
 * Wireframe 3d: one unified table (Site + Route rows together, not two
 * side-by-side region cards) with a row-expand revealing the four human
 * verification actions. Reuses GET /api/impact-metrics for the row data
 * (already split by region -- rows are tagged with their region here just
 * for the "recommendation" label and re-fetched after each verify action
 * to keep the table exact rather than hand-patching local state).
 */
export function FollowUpPage() {
  const [metrics, setMetrics] = useState<ImpactMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  function load() {
    getImpactMetrics()
      .then(setMetrics)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }

  useEffect(load, []);

  if (error) return <p className="chat-error">{error}</p>;
  if (!metrics) return <p className="empty">Loading...</p>;

  const rows: Row[] = [
    ...metrics.urban.tracts.map((t) => ({ ...t, region: "urban" as const })),
    ...metrics.rural.tracts.map((t) => ({ ...t, region: "rural" as const })),
  ];

  function rowKey(row: Row): string {
    return `${row.tract_fips}-${row.recommendation_type}`;
  }

  async function handleVerify(row: Row, choice: VerificationChoice) {
    setVerifyBusy(true);
    setVerifyError(null);
    try {
      await verifyFlaggedTract(row.tract_fips, row.recommendation_type, choice);
      setExpandedKey(null);
      load();
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : String(err));
    } finally {
      setVerifyBusy(false);
    }
  }

  return (
    <div className="follow-up-page">
      <h1>Follow-up</h1>
      <p className="page-sub">
        Every tract either Advisor has flagged, and what the Watchdog found the last time it rechecked. A Watchdog
        observation is reported as a "possible change" awaiting your verification — not proof, until you confirm it.
      </p>

      {verifyError && <p className="chat-error">{verifyError}</p>}

      <table className="followup-table">
        <thead>
          <tr>
            <th>Recommendation</th>
            <th>Type</th>
            <th>Flagged</th>
            <th>Checked</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">
                Nothing flagged yet.
              </td>
            </tr>
          )}
          {rows.map((row) => {
            const key = rowKey(row);
            const expanded = expandedKey === key;
            return (
              <Fragment key={key}>
                <tr className="expandable" onClick={() => setExpandedKey(expanded ? null : key)}>
                  <td>
                    {REGION_LABEL[row.region]} tract {row.tract_fips}
                  </td>
                  <td style={{ textTransform: "capitalize" }}>{row.recommendation_type}</td>
                  <td>{row.flagged_date}</td>
                  <td>{dateOnly(row.last_checked_date)}</td>
                  <td>
                    <StatusPill status={row.status} />
                  </td>
                </tr>
                {expanded && (
                  <tr className="verify-row">
                    <td colSpan={5}>
                      {row.note && <p style={{ margin: "0 0 8px", color: "var(--ink-soft)" }}>Note: {row.note}</p>}
                      <div className="verify-actions">
                        {VERIFY_ACTIONS.map((action) => (
                          <button
                            key={action.choice}
                            disabled={verifyBusy}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleVerify(row, action.choice);
                            }}
                          >
                            {action.label}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      <div className="regions" style={{ marginTop: 20 }}>
        {(["urban", "rural"] as const).map((region) => (
          <div key={region} className="card">
            <h2>{region}</h2>
            <p className="region-note">
              {region === "urban" ? "Site Advisor recommendations" : "Route Advisor recommendations"}
            </p>
            <div className="stat-row">
              <div className="stat unclosed">
                <div className="n">{metrics[region].unclosed}</div>
                <div className="l">Unclosed</div>
              </div>
              <div className="stat">
                <div className="n">{metrics[region].possible_change}</div>
                <div className="l">Possible change</div>
              </div>
              <div className="stat resolved">
                <div className="n">{metrics[region].resolved}</div>
                <div className="l">Resolved</div>
              </div>
            </div>
            <p className="median">
              Median days to resolution: <b>{metrics[region].median_days_to_resolution ?? "—"}</b>
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
