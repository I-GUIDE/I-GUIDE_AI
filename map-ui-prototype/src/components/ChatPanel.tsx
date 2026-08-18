import { useEffect, useRef, useState } from 'react';
import type { LayerArtifact } from '../contracts';
import type { FileRecord, TraceLine } from '../agentClient';
import { SUGGESTIONS } from '../agentBrain';
import { groupedSources, type SourceGroup } from '../answerFormat';

export interface ChatMessage {
  role: 'user' | 'agent';
  text?: string;
  html?: string;
  trace?: TraceLine[];
  artifacts?: FileRecord[];
  layers?: { id: string; label: string; source: string }[];
  response?: any;        // terminal result payload -> "Sources used"
  streaming?: boolean;
}

export type Mode = 'live' | 'local';
export interface AgentCfg { endpoint: string; uploadEndpoint: string; apiKey: string }

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  drawMode: boolean;
  hasRegion: boolean;
  layers: LayerArtifact[];
  mode: Mode;
  cfg: AgentCfg;
  spatial: boolean;
  mapVisible: boolean;
  resolveUrl: (u: string) => string;
  onSend: (text: string) => void;
  onToggleDraw: () => void;
  onClearRegion: () => void;
  onUpload: (files: File[]) => void;
  onClearAll: () => void;
  onSetMode: (m: Mode) => void;
  onSetCfg: (c: AgentCfg) => void;
  onSetSpatial: (v: boolean) => void;
  onToggleMap: () => void;
}

const SOURCE_COLORS: Record<string, string> = {
  kb: '#7c3aed', overpass: '#10b981', upload: '#ef4444', analysis: '#f59e0b',
};
const GROUP_LABEL: Record<SourceGroup, string> = {
  internal: 'I-GUIDE knowledge base', external: 'External open-data catalogs', web: 'Open web',
};
const isImg = (f: FileRecord) => f.kind === 'image' || /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(f.filename || f.download_url || '');

function Sources({ response }: { response: any }) {
  const groups = groupedSources(response);
  const order: SourceGroup[] = ['internal', 'external', 'web'];
  if (!order.some((k) => groups[k].length)) return null;
  return (
    <div className="srcs">
      <h4>Sources used</h4>
      {order.filter((k) => groups[k].length).map((k) => (
        <div key={k} className="grp">
          <div className="hd">{GROUP_LABEL[k]}<span className="n">{groups[k].length} item{groups[k].length === 1 ? '' : 's'}</span></div>
          {groups[k].slice(0, 12).map((s, i) => {
            const title = String(s.title || s.doc_id || '(untitled)');
            const url = String(s.url || '');
            const snip = String(s.abstract || s.snippet || s.contents || '').trim();
            return (
              <div key={i} className="it">
                <div className="t">{/^https?:\/\//i.test(url)
                  ? <a href={url} target="_blank" rel="noopener noreferrer">{title}</a> : title}</div>
                {snip && <div className="sn">{snip.length > 260 ? snip.slice(0, 260) + '…' : snip}</div>}
              </div>
            );
          })}
          {groups[k].length > 12 && <div className="sn">+{groups[k].length - 12} more not shown</div>}
        </div>
      ))}
    </div>
  );
}

export function ChatPanel(p: Props) {
  const [text, setText] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [p.messages, p.busy]);

  const send = (t: string) => { const v = t.trim(); if (!v || p.busy) return; setText(''); p.onSend(v); };

  return (
    <aside className="chat">
      <header>
        <h1>I-GUIDE Agent</h1>
        <span className={`tag ${p.mode}`}>{p.mode === 'live' ? 'live agent' : 'local demo'}</span>
        {p.layers.length > 0 && (
          <button className="mapbtn" onClick={p.onToggleMap} title="Show/hide map">
            {p.mapVisible ? '🗺 hide' : '🗺 map'}
          </button>
        )}
        <button className="gear" onClick={() => setShowSettings((s) => !s)} title="Settings">⚙</button>
      </header>

      {showSettings && (
        <div className="settings">
          <label>Mode
            <select value={p.mode} onChange={(e) => p.onSetMode(e.target.value as Mode)}>
              <option value="live">Live agent (real backend)</option>
              <option value="local">Local demo (mock, offline)</option>
            </select>
          </label>
          <label className="chk">
            <input type="checkbox" checked={p.spatial} onChange={(e) => p.onSetSpatial(e.target.checked)} />
            Spatial tools (maps, OSM/Overpass, geo search)
          </label>
          <label>API key
            <input type="password" value={p.cfg.apiKey} placeholder="X-API-KEY (if required)"
              onChange={(e) => p.onSetCfg({ ...p.cfg, apiKey: e.target.value })} />
          </label>
          <label>Chat endpoint
            <input value={p.cfg.endpoint} onChange={(e) => p.onSetCfg({ ...p.cfg, endpoint: e.target.value })} />
          </label>
          <p className="hint">Spatial off = pure chat (no map, no geo tools — faster). The map appears on its own when the agent returns geometry.</p>
        </div>
      )}

      <div className="toolbar">
        <button className={p.drawMode ? 'primary active' : ''} onClick={p.onToggleDraw} disabled={!p.spatial}>
          {p.drawMode ? 'Click 2 corners…' : '▭ Region'}
        </button>
        <button onClick={p.onClearRegion} disabled={!p.hasRegion}>Clear region</button>
        <label className="filebtn">＋ files
          <input type="file" multiple style={{ display: 'none' }}
            onChange={(e) => { const fs = Array.from(e.target.files || []); if (fs.length) p.onUpload(fs); }} />
        </label>
        <span className={p.spatial ? 'rstat on' : 'rstat'}>{p.spatial ? (p.hasRegion ? '● region' : '◇ spatial') : '○ chat only'}</span>
      </div>

      <div className="transcript" ref={scrollRef}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); const fs = Array.from(e.dataTransfer.files || []); if (fs.length) p.onUpload(fs); }}>
        {p.messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">
              {m.trace && m.trace.length > 0 && (
                <details className="trace" open={m.streaming}>
                  <summary>{m.streaming ? 'thinking…' : `reasoning (${m.trace.length})`}</summary>
                  {m.trace.map((t, j) => <div key={j} className={`tl ${t.kind || ''}`}>{t.text}</div>)}
                </details>
              )}
              {m.html
                ? <div className="md" dangerouslySetInnerHTML={{ __html: m.html }} />
                : m.text && <div className="txt">{m.text}</div>}
              {m.streaming && !m.html && <span className="cursor">▋</span>}
              {m.artifacts && m.artifacts.filter(isImg).filter((f) => !(m.html || '').includes(f.file_id)).length > 0 && (
                <div className="arts">
                  {m.artifacts.filter(isImg).filter((f) => !(m.html || '').includes(f.file_id)).map((f) => (
                    <a key={f.file_id} href={p.resolveUrl(f.download_url)} target="_blank" rel="noopener noreferrer">
                      <img src={p.resolveUrl(f.download_url)} alt={f.filename} loading="lazy" />
                    </a>
                  ))}
                </div>
              )}
              {m.response && <Sources response={m.response} />}
              {m.layers && m.layers.length > 0 && (
                <div className="mlayers">
                  {m.layers.map((l) => (
                    <span key={l.id} className="pill"><span className="dot" style={{ background: SOURCE_COLORS[l.source] ?? '#888' }} />{l.label}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {p.busy && !p.messages.some((m) => m.streaming) && <div className="msg agent"><div className="bubble typing">…</div></div>}
      </div>

      {p.messages.length <= 1 && (
        <div className="suggest">
          {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}
        </div>
      )}

      <div className="inputbar">
        <textarea value={text}
          placeholder={p.mode === 'live' ? 'Ask the I-GUIDE agent…' : 'Ask the mock… “show rivers here”'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(text); } }} />
        <button className="primary" onClick={() => send(text)} disabled={p.busy || !text.trim()}>Send</button>
      </div>

      <div className="foot">
        <span>{p.layers.length} layer(s){p.mapVisible ? ' · map on' : ''}</span>
        <button className="small" onClick={p.onClearAll} disabled={!p.layers.length}>clear layers</button>
      </div>
    </aside>
  );
}
