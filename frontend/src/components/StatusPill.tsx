import type { FlaggedTractStatus } from "../lib/api";

const LABELS: Record<FlaggedTractStatus, string> = {
  pending: "pending",
  still_needed: "still needed",
  resource_found: "resource found",
};

export function StatusPill({ status }: { status: FlaggedTractStatus }) {
  return <span className={`status-pill status-${status}`}>{LABELS[status]}</span>;
}
