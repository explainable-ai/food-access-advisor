import { useEffect, useState } from "react";
import { askSiteAdvisor, getFlaggedTracts, type FlaggedTract } from "../lib/api";
import { ChatBar } from "../components/ChatBar";
import { FlaggedTractTable } from "../components/FlaggedTractTable";

/**
 * v1 scope note: POST /api/site-advisor returns the advisor's composed
 * text answer (its ranked recommendation plus the cited evidence brief,
 * exactly as tools/evidence_brief.py's write_evidence_brief produces it)
 * -- not score_gaps's structured per-tract JSON, since no endpoint exposes
 * that separately today. So this renders the answer as one evidence
 * panel rather than a clickable per-row ranked table; a structured
 * ranking table is a natural fast-follow once a
 * GET /api/site-advisor/ranked-tracts-style endpoint exists.
 */
export function SiteAdvisorWorkspace() {
  const [flagged, setFlagged] = useState<FlaggedTract[]>([]);

  useEffect(() => {
    Promise.all([
      getFlaggedTracts("pending"),
      getFlaggedTracts("still_needed"),
      getFlaggedTracts("resource_found"),
    ])
      .then((results) => results.flat().filter((t) => t.recommendation_type === "site"))
      .then(setFlagged)
      .catch(() => setFlagged([]));
  }, []);

  return (
    <div className="workspace-page">
      <h1>Site Advisor</h1>
      <p className="page-sub">
        Ask where a new fixed food resource (a farm, market, or food-rescue drop point) would do the most good in
        the urban pilot city.
      </p>

      <ChatBar
        placeholder="Where's the highest-need spot for a new food resource?"
        onAsk={async (question) => {
          const result = await askSiteAdvisor(question);
          return result.answer;
        }}
      />

      <h2>Follow-up records for this workspace</h2>
      <FlaggedTractTable tracts={flagged} />
    </div>
  );
}
