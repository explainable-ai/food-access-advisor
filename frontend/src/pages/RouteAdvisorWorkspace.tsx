import { useEffect, useState } from "react";
import {
  askRouteAdvisor,
  getRouteEvidence,
  getRouteRankedTracts,
  getRouteResources,
  type ExistingResource,
  type RankedTract,
} from "../lib/api";
import { ChatBar } from "../components/ChatBar";
import { RankedTractsMap } from "../components/RankedTractsMap";

const ALEXANDER_COUNTY_CENTER: [number, number] = [-89.3, 37.15];

function gapLabel(needScore: number): string {
  if (needScore >= 70) return "High";
  if (needScore >= 40) return "Moderate";
  return "Low";
}

/** Same v1 scope note as SiteAdvisorWorkspace.tsx -- see its docstring:
 * browsing the ranked list + evidence here never flags a tract for
 * follow-up, only the full "Ask the Advisor" agent does that. */
export function RouteAdvisorWorkspace() {
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
    getRouteEvidence(tract)
      .then((res) => setEvidence(res.brief))
      .catch((err) => setEvidenceError(err instanceof Error ? err.message : String(err)))
      .finally(() => setEvidenceLoading(false));
  }

  useEffect(() => {
    Promise.all([getRouteRankedTracts(3), getRouteResources()])
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
      <h1>Route Analysis — Alexander County</h1>
      <p className="page-sub">
        Ranked tracts and their scores come straight from score_gaps -- no LLM call. Click a row for its evidence
        brief, or ask the advisor a free-text question below.
      </p>

      <div className="sample-data-note">
        Running on illustrative sample data — the real rural Atlas database isn't built yet (see the README's
        Roadmap).
      </div>

      <div className="capacity-banner">
        ⚠ A mobile route has fixed stop capacity. Adding a stop here usually means extending the route, adding a
        service day, or replacing a lower-impact stop elsewhere — the planner decides, not this tool.
      </div>

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
          center={ALEXANDER_COUNTY_CENTER}
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
        placeholder="What's the trade-off if we skip Tract Q instead?"
        onAsk={async (question) => {
          const result = await askRouteAdvisor(question);
          return result.answer;
        }}
      />
    </div>
  );
}
