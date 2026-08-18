import { useEffect, useRef, useState } from 'react';
import type { FeatureCollection } from 'geojson';
import type { LayerArtifact } from '../contracts';
import type { FileRecord, TraceLine } from '../agentClient';
import { SUGGESTIONS } from '../agentBrain';

export interface ChatMessage {
  role: 'user' | 'agent';
  text?: string;
  html?: string;
  trace?: TraceLine[];
  artifacts?: FileRecord[];
  layers?: { id: string; label: string; source: string }[];
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
  resolveUrl: (u: string) => string;
  onSend: (text: string) => void;
  onToggleDraw: () => void;
  onClearRegion: () => void;
  onUpload: (files: File[]) => void;
  onClearAll: () => void;
  onSetMode: (m: Mode) => void;
  onSetCfg: (c: AgentCfg) => void;
}

const SOURCE_COLORS: Record<string, string> = {
  kb: '#7c3aed', overpass: '#10b981', upload: '#ef4444', analysis: '#f59e0b',
};
const isImg = (f: FileRecord) => f.kind === 'image' || /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(f.filename || f.download_url || '');

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
          <label>API key
            <input type="password" value={p.cfg.apiKey} placeholder="X-API-KEY (if required)"
              onChange={(e) => p.onSetCfg({ ...p.cfg, apiKey: e.target.value })} />
          </label>
          <label>Chat endpoint
            <input value={p.cfg.endpoint} onChange={(e) => p.onSetCfg({ ...p.cfg, endpoint: e.target.value })} />
          </label>
          <p className="hint">Live mode streams the real agent (SSE). Local mode uses the in-browser mock.</p>
        </div>
      )}

      <div className="toolbar">
        <button className={p.drawMode ? 'primary active' : ''} onClick={p.onToggleDraw}>
          {p.drawMode ? 'Click 2 corners…' : '▭ Region'}
        </button>
        <button onClick={p.onClearRegion} disabled={!p.hasRegion}>Clear region</button>
        <label className="filebtn">＋ files
          <input type="file" multiple style={{ display: 'none' }}
            onChange={(e) => { const fs = Array.from(e.target.files || []); if (fs.length) p.onUpload(fs); }} />
        </label>
        <span className={p.hasRegion ? 'rstat on' : 'rstat'}>{p.hasRegion ? '● region' : '○ no region'}</span>
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
        <span>{p.layers.length} layer(s) on map</span>
        <button className="small" onClick={p.onClearAll} disabled={!p.layers.length}>clear layers</button>
      </div>
    </aside>
  );
}
