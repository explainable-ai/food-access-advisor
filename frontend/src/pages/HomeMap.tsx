import { useEffect, useRef, useState } from "react";
import { GeoJSONSource, MapLibreMap, NavigationControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { getImpactMetrics, getTractBoundaries, type FlaggedTractStatus, type ImpactMetrics } from "../lib/api";

type RegionKey = "urban" | "rural";

const REGIONS: Record<
  RegionKey,
  { label: string; countyFips: string; center: [number, number]; zoom: number }
> = {
  // Centers are the rough midpoint of config.py's PILOT_CITY / PILOT_RURAL_COUNTY bboxes.
  urban: { label: "Chicago, IL (Site Advisor)", countyFips: "17031", center: [-87.685, 41.825], zoom: 9 },
  rural: { label: "Alexander County, IL (Route Advisor)", countyFips: "17003", center: [-89.3, 37.15], zoom: 10 },
};

// MapLibre's own free demo vector style -- no API key/signup needed, good
// enough to show real tract polygons against real streets/borders for a
// hackathon demo. Swap for a MapTiler/Maptiler-style key'd style later if a
// more detailed basemap is wanted.
const BASE_STYLE = "https://demotiles.maplibre.org/style.json";

const STATUS_COLORS: Record<FlaggedTractStatus | "unflagged", string> = {
  pending: "#1D5FA8",
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

  // Fetch impact metrics once -- used for both the summary cards and the
  // per-tract status coloring below.
  useEffect(() => {
    getImpactMetrics().then(setMetrics).catch(() => setMetrics(null));
  }, []);

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

  return (
    <div className="home-map-page">
      <div className="region-toggle">
        {(Object.keys(REGIONS) as RegionKey[]).map((key) => (
          <button key={key} className={key === region ? "active" : ""} onClick={() => setRegion(key)}>
            {REGIONS[key].label}
          </button>
        ))}
      </div>

      {boundaryError && (
        <p className="chat-error">
          Couldn't load tract boundaries: {boundaryError}. Run{" "}
          <code>python data/prep_tract_boundaries.py</code> first, then reload.
        </p>
      )}

      <div className="map-and-stats">
        <div ref={mapContainerRef} className="map-container" />
        <div className="stat-cards">
          {regionMetrics ? (
            <>
              <div className="stat-card">
                <div className="n">{regionMetrics.unclosed}</div>
                <div className="l">Unclosed gaps</div>
              </div>
              <div className="stat-card">
                <div className="n">{regionMetrics.resolved}</div>
                <div className="l">Resolved</div>
              </div>
              <div className="stat-card">
                <div className="n">{regionMetrics.total_flagged}</div>
                <div className="l">Total flagged</div>
              </div>
            </>
          ) : (
            <p className="empty">Loading impact metrics...</p>
          )}
          <div className="legend">
            <p className="legend-title">Tract status</p>
            <p>
              <span className="legend-swatch" style={{ background: STATUS_COLORS.pending }} /> pending
            </p>
            <p>
              <span className="legend-swatch" style={{ background: STATUS_COLORS.still_needed }} /> still needed
            </p>
            <p>
              <span className="legend-swatch" style={{ background: STATUS_COLORS.resource_found }} /> resource
              found
            </p>
            <p>
              <span className="legend-swatch" style={{ background: STATUS_COLORS.unflagged }} /> not flagged
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
