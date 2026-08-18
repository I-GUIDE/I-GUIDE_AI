// The agent <-> map contract (Contract 1 from the design).
// Every visual on the map -- KB search results, Overpass results, user uploads,
// and analysis outputs -- is expressed as a LayerArtifact and rendered by one
// pure function (toDeckLayer). In production the agent emits these instead of
// baked PNGs; here the browser modules produce them locally.

import type { FeatureCollection, Geometry } from 'geojson';

export type RGBA = [number, number, number, number];

export interface Legend {
  label: string;
  color: RGBA;
}

export interface DeckStyle {
  fill?: RGBA;
  line?: RGBA;
  lineWidth?: number;   // pixels
  pointRadius?: number; // meters (deck.gl radius) or pixels if radiusUnits='pixels'
  radiusUnits?: 'meters' | 'pixels';
  opacity?: number;     // 0..1
  extruded?: boolean;
  elevation?: number;
}

export type LayerArtifact =
  | {
      kind: 'geojson';
      id: string;
      source: string;            // provenance: 'kb' | 'overpass' | 'upload' | 'analysis'
      label: string;
      data: FeatureCollection;
      style?: DeckStyle;
      render?: 'geojson' | 'heatmap' | 'points';
      legend?: Legend[];
      fitBounds?: boolean;
    };

export interface MapUpdate {
  layers: LayerArtifact[];
  camera?: { bounds?: [number, number, number, number]; center?: [number, number]; zoom?: number };
}

// Contract 2: a drawn region becomes a spatial filter for retrieval.
export interface SpatialFilter {
  geometry: Geometry;
  relation: 'intersects' | 'within' | 'contains';
}
