import { useMemo } from 'react';
import { Map, Source, Layer, useControl } from 'react-map-gl/maplibre';
import type { MapLayerMouseEvent } from 'react-map-gl/maplibre';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Feature, Polygon } from 'geojson';
import type { LayerArtifact } from '../contracts';
import { toDeckLayer } from '../toDeckLayer';
import 'maplibre-gl/dist/maplibre-gl.css';

// Raster OSM basemap -- no API key required (fine for a prototype).
const OSM_STYLE: any = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://a.tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};

function getTooltip({ object }: any) {
  if (!object) return null;
  const p = object.properties ?? {};
  const name = p.name ?? p.id ?? object.id ?? 'feature';
  const extra = p.type ? ` (${p.type})` : p.amenity ? ` (${p.amenity})` : '';
  const score = p.score ? `  score ${p.score}` : '';
  return { text: `${name}${extra}${score}` };
}

function DeckOverlay({ layers }: { layers: any[] }) {
  const overlay = useControl(() => new MapboxOverlay({ interleaved: true, layers, getTooltip }));
  (overlay as MapboxOverlay).setProps({ layers, getTooltip });
  return null;
}

interface Props {
  layers: LayerArtifact[];
  drawnRegion: Polygon | null;
  drawPreview: Feature | null;
  drawMode: boolean;
  onMapClick: (lng: number, lat: number) => void;
  onHover: (info: any) => void;
}

export function AgentMap({ layers, drawnRegion, drawPreview, drawMode, onMapClick, onHover }: Props) {
  const deckLayers = useMemo(
    () => layers.map((a) => toDeckLayer(a)),
    [layers],
  );

  const regionFeature: Feature | null = useMemo(() => {
    if (!drawnRegion) return null;
    return { type: 'Feature', geometry: drawnRegion, properties: {} };
  }, [drawnRegion]);

  return (
    <Map
      initialViewState={{ longitude: -89.0, latitude: 40.5, zoom: 5.2 }}
      mapStyle={OSM_STYLE}
      cursor={drawMode ? 'crosshair' : 'grab'}
      onClick={(e: MapLayerMouseEvent) => onMapClick(e.lngLat.lng, e.lngLat.lat)}
      onLoad={(e: any) => { (window as any).__map = e.target; }}
      interactiveLayerIds={[]}
      onMouseMove={() => {}}
      style={{ position: 'absolute', inset: 0 }}
    >
      <DeckOverlay layers={deckLayers} />

      {regionFeature && (
        <Source id="drawn-region" type="geojson" data={regionFeature}>
          <Layer id="drawn-fill" type="fill" paint={{ 'fill-color': '#ff7f0e', 'fill-opacity': 0.12 }} />
          <Layer id="drawn-line" type="line" paint={{ 'line-color': '#ff7f0e', 'line-width': 2, 'line-dasharray': [2, 1] }} />
        </Source>
      )}
      {drawPreview && (
        <Source id="draw-preview" type="geojson" data={drawPreview}>
          <Layer id="preview-line" type="line" paint={{ 'line-color': '#ff7f0e', 'line-width': 2 }} />
        </Source>
      )}
    </Map>
  );
}
