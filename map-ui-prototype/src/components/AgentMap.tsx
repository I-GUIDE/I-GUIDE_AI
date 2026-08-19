import { useEffect, useMemo, useRef } from 'react';
import { Map, Source, Layer, useControl } from 'react-map-gl/maplibre';
import type { MapRef } from 'react-map-gl/maplibre';
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

function DeckOverlay({ layers, onFeatureClick }: { layers: any[]; onFeatureClick: (feature: any, layerId: string) => void }) {
  const onClick = (info: any) => { if (info && info.object) onFeatureClick(info.object, info.layer?.id ?? ''); };
  const overlay = useControl(() => new MapboxOverlay({ interleaved: true, layers, getTooltip, onClick }));
  (overlay as MapboxOverlay).setProps({ layers, getTooltip, onClick });
  return null;
}

interface Props {
  layers: LayerArtifact[];
  drawnRegion: Polygon | null;
  drawPreview: Feature | null;
  drawMode: boolean;
  onMapClick: (lng: number, lat: number) => void;
  onHover: (info: any) => void;
  onFeatureClick: (feature: any, layerId: string) => void;
  onReady?: () => void;
  onResize?: () => void;
}

export function AgentMap({ layers, drawnRegion, drawPreview, drawMode, onMapClick, onHover, onFeatureClick, onReady, onResize }: Props) {
  // The map is mounted while hidden (progressive reveal), so its canvas is sized for a
  // zero/40x30 box and stays that way: observed 400x300 inside an 820x646 container, painting
  // nothing. A one-shot resize on reveal races the layout, so track the container instead.
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapRef | null>(null);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      const m = (window as any).__map;
      if (m && el.clientWidth > 0) {
        try { m.resize(); } catch { /* */ }
        // Resizing keeps the CENTER but not the framing: a fit computed against the
        // pre-reveal box stays over-zoomed after the canvas grows, so let the owner
        // re-apply it now that the container is its real size.
        onResize?.();
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [onResize]);

  const deckLayers = useMemo(
    () => layers.map((a) => toDeckLayer(a)),
    [layers],
  );

  const regionFeature: Feature | null = useMemo(() => {
    if (!drawnRegion) return null;
    return { type: 'Feature', geometry: drawnRegion, properties: {} };
  }, [drawnRegion]);

  return (
    <div ref={wrapRef} style={{ position: 'absolute', inset: 0 }}>
    <Map
      initialViewState={{ longitude: -89.0, latitude: 40.5, zoom: 5.2 }}
      mapStyle={OSM_STYLE}
      // Keep the WebGL buffer so the rendered view can be exported as an image;
      // without it canvas.toDataURL() returns a cleared, single-colour frame.
      preserveDrawingBuffer
      cursor={drawMode ? 'crosshair' : 'grab'}
      onClick={(e: MapLayerMouseEvent) => onMapClick(e.lngLat.lng, e.lngLat.lat)}
      ref={(r) => {
        // Publish the instance as soon as it EXISTS. Waiting for the `load` event is
        // unreliable: if the basemap's tile source fails (throttled/blocked), `load` never
        // fires, `__map` stays unset and everything keyed off it — auto-framing a delivered
        // layer, resize, export — silently never happens, even though deck.gl is drawing.
        mapRef.current = r;
        const m = r?.getMap?.();
        if (m && (window as any).__map !== m) {
          (window as any).__map = m;
          const announce = () => onReady?.();
          if (m.isStyleLoaded?.()) announce();
          else { m.once?.('idle', announce); setTimeout(announce, 3000); }
        }
      }}
      onLoad={(e: any) => { (window as any).__map = e.target; onReady?.(); }}
      interactiveLayerIds={[]}
      onMouseMove={() => {}}
      style={{ position: 'absolute', inset: 0 }}
    >
      <DeckOverlay layers={deckLayers} onFeatureClick={onFeatureClick} />

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
    </div>
  );
}
