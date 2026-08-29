import { useEffect, useRef } from "react";
import { MapLibreMap, Marker, type GeoJSONSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { ExistingResource, RankedTract } from "../lib/api";

const BASE_STYLE = "https://demotiles.maplibre.org/style.json";

type Props = {
  tracts: RankedTract[];
  resources: ExistingResource[];
  center: [number, number];
  zoom: number;
  selectedFips: string | null;
  routeStopIds?: string[];
  depot?: { lat: number; lon: number };
};

/**
 * A lighter-weight map than HomeMap's -- just numbered markers for the
 * ranked tracts plus dots for existing resources, matching wireframe
 * 3b/3c's map column (pins, not shaded tract polygons -- that full
 * choropleth treatment is HomeMap's job).
 */
export function RankedTractsMap({ tracts, resources, center, zoom, selectedFips, routeStopIds = [], depot }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef<Marker[]>([]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new MapLibreMap({
      container: containerRef.current,
      style: BASE_STYLE,
      center,
      zoom,
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];

    resources.forEach((r) => {
      const el = document.createElement("div");
      el.className = "map-marker resource-marker";
      const marker = new Marker({ element: el }).setLngLat([r.lon, r.lat]).addTo(map);
      markersRef.current.push(marker);
    });

    if (depot) {
      const el = document.createElement("div");
      el.className = "map-marker depot-marker";
      el.textContent = "D";
      markersRef.current.push(new Marker({ element: el }).setLngLat([depot.lon, depot.lat]).addTo(map));
    }

    tracts.forEach((t, i) => {
      if (t.centroid_lat == null || t.centroid_lon == null) return;
      const el = document.createElement("div");
      const routeIndex = routeStopIds.indexOf(t.tract_fips);
      el.className = `map-marker tract-marker${t.tract_fips === selectedFips ? " selected" : ""}${routeIndex >= 0 ? " route-stop" : ""}`;
      el.textContent = routeIndex >= 0 ? String(routeIndex + 1) : String(i + 1);
      const marker = new Marker({ element: el }).setLngLat([t.centroid_lon, t.centroid_lat]).addTo(map);
      markersRef.current.push(marker);
    });
    const routeCoordinates = routeStopIds.map((id) => tracts.find((tract) => tract.tract_fips === id))
      .filter((tract): tract is RankedTract => tract?.centroid_lat != null && tract?.centroid_lon != null)
      .map((tract) => [tract.centroid_lon!, tract.centroid_lat!]);
    const coordinates = depot && routeCoordinates.length ? [[depot.lon, depot.lat], ...routeCoordinates, [depot.lon, depot.lat]] : [];
    const geojson = { type: "FeatureCollection" as const, features: coordinates.length ? [{ type: "Feature" as const,
      properties: {}, geometry: { type: "LineString" as const, coordinates } }] : [] };
    const drawRoute = () => {
      const source = map.getSource("scenario-route") as GeoJSONSource | undefined;
      if (source) source.setData(geojson);
      else {
        map.addSource("scenario-route", { type: "geojson", data: geojson });
        map.addLayer({ id: "scenario-route-line", type: "line", source: "scenario-route",
          paint: { "line-color": "#16697a", "line-width": 4, "line-opacity": 0.8 } });
      }
    };
    if (map.isStyleLoaded()) drawRoute(); else map.once("load", drawRoute);
  }, [tracts, resources, selectedFips, routeStopIds, depot]);

  return <div ref={containerRef} className="map-container" style={{ height: 360 }} />;
}
