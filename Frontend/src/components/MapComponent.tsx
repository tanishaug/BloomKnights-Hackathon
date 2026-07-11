import { useEffect, useRef } from "react";
import L from "leaflet";
import type { Building } from "../app";

interface MapProps {
  buildings: Building[];
  center: { lat: number; lon: number };
  theme: "light" | "dark";
  selectedBuildingId: number | null;
  optimizedIds: number[];
  onSelectBuilding: (id: number) => void;
}

const tileUrls = {
  dark: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
  light: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
};

export default function MapComponent({ buildings, center, theme, selectedBuildingId, optimizedIds, onSelectBuilding }: MapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tileLayerRef = useRef<L.TileLayer | null>(null);
  const markerLayerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current).setView([center.lat, center.lon], 12);
    mapRef.current = map;
    markerLayerRef.current = L.layerGroup().addTo(map);
    return () => {
      map.remove();
      mapRef.current = null;
      tileLayerRef.current = null;
      markerLayerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    tileLayerRef.current?.remove();
    tileLayerRef.current = L.tileLayer(tileUrls[theme], {
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
    }).addTo(map);
  }, [theme]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.setView([center.lat, center.lon], map.getZoom() < 11 ? 12 : map.getZoom());
  }, [center.lat, center.lon]);

  useEffect(() => {
    const layer = markerLayerRef.current;
    if (!layer) return;
    layer.clearLayers();
    for (const building of buildings) {
      const tier = building.investment_score != null && building.investment_score >= 75 ? "high" : building.investment_score != null && building.investment_score >= 50 ? "moderate" : "low";
      const selected = building.id === selectedBuildingId;
      const optimized = optimizedIds.includes(building.id);
      const colors = tier === "high" ? ["#5ce0a3", "#198963"] : tier === "moderate" ? ["#f4bd62", "#bd7a25"] : ["#ff7474", "#a33e43"];
      const marker = L.circleMarker([building.lat, building.lon], {
        radius: selected ? 9 : optimized ? 7 : 5,
        color: selected ? "#ffffff" : colors[0],
        weight: selected ? 3 : optimized ? 2 : 1,
        fillColor: colors[1],
        fillOpacity: selected ? 1 : 0.85,
      });
      marker.bindTooltip(`${building.name}<br>Score: ${building.investment_score ?? "N/A"}`, { direction: "top" });
      marker.on("click", () => onSelectBuilding(building.id));
      marker.addTo(layer);
    }
  }, [buildings, selectedBuildingId, optimizedIds, onSelectBuilding]);

  return <div ref={containerRef} className="map-container" />;
}
