import type { FlaggedTract } from "../lib/api";
import { StatusPill } from "./StatusPill";

export function FlaggedTractTable({ tracts }: { tracts: FlaggedTract[] }) {
  if (tracts.length === 0) {
    return <p className="empty">Nothing flagged yet.</p>;
  }

  return (
    <table className="tract-table">
      <thead>
        <tr>
          <th>Tract</th>
          <th>Type</th>
          <th>Population</th>
          <th>Flagged</th>
          <th>Last checked</th>
          <th>Status</th>
          <th>Note</th>
        </tr>
      </thead>
      <tbody>
        {tracts.map((tract) => (
          <tr key={`${tract.tract_fips}-${tract.recommendation_type}`}>
            <td className="fips">{tract.tract_fips}</td>
            <td>{tract.recommendation_type}</td>
            <td>{tract.population ?? "—"}</td>
            <td>{tract.flagged_date}</td>
            <td>{tract.last_checked_date ?? "—"}</td>
            <td>
              <StatusPill status={tract.status} />
            </td>
            <td className="note">{tract.note ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
