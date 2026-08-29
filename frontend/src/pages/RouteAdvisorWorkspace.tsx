import { useEffect, useState } from "react";
import { askRouteAdvisor, getFlaggedTracts, type FlaggedTract } from "../lib/api";
import { ChatBar } from "../components/ChatBar";
import { FlaggedTractTable } from "../components/FlaggedTractTable";

/** Same v1 scope note as SiteAdvisorWorkspace.tsx -- see its docstring. */
export function RouteAdvisorWorkspace() {
  const [flagged, setFlagged] = useState<FlaggedTract[]>([]);

  useEffect(() => {
    Promise.all([
      getFlaggedTracts("pending"),
      getFlaggedTracts("still_needed"),
      getFlaggedTracts("resource_found"),
    ])
      .then((results) => results.flat().filter((t) => t.recommendation_type === "route"))
      .then(setFlagged)
      .catch(() => setFlagged([]));
  }, []);

  return (
    <div className="workspace-page">
      <h1>Route Advisor</h1>
      <p className="page-sub">
        Ask where a route or distribution-schedule change -- a mobile market stop, a food-bank delivery day --
        would do the most good in the rural pilot county. A mobile route has fixed stop capacity, so the
        advisor's answer will name the trade-off explicitly.
      </p>

      <ChatBar
        placeholder="Where would a route change help most?"
        onAsk={async (question) => {
          const result = await askRouteAdvisor(question);
          return result.answer;
        }}
      />

      <h2>Follow-up records for this workspace</h2>
      <FlaggedTractTable tracts={flagged} />
    </div>
  );
}
