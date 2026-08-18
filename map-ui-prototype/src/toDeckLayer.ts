// The thin renderer: LayerArtifact -> deck.gl layer. One switch, easy to test.
import { GeoJsonLayer } from '@deck.gl/layers';
import { HeatmapLayer } from '@deck.gl/aggregation-layers';
import type { LayerArtifact } from './contracts';
import type { Feature, Point } from 'geojson';

const DEFAULT_FILL: [number, number, number, number] = [51, 136, 255, 120];
const DEFAULT_LINE: [number, number, number, number] = [20, 90, 200, 255];

function centroidOf(f: Feature): [number, number] | null {
  const g = f.geometry;
  if (!g) return null;
  if (g.type === 'Point') return (g as Point).coordinates as [number, number];
  return null;
}

export function toDeckLayer(a: LayerArtifact) {
  const s = a.style ?? {};
  const fill = s.fill ?? DEFAULT_FILL;
  const line = s.line ?? DEFAULT_LINE;

  if (a.render === 'heatmap') {
    const pts = a.data.features
      .map((f) => centroidOf(f))
      .filter((c): c is [number, number] => !!c)
      .map((c) => ({ position: c, weight: 1 }));
    return new HeatmapLayer({
      id: a.id,
      data: pts,
      getPosition: (d: any) => d.position,
      getWeight: (d: any) => d.weight,
      radiusPixels: 40,
      intensity: 1,
      opacity: s.opacity ?? 0.8,
      pickable: false,
    });
  }

  return new GeoJsonLayer({
    id: a.id,
    data: a.data,
    pickable: true,
    stroked: true,
    filled: true,
    extruded: !!s.extruded,
    getElevation: s.elevation ?? 0,
    pointType: 'circle',
    getFillColor: fill,
    getLineColor: line,
    getLineWidth: s.lineWidth ?? 2,
    lineWidthUnits: 'pixels',
    getPointRadius: s.pointRadius ?? 6,
    pointRadiusUnits: s.radiusUnits ?? 'pixels',
    opacity: s.opacity ?? 1,
    // expose source + props for the tooltip
    updateTriggers: {},
  });
}
