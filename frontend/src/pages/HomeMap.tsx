import { useEffect, useRef, useState } from "react";
import { GeoJSONSource, MapLibreMap, NavigationControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
  getImpactMetrics,
  getSiteRankedTracts,
  getRouteRankedTracts,
  getSiteResources,
  getRouteResources,
  getTractBoundaries,
  type ExistingResource,
  type FlaggedTractStatus,
  type ImpactMetrics,
  type RankedTract,
} from "../lib/api";

type RegionKey = "urban" | "rural";

const REGIONS: Record<
  RegionKey,
  {
    toggleLabel: string;
    countyFips: string;
    center: [number, number];
    zoom: number;
    getRankedTracts: (topN?: number) => Promise<RankedTract[]>;
    getResources: () => Promise<ExistingResource[]>;
  }
> = {
  // Centers are the rough midpoint of config.py's PILOT_CITY / PILOT_RURAL_COUNTY bboxes.
  // "Fixed site / mobile route" and "urban / rural" are the same axis in this
  // system today -- there's no independent combination in the real data, so
  // the wireframe's two separate filters collapse into one real toggle here.
  urban: {
    toggleLabel: "Chicago · Fixed site",
    countyFips: "17031",
    center: [-87.685, 41.825],
    zoom: 9,
    getRankedTracts: getSiteRankedTracts,
    getResources: getSiteResources,
  },
  rural: {
    toggleLabel: "Chicagoland rural fringe · Mobile route",
    countyFips: "17089",
    center: [-89.3, 37.15],
    zoom: 10,
    getRankedTracts: getRouteRankedTracts,
    getResources: getRouteResources,
  },
};

// The tract-fetch tools this project has today cap out at a limit (see
// tools/access_data.py), so this is an honest "top N ranked" count, not a
// claim about the true total number of low-access tracts in the region.
const RANKED_TRACTS_SAMPLE_SIZE = 25;

// MapLibre's own free demo vector style -- no API key/signup needed, good
// enough to show real tract polygons against real streets/borders for a
// hackathon demo. Swap for a MapTiler/Maptiler-style key'd style later if a
// more detailed basemap is wanted.
const BASE_STYLE = "https://demotiles.maplibre.org/style.json";

const STATUS_COLORS: Record<FlaggedTractStatus | "unflagged", string> = {
  pending: "#1D5FA8",
  possible_change: "#5B3FA8",
  still_needed: "#A85B12",
  resource_found: "#2A7D68",
  unflagged: "#C9D2DC",
};

function buildStatusByFips(metrics: ImpactMetrics, region: RegionKey): Record<string, FlaggedTractStatus> {
  const byFips: Record<string, FlaggedTractStatus> = {};
  for (const tract of metrics[region].tracts) {
    byFips[tract.tract_fips] = tract.status;
  }
  return byFips;
}

export function HomeMap() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [region, setRegion] = useState<RegionKey>("urban");
  const [metrics, setMetrics] = useState<ImpactMetrics | null>(null);
  const [rankedTracts, setRankedTracts] = useState<RankedTract[] | null>(null);
  const [resources, setResources] = useState<ExistingResource[] | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);

  // Initialize the map once.
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;
    const map = new MapLibreMap({
      container: mapContainerRef.current,
      style: BASE_STYLE,
      center: REGIONS.urban.center,
      zoom: REGIONS.urban.zoom,
    });
    map.addControl(new NavigationControl(), "top-right");
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Fetch impact metrics once -- used for both the follow-up stat and the
  // per-tract status coloring below.
  useEffect(() => {
    getImpactMetrics().then(setMetrics).catch(() => setMetrics(null));
  }, []);

  // Fetch the region's ranked tracts + resources whenever it changes.
  useEffect(() => {
    setSummaryError(null);
    setRankedTracts(null);
    setResources(null);
    const config = REGIONS[region];
    Promise.all([config.getRankedTracts(RANKED_TRACTS_SAMPLE_SIZE), config.getResources()])
      .then(([tracts, res]) => {
        setRankedTracts(tracts);
        setResources(res);
      })
      .catch((err) => setSummaryError(err instanceof Error ? err.message : String(err)));
  }, [region]);

  // Load and (re)draw tract boundaries whenever the selected region changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const config = REGIONS[region];

    map.flyTo({ center: config.center, zoom: config.zoom });

    function draw() {
      if (!map || !map.isStyleLoaded()) {
        map?.once("load", draw);
        return;
      }
      setBoundaryError(null);
      getTractBoundaries(config.countyFips)
        .then((boundaries) => {
          const statusByFips = metrics ? buildStatusByFips(metrics, region) : {};
          const withStatus = {
            ...boundaries,
            features: boundaries.features.map((f) => ({
              ...f,
              properties: {
                ...f.properties,
                status: statusByFips[f.properties.tract_fips] ?? "unflagged",
              },
            })),
          };

          const existingSource = map.getSource("tracts") as GeoJSONSource | undefined;
          if (existingSource) {
            existingSource.setData(withStatus as never);
            return;
          }

          map.addSource("tracts", { type: "geojson", data: withStatus as never });
          map.addLayer({
            id: "tracts-fill",
            type: "fill",
            source: "tracts",
            paint: {
              "fill-color": [
                "match",
                ["get", "status"],
                "pending",
                STATUS_COLORS.pending,
                "possible_change",
                STATUS_COLORS.possible_change,
                "still_needed",
                STATUS_COLORS.still_needed,
                "resource_found",
                STATUS_COLORS.resource_found,
                STATUS_COLORS.unflagged,
              ],
              "fill-opacity": 0.55,
            },
          });
          map.addLayer({
            id: "tracts-outline",
            type: "line",
            source: "tracts",
            paint: { "line-color": "#17212B", "line-width": 1 },
          });
        })
        .catch((err) => setBoundaryError(err instanceof Error ? err.message : String(err)));
    }

    draw();
  }, [region, metrics]);

  const regionMetrics = metrics?.[region];
  const populationServed = rankedTracts?.reduce((sum, t) => sum + (t.population ?? 0), 0) ?? null;
  const awaitingFollowUp = regionMetrics ? regionMetrics.unclosed + regionMetrics.possible_change : null;

  return (
    <div className="home-map-page">
      <div className="region-toggle">
        {(Object.keys(REGIONS) as RegionKey[]).map((key) => (
          <button key={key} className={key === region ? "active" : ""} onClick={() => setRegion(key)}>
            {REGIONS[key].toggleLabel}
          </button>
        ))}
      </div>

      {boundaryError && (
        <p className="chat-error">
          Couldn't load tract boundaries: {boundaryError}. Run{" "}
          <code>python data/prep_tract_boundaries.py</code> first, then reload.
        </p>
      )}
      {summaryError && <p className="chat-error">Couldn't load summary stats: {summaryError}</p>}

      <div className="overview-columns">
        <div className="filter-box">
          <div>Fixed site / Mobile route</div>
          <div>Urban / Rural</div>
          <span className="coming-soon">set via the toggle above</span>
        </div>

        <div ref={mapContainerRef} className="map-container" />

        <div className="summary-stats">
          <div className="stat-card">
            <div className="n">{rankedTracts ? rankedTracts.length : "…"}</div>
            <div className="l">Low-access tracts (top {RANKED_TRACTS_SAMPLE_SIZE})</div>
          </div>
          <div className="stat-card">
            <div className="n">{populationServed !== null ? populationServed.toLocaleString() : "…"}</div>
            <div className="l">Population potentially served</div>
          </div>
          <div className="stat-card">
            <div className="n">{resources ? resources.length : "…"}</div>
            <div className="l">Existing resources identified</div>
          </div>
          <div className="stat-card">
            <div className="n">{awaitingFollowUp ?? "…"}</div>
            <div className="l">Recommendations awaiting follow-up</div>
          </div>
        </div>
      </div>

      <div className="filter-box disabled" style={{ marginTop: 16, maxWidth: 420 }}>
        <div>Resource type: grocery, pantry, garden, mobile</div>
        <div>Service radius / stop count</div>
        <span className="coming-soon">not wired to real filtering yet</span>
      </div>

      <div className="legend" style={{ marginTop: 16, maxWidth: 300 }}>
        <p className="legend-title">Tract status</p>
        <p>
          <span className="legend-swatch" style={{ background: STATUS_COLORS.pending }} /> pending
        </p>
        <p>
          <span className="legend-swatch" style={{ background: STATUS_COLORS.possible_change }} /> possible change
        </p>
        <p>
          <span className="legend-swatch" style={{ background: STATUS_COLORS.still_needed }} /> still needed
        </p>
        <p>
          <span className="legend-swatch" style={{ background: STATUS_COLORS.resource_found }} /> resource found
        </p>
        <p>
          <span className="legend-swatch" style={{ background: STATUS_COLORS.unflagged }} /> not flagged
        </p>
      </div>
    </div>
  );
}
