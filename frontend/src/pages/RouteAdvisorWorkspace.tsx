import { useEffect, useMemo, useState } from "react";
import {
  askRouteAdvisor, getRouteEvidence, getRouteRankedTracts, getRouteResources, optimizeRoute,
  type ExistingResource, type RankedTract, type RouteOptimizationResponse,
} from "../lib/api";
import { ChatBar } from "../components/ChatBar";
import { RankedTractsMap } from "../components/RankedTractsMap";

const ALEXANDER_COUNTY_CENTER: [number, number] = [-89.3, 37.15];
const DEFAULT_DEPOT = { lat: 37.0059, lon: -89.177 };

type StopDraft = { included: boolean; demand: number; required: boolean; currentlyServed: boolean };

function gapLabel(score: number) {
  return score >= 70 ? "High" : score >= 40 ? "Moderate" : "Low";
}

export function RouteAdvisorWorkspace() {
  const [tracts, setTracts] = useState<RankedTract[]>([]);
  const [resources, setResources] = useState<ExistingResource[]>([]);
  const [selected, setSelected] = useState<RankedTract | null>(null);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, StopDraft>>({});
  const [depot, setDepot] = useState(DEFAULT_DEPOT);
  const [maxMinutes, setMaxMinutes] = useState(240);
  const [capacity, setCapacity] = useState(500);
  const [maxStops, setMaxStops] = useState(3);
  const [serviceMinutes, setServiceMinutes] = useState(20);
  const [provider, setProvider] = useState<"amazon_location" | "estimate">("amazon_location");
  const [plan, setPlan] = useState<RouteOptimizationResponse | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeError, setOptimizeError] = useState<string | null>(null);

  function selectTract(tract: RankedTract) {
    setSelected(tract); setEvidence(null); setEvidenceError(null); setEvidenceLoading(true);
    getRouteEvidence(tract).then((res) => setEvidence(res.brief))
      .catch((err) => setEvidenceError(err instanceof Error ? err.message : String(err)))
      .finally(() => setEvidenceLoading(false));
  }

  useEffect(() => {
    Promise.all([getRouteRankedTracts(10), getRouteResources()])
      .then(([nextTracts, nextResources]) => {
        setTracts(nextTracts); setResources(nextResources);
        setDrafts(Object.fromEntries(nextTracts.map((tract) => [tract.tract_fips, {
          included: tract.centroid_lat != null && tract.centroid_lon != null,
          demand: tract.households_no_vehicle ?? 0, required: false, currentlyServed: false,
        }])));
        if (nextTracts.length) selectTract(nextTracts[0]);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const includedCount = useMemo(() => Object.values(drafts).filter((draft) => draft.included).length, [drafts]);

  function updateDraft(fips: string, update: Partial<StopDraft>) {
    setDrafts((current) => ({ ...current, [fips]: { ...current[fips], ...update } }));
    setPlan(null);
  }

  async function runOptimization() {
    const candidates = tracts.filter((tract) => drafts[tract.tract_fips]?.included)
      .map((tract) => ({
        stop_id: tract.tract_fips, tract_fips: tract.tract_fips,
        lat: tract.centroid_lat!, lon: tract.centroid_lon!,
        demand: drafts[tract.tract_fips].demand, need_score: tract.need_score,
        population: tract.population, required: drafts[tract.tract_fips].required,
        currently_served: drafts[tract.tract_fips].currentlyServed,
      })).filter((candidate) => candidate.demand > 0);
    if (!candidates.length) {
      setOptimizeError("Include at least one stop with a demand greater than zero.");
      return;
    }
    setOptimizing(true); setOptimizeError(null); setPlan(null);
    try {
      setPlan(await optimizeRoute({
        candidates, depot, max_route_minutes: maxMinutes, vehicle_capacity: capacity,
        max_stops: maxStops, service_minutes: serviceMinutes, travel_time_provider: provider,
      }));
    } catch (err) {
      setOptimizeError(err instanceof Error ? err.message : String(err));
    } finally {
      setOptimizing(false);
    }
  }

  const routeStopIds = plan?.selected_stops.map((stop) => stop.stop_id) ?? [];

  return (
    <div className="workspace-page">
      <h1>Route Scenario Builder — Alexander County</h1>
      <p className="page-sub">Compare service scenarios using ranked need, operational constraints, and AWS road-network travel times.</p>
      <div className="capacity-banner">The optimizer provides decision support. A planner must confirm stop safety, operating hours, demand, and community preference.</div>
      {loadError && <p className="chat-error">Couldn't load ranked tracts: {loadError}</p>}

      <section className="route-builder">
        <div className="route-controls">
          <h2>1. Operating constraints</h2>
          <div className="control-grid">
            <label>Depot latitude<input type="number" step="0.0001" value={depot.lat} onChange={(e) => setDepot({ ...depot, lat: Number(e.target.value) })} /></label>
            <label>Depot longitude<input type="number" step="0.0001" value={depot.lon} onChange={(e) => setDepot({ ...depot, lon: Number(e.target.value) })} /></label>
            <label>Maximum route minutes<input type="number" min="1" value={maxMinutes} onChange={(e) => setMaxMinutes(Number(e.target.value))} /></label>
            <label>Vehicle capacity<input type="number" min="1" value={capacity} onChange={(e) => setCapacity(Number(e.target.value))} /></label>
            <label>Maximum stops<input type="number" min="1" max="15" value={maxStops} onChange={(e) => setMaxStops(Number(e.target.value))} /></label>
            <label>Minutes per stop<input type="number" min="0" value={serviceMinutes} onChange={(e) => setServiceMinutes(Number(e.target.value))} /></label>
            <label>Travel-time source<select value={provider} onChange={(e) => setProvider(e.target.value as "amazon_location" | "estimate")}>
              <option value="amazon_location">Amazon Location road network</option>
              <option value="estimate">Haversine estimate (demo)</option>
            </select></label>
          </div>
        </div>

        <div className="route-candidates">
          <h2>2. Candidate stops <span>{includedCount} included</span></h2>
          <div className="candidate-scroll">
            <table className="ranked-table">
              <thead><tr><th>Use</th><th>Tract</th><th>Need</th><th>Demand</th><th>Existing</th><th>Required</th></tr></thead>
              <tbody>{tracts.map((tract) => {
                const draft = drafts[tract.tract_fips];
                if (!draft) return null;
                return <tr key={tract.tract_fips} className="selectable" onClick={() => selectTract(tract)}>
                  <td><input aria-label={`Include ${tract.tract_fips}`} type="checkbox" checked={draft.included}
                    disabled={tract.centroid_lat == null || tract.centroid_lon == null}
                    onClick={(e) => e.stopPropagation()} onChange={(e) => updateDraft(tract.tract_fips, { included: e.target.checked })} /></td>
                  <td className="fips">{tract.tract_fips}</td><td>{tract.need_score.toFixed(1)} · {gapLabel(tract.need_score)}</td>
                  <td><input aria-label={`Demand ${tract.tract_fips}`} className="demand-input" type="number" min="0" value={draft.demand}
                    onClick={(e) => e.stopPropagation()} onChange={(e) => updateDraft(tract.tract_fips, { demand: Number(e.target.value) })} /></td>
                  <td><input aria-label={`Existing ${tract.tract_fips}`} type="checkbox" checked={draft.currentlyServed}
                    onClick={(e) => e.stopPropagation()} onChange={(e) => updateDraft(tract.tract_fips, { currentlyServed: e.target.checked })} /></td>
                  <td><input aria-label={`Required ${tract.tract_fips}`} type="checkbox" checked={draft.required}
                    onClick={(e) => e.stopPropagation()} onChange={(e) => updateDraft(tract.tract_fips, { required: e.target.checked })} /></td>
                </tr>;
              })}</tbody>
            </table>
          </div>
          <p className="field-note">Demand is households or service units. ACS no-vehicle households prefill it when available; otherwise enter a planning estimate.</p>
          <button className="optimize-button" onClick={runOptimization} disabled={optimizing}>{optimizing ? "Optimizing…" : "Optimize route"}</button>
          {optimizeError && <p className="chat-error">{optimizeError}</p>}
        </div>

        <div className="route-map-panel">
          <h2>3. Optimized scenario</h2>
          <RankedTractsMap tracts={tracts} resources={resources} center={ALEXANDER_COUNTY_CENTER} zoom={9}
            selectedFips={selected?.tract_fips ?? null} routeStopIds={routeStopIds} depot={depot} />
          <p className="field-note">The line shows stop sequence; it is not turn-by-turn road geometry.</p>
          {!plan && <p className="empty">Set demand and constraints, then optimize.</p>}
          {plan?.status === "infeasible" && <p className="chat-error">{plan.reason}</p>}
          {plan?.status === "optimal" && <div className="route-results">
            <div><strong>{plan.selected_stops.length}</strong><span>stops</span></div>
            <div><strong>{plan.route_minutes}</strong><span>route minutes</span></div>
            <div><strong>{plan.capacity_used}</strong><span>capacity used</span></div>
            <div><strong>{plan.coverage_change?.gained.length ?? 0}</strong><span>coverage gains</span></div>
            <p className="provider-label">Source: {plan.travel_time_source.replaceAll("_", " ")}</p>
            <ol>{plan.selected_stops.map((stop) => <li key={stop.stop_id}>{stop.stop_id} — demand {stop.demand}</li>)}</ol>
          </div>}
        </div>
      </section>

      <div className="evidence-panel">
        <p className="evidence-panel-label">Evidence{selected ? ` — tract ${selected.tract_fips}` : ""}</p>
        {evidenceLoading && <p className="chat-status">Generating evidence brief...</p>}
        {evidenceError && <p className="chat-error">{evidenceError}</p>}
        {evidence && <p className="evidence-panel-text">{evidence}</p>}
      </div>
      <ChatBar placeholder="What's the trade-off if we preserve the current stop?" onAsk={async (question) => (await askRouteAdvisor(question)).answer} />
    </div>
  );
}
