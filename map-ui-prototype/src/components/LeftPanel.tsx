import type { LayerArtifact } from '../contracts';

export interface SelectedFeature {
  name: string;
  layerLabel: string;
  properties: Record<string, any>;
}

interface Props {
  layers: LayerArtifact[];
  selected: SelectedFeature | null;
  onToggleLayer: (id: string) => void;
  onRemoveLayer: (id: string) => void;
  onFitLayer: (id: string) => void;
  onClearSelection: () => void;
}

const SOURCE_COLORS: Record<string, string> = {
  kb: '#7c3aed', overpass: '#1aa37a', upload: '#d1495b', analysis: '#c98a1a',
};

const HIDDEN_KEYS = new Set(['geometry', 'geojson', 'contents-embedding']);

function fmtValue(v: any): string {
  if (v == null) return '';
  if (typeof v === 'object') { try { return JSON.stringify(v); } catch { return String(v); } }
  return String(v);
}

export function LeftPanel(p: Props) {
  return (
    <aside className="leftpanel">
      <section className="lp-layers">
        <h3>Map layers <span className="cnt">{p.layers.length}</span></h3>
        {p.layers.length === 0 && <p className="lp-hint">No layers yet. Ask the agent for features, or upload data.</p>}
        <ul>
          {p.layers.map((l) => {
            const visible = l.visible !== false;
            const n = l.data?.features?.length ?? 0;
            return (
              <li key={l.id} className={visible ? '' : 'off'}>
                <button className="eye" title={visible ? 'Hide' : 'Show'} onClick={() => p.onToggleLayer(l.id)}>{visible ? '👁' : '⦰'}</button>
                <span className="dot" style={{ background: SOURCE_COLORS[l.source] ?? '#888' }} />
                <button className="lname" title="Zoom to layer" onClick={() => p.onFitLayer(l.id)}>{l.label}</button>
                <span className="cnt">{n}</span>
                <button className="x" title="Remove" onClick={() => p.onRemoveLayer(l.id)}>×</button>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="lp-feature">
        <h3>Selected feature{p.selected && <button className="x" title="Clear" onClick={p.onClearSelection}>×</button>}</h3>
        {!p.selected && <p className="lp-hint">Click a feature on the map to see its details.</p>}
        {p.selected && (
          <div className="fdetail">
            <div className="ftitle">{p.selected.name}</div>
            <div className="fsub">{p.selected.layerLabel}</div>
            <table className="ftable">
              <tbody>
                {Object.entries(p.selected.properties)
                  .filter(([k, v]) => !HIDDEN_KEYS.has(k) && v != null && v !== '')
                  .map(([k, v]) => (
                    <tr key={k}><th>{k}</th><td>{fmtValue(v)}</td></tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </aside>
  );
}
