import { useEffect, useRef } from "react";
import { MapLibreMap, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { ExistingResource, RankedTract } from "../lib/api";

const BASE_STYLE = "https://demotiles.maplibre.org/style.json";

type Props = {
  tracts: RankedTract[];
  resources: ExistingResource[];
  center: [number, number];
  zoom: number;
  selectedFips: string | null;
};

/**
 * A lighter-weight map than HomeMap's -- just numbered markers for the
 * ranked tracts plus dots for existing resources, matching wireframe
 * 3b/3c's map column (pins, not shaded tract polygons -- that full
 * choropleth treatment is HomeMap's job).
 */
export function RankedTractsMap({ tracts, resources, center, zoom, selectedFips }: Props) {
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

    tracts.forEach((t, i) => {
      if (t.centroid_lat == null || t.centroid_lon == null) return;
      const el = document.createElement("div");
      el.className = `map-marker tract-marker${t.tract_fips === selectedFips ? " selected" : ""}`;
      el.textContent = String(i + 1);
      const marker = new Marker({ element: el }).setLngLat([t.centroid_lon, t.centroid_lat]).addTo(map);
      markersRef.current.push(marker);
    });
  }, [tracts, resources, selectedFips]);

  return <div ref={containerRef} className="map-container" style={{ height: 360 }} />;
}
