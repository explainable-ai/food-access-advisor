import { useEffect, useState } from "react";
import {
  askSiteAdvisor,
  getSiteEvidence,
  getSiteRankedTracts,
  getSiteResources,
  type ExistingResource,
  type RankedTract,
} from "../lib/api";
import { ChatBar } from "../components/ChatBar";
import { RankedTractsMap } from "../components/RankedTractsMap";

const CHICAGO_CENTER: [number, number] = [-87.685, 41.825];

function gapLabel(needScore: number): string {
  if (needScore >= 70) return "High";
  if (needScore >= 40) return "Moderate";
  return "Low";
}

/**
 * v1 scope note: the ranked table and evidence panel below use the new
 * deterministic ranked-tracts endpoint and the single-call evidence
 * endpoint -- neither of these flags a tract for follow-up (only the full
 * Advisor, via the "Ask the Advisor" box below, calls
 * flag_top_tract_for_recheck as part of its own tool sequence). So this
 * view intentionally does NOT show a "flagged for follow-up" pill the way
 * the wireframe's evidence panel does -- that would misrepresent what
 * browsing the ranked list actually does.
 */
export function SiteAdvisorWorkspace() {
  const [tracts, setTracts] = useState<RankedTract[]>([]);
  const [resources, setResources] = useState<ExistingResource[]>([]);
  const [selected, setSelected] = useState<RankedTract | null>(null);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  function selectTract(tract: RankedTract) {
    setSelected(tract);
    setEvidence(null);
    setEvidenceError(null);
    setEvidenceLoading(true);
    getSiteEvidence(tract)
      .then((res) => setEvidence(res.brief))
      .catch((err) => setEvidenceError(err instanceof Error ? err.message : String(err)))
      .finally(() => setEvidenceLoading(false));
  }

  useEffect(() => {
    Promise.all([getSiteRankedTracts(3), getSiteResources()])
      .then(([t, r]) => {
        setTracts(t);
        setResources(r);
        if (t.length > 0) selectTract(t[0]);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="workspace-page">
      <h1>Site Analysis — Chicago</h1>
      <p className="page-sub">
        Ranked tracts and their scores come straight from score_gaps -- no LLM call. Click a row for its evidence
        brief, or ask the advisor a free-text question below.
      </p>

      {loadError && <p className="chat-error">Couldn't load ranked tracts: {loadError}</p>}

      <div className="workspace-columns">
        <table className="ranked-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Tract</th>
              <th>Gap</th>
              <th>Resources</th>
            </tr>
          </thead>
          <tbody>
            {tracts.map((t, i) => (
              <tr
                key={t.tract_fips}
                className={`selectable${selected?.tract_fips === t.tract_fips ? " selected" : ""}`}
                onClick={() => selectTract(t)}
              >
                <td>{i + 1}</td>
                <td className="fips">{t.tract_fips}</td>
                <td>{gapLabel(t.need_score)}</td>
                <td>{t.nearest_resource_kind ? `${t.nearest_resource_kind} nearby` : "none nearby"}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <RankedTractsMap
          tracts={tracts}
          resources={resources}
          center={CHICAGO_CENTER}
          zoom={10}
          selectedFips={selected?.tract_fips ?? null}
        />

        <div className="evidence-panel" style={{ margin: 0 }}>
          <p className="evidence-panel-label">Evidence{selected ? ` — tract ${selected.tract_fips}` : ""}</p>
          {evidenceLoading && <p className="chat-status">Generating evidence brief...</p>}
          {evidenceError && <p className="chat-error">{evidenceError}</p>}
          {evidence && <p className="evidence-panel-text">{evidence}</p>}
          {!selected && !evidenceLoading && <p className="empty">Select a ranked tract to see its evidence.</p>}
        </div>
      </div>

      <ChatBar
        placeholder="Why does tract 1 rank above tract 2?"
        onAsk={async (question) => {
          const result = await askSiteAdvisor(question);
          return result.answer;
        }}
      />
    </div>
  );
}
