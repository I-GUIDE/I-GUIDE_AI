import { useCallback, useEffect, useRef, useState } from 'react';
import type { Feature, FeatureCollection, Polygon, Geometry } from 'geojson';
import { AgentMap } from './components/AgentMap';
import { ChatPanel, type ChatMessage, type Mode, type AgentCfg } from './components/ChatPanel';
import { TopNav } from './components/TopNav';
import { LeftPanel, type SelectedFeature } from './components/LeftPanel';
import type { LayerArtifact } from './contracts';
import { parseIntent } from './agentBrain';
import { searchKb, kbHitsToFeatureCollections, type KbHit } from './mockKb';
import { queryOverpass } from './overpass';
import { bufferFC, clipToRegion, convexHull, areaKm2, stats, selectRelated, layerBBox } from './analysis';
import {
  streamChat, uploadFiles, absoluteUrl, extractFeatures, newThreadId,
  type AgentConfig, type FileRecord, type TraceLine,
} from './agentClient';
import { renderMarkdown } from './markdown';

const DEFAULT_CFG: AgentCfg = {
  endpoint: '/agent/chat/stream',       // proxied to the deployed agent by vite (see vite.config)
  uploadEndpoint: '/agent/files/upload',
  apiKey: '',
};

// Retrieval tools that do NOT touch geography — used when the "Spatial tools" toggle
// is off so a pure-chat turn stays fast and never opens the map.
const CHAT_ONLY_METHODS = ['keyword_search', 'semantic_search', 'neo4j_search', 'web_search', 'agent_kb_search', 'get_kb_block'];

function loadCfg(): { mode: Mode; cfg: AgentCfg; spatial: boolean } {
  try {
    const raw = localStorage.getItem('iguide-map-ui');
    if (raw) { const j = JSON.parse(raw); return { mode: j.mode || 'live', cfg: { ...DEFAULT_CFG, ...j.cfg }, spatial: j.spatial !== false }; }
  } catch { /* */ }
  return { mode: 'live', cfg: DEFAULT_CFG, spatial: true };
}

function bboxPolygon(a: [number, number], b: [number, number]): Polygon {
  const minLon = Math.min(a[0], b[0]), maxLon = Math.max(a[0], b[0]);
  const minLat = Math.min(a[1], b[1]), maxLat = Math.max(a[1], b[1]);
  return { type: 'Polygon', coordinates: [[[minLon, minLat], [maxLon, minLat], [maxLon, maxLat], [minLon, maxLat], [minLon, minLat]]] };
}
function viewportPolygon(): Polygon | null {
  const m = (window as any).__map; if (!m) return null;
  const b = m.getBounds();
  return bboxPolygon([b.getWest(), b.getSouth()], [b.getEast(), b.getNorth()]);
}
function polygonBBox(poly: Polygon): [number, number, number, number] {
  const ring = poly.coordinates[0]; const lons = ring.map((c) => c[0]); const lats = ring.map((c) => c[1]);
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
}

export default function App() {
  const init = loadCfg();
  const [layers, setLayers] = useState<LayerArtifact[]>([]);
  const [drawMode, setDrawMode] = useState(false);
  const [drawnRegion, setDrawnRegion] = useState<Polygon | null>(null);
  const [drawPreview] = useState<Feature | null>(null);
  const firstCorner = useRef<[number, number] | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>(init.mode);
  const [cfg, setCfg] = useState<AgentCfg>(init.cfg);
  const [spatial, setSpatial] = useState<boolean>(init.spatial);
  const [mapVisible, setMapVisible] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [selected, setSelected] = useState<SelectedFeature | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'agent', text: "Hi — I'm the I-GUIDE agent. Ask me anything. Turn on Spatial tools (⚙) to search geodata and draw regions; the map opens on its own when I return geometry." },
  ]);

  const threadRef = useRef<string>(newThreadId());
  const memoryRef = useRef<string | null>(null);
  const pendingFileIds = useRef<string[]>([]);
  const uploadContext = useRef<string>(''); // e.g. 'Uploaded "chicago" covers bbox [...]' — spatial context for the agent
  const layersRef = useRef(layers); layersRef.current = layers;
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => { try { localStorage.setItem('iguide-map-ui', JSON.stringify({ mode, cfg, spatial })); } catch { /* */ } }, [mode, cfg, spatial]);

  // Progressive map: auto-reveal ONCE the first time a layer appears, then respect the
  // manual show/hide toggle (don't fight the user). Re-arm after layers are cleared.
  const autoRevealed = useRef(false);
  useEffect(() => {
    if (layers.length && !autoRevealed.current) { autoRevealed.current = true; setMapVisible(true); }
    if (!layers.length) autoRevealed.current = false;
  }, [layers.length]);
  // The instance is destroyed when hidden, so drop the stale handle.
  useEffect(() => { if (!mapVisible) (window as any).__map = undefined; }, [mapVisible]);

  const asAgentConfig = useCallback((): AgentConfig => ({ ...cfg }), [cfg]);
  const resolveUrl = useCallback((u: string) => absoluteUrl(u, asAgentConfig()), [asAgentConfig]);

  const pushMsg = useCallback((m: ChatMessage) => setMessages((prev) => [...prev, m]), []);
  const putLayer = useCallback((a: LayerArtifact) => {
    setLayers((prev) => { const i = prev.findIndex((l) => l.id === a.id); if (i === -1) return [...prev, a]; const n = prev.slice(); n[i] = a; return n; });
  }, []);
  const fitView = useCallback((fc: FeatureCollection) => {
    const m = (window as any).__map; if (!m || !fc.features.length) return;
    try { const [w, s, e, n] = layerBBox(fc); m.fitBounds([[w, s], [e, n]], { padding: 80, duration: 800, maxZoom: 14 }); } catch { /* */ }
  }, []);

  // --- layer management + feature inspection (left panel) ---
  const toggleLayer = useCallback((id: string) => {
    setLayers((prev) => prev.map((l) => (l.id === id ? { ...l, visible: !(l.visible !== false) } : l)));
  }, []);
  const removeLayerById = useCallback((id: string) => {
    setLayers((prev) => prev.filter((l) => l.id !== id));
  }, []);
  const fitLayer = useCallback((id: string) => {
    const l = layersRef.current.find((x) => x.id === id);
    if (l) fitView(l.data);
  }, [fitView]);
  // Vector artifacts (a .geojson the agent WROTE, e.g. from execute_code) belong on the
  // interactive map, not just in a download list. Fetch and add each one once. Static
  // images (PNG plots) stay as attachments — that is the other visualization route.
  const loadedArtifacts = useRef<Set<string>>(new Set());
  // Set when a turn delivers a layer through the map_layer event. The agent has then
  // said exactly what belongs on the map, so its other .geojson artifacts — the raw
  // conversion it made on the way, or the same file a second time — must NOT be
  // auto-loaded too: three stacked layers of 128,855 points buried the heatmap under
  // a solid mass of circles.
  const mapLayerDelivered = useRef(false);
  const loadVectorArtifacts = useCallback(async (files: FileRecord[]) => {
    if (!spatial) return;
    for (const f of files) {
      const name = f.filename || f.download_url || '';
      if (!/\.(geo)?json$/i.test(name)) continue;
      const key = f.file_id || f.download_url;
      if (!key || loadedArtifacts.current.has(key)) continue;
      loadedArtifacts.current.add(key);
      try {
        const res = await fetch(resolveUrl(f.download_url));
        if (!res.ok) continue;
        const j = await res.json();
        const fc: FeatureCollection =
          j?.type === 'FeatureCollection' ? j
          : j?.type === 'Feature' ? { type: 'FeatureCollection', features: [j] }
          : j?.type && j?.coordinates ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: j, properties: {} }] }
          : { type: 'FeatureCollection', features: [] };
        if (!fc.features.length) continue;
        const label = (f.filename || 'result').replace(/\.(geo)?json$/i, '');
        // Key the LAYER by filename, not file_id: when the agent regenerates the same
        // output (a retry, or a second pass over the same step) it gets a new file_id but
        // the same name, and the user should see one updated layer rather than two
        // identical ones stacked on the map.
        const layerId = `artifact-${label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}`;
        putLayer({
          kind: 'geojson', id: layerId, source: 'analysis', label: `${label}`,
          data: fc, fitBounds: true,
          style: { fill: [201, 138, 26, 110], line: [201, 138, 26, 255], lineWidth: 2, pointRadius: 5 },
        });
        fitView(fc);
      } catch { /* not loadable as GeoJSON — leave it as a download */ }
    }
  }, [spatial, resolveUrl, putLayer, fitView]);

  const onFeatureClick = useCallback((feature: any, layerId: string) => {
    const props = (feature && feature.properties) || {};
    const layer = layersRef.current.find((l) => l.id === layerId);
    const name = props.name || props.title || props.id || props.doc_id || '(feature)';
    setSelected({ name: String(name), layerLabel: layer?.label || layerId, properties: props });
  }, []);

  const onMapClick = useCallback((lng: number, lat: number) => {
    if (!drawMode) return;
    if (!firstCorner.current) { firstCorner.current = [lng, lat]; }
    else {
      const region = bboxPolygon(firstCorner.current, [lng, lat]); firstCorner.current = null;
      setDrawMode(false); setDrawnRegion(region);
      pushMsg({ role: 'agent', text: `Region set (≈ ${areaKm2(region).toLocaleString(undefined, { maximumFractionDigits: 0 })} km²).` });
    }
  }, [drawMode, pushMsg]);

  // ---- LIVE: drive the real agent over SSE, updating the map from events ----
  const runLive = useCallback(async (text: string) => {
    let agentIdx = -1;
    setMessages((prev) => { const base = [...prev, { role: 'user', text } as ChatMessage]; agentIdx = base.length; return [...base, { role: 'agent', streaming: true, trace: [] }]; });
    const patch = (up: Partial<ChatMessage>) => setMessages((prev) => { if (agentIdx < 0 || agentIdx >= prev.length) return prev; const n = prev.slice(); n[agentIdx] = { ...n[agentIdx], ...up }; return n; });
    mapLayerDelivered.current = false;
    const trace: TraceLine[] = [];
    const addTrace = (t: TraceLine) => { trace.push(t); patch({ trace: [...trace] }); };
    setBusy(true);
    // A drawn region becomes spatial context for the agent (the API has no geometry
    // field, so it rides along in the prompt). The user bubble keeps the original text.
    const hints: string[] = [];
    if (spatial && drawnRegion) hints.push(`Focus on this drawn area — bbox [${polygonBBox(drawnRegion).map((n) => n.toFixed(4)).join(', ')}] (minLon,minLat,maxLon,maxLat, EPSG:4326).`);
    if (spatial && uploadContext.current) hints.push(uploadContext.current);
    const regionHint = hints.length ? `\n\n(${hints.join(' ')})` : '';
    try {
      abortRef.current = new AbortController();
      const res = await streamChat(text + regionHint, {
        signal: abortRef.current.signal,
        threadId: threadRef.current, memoryId: memoryRef.current,
        fileIds: pendingFileIds.current, agentDev: true,
        includeMcpTools: spatial,
        enabledSearchMethods: spatial ? null : CHAT_ONLY_METHODS,
      }, asAgentConfig(), {
        onTrace: (l) => addTrace(l),
        onToolCall: (name, args) => {
          addTrace({ text: `${name}(${short(args)})`, kind: 'tool' });
          drawFromToolArgs(name, args);
        },
        onToolResult: (name, parsed) => {
          const fc = extractFeatures(parsed);
          if (fc.features.length) putLayer({ kind: 'geojson', id: `live-${name}`, source: 'kb', label: `${name} (preview)`, data: fc, style: { fill: [124, 58, 237, 180], line: [255, 255, 255, 255], pointRadius: 6, lineWidth: 2 } });
        },
        onFile: (files) => patch({ artifacts: files }),
        onMapLayer: async (layer) => {
          mapLayerDelivered.current = true;
          // A layer may arrive inline or as a URL to fetch (large heatmaps/choropleths).
          let fc = layer.geojson;
          if (!fc && layer.url) {
            try {
              const res = await fetch(resolveUrl(layer.url));
              if (res.ok) fc = await res.json();
            } catch { /* leave it undelivered rather than guess */ }
          }
          if (!fc || !Array.isArray(fc.features) || !fc.features.length) return;
          const heat = layer.render === 'heatmap';
          const green = layer.source === 'overpass';
          putLayer({
            kind: 'geojson', id: layer.id, source: (layer.source as any) || 'analysis', label: layer.label,
            data: fc, fitBounds: true,
            render: heat ? 'heatmap' : 'geojson',
            styleBy: layer.styleBy,
            partial: layer.sampled && layer.total
              ? { shown: layer.count ?? fc.features.length, total: layer.total }
              : undefined,
            style: heat
              ? { opacity: 0.85 }
              : green
                ? { fill: [16, 185, 129, 120], line: [16, 185, 129, 255], lineWidth: 3, pointRadius: 5 }
                : { fill: [124, 58, 237, 120], line: [124, 58, 237, 255], lineWidth: 2, pointRadius: 6 },
          });
          fitView(fc);
          addTrace({ text: `map: ${layer.render || 'layer'} — ${layer.label} `
            + (layer.sampled && layer.total
                ? `(SAMPLE: ${layer.count ?? fc.features.length} of ${layer.total})`
                : `(${layer.count ?? fc.features.length} features)`), kind: 'tool' });
        },
        onIds: ({ threadId, memoryId }) => { if (threadId) threadRef.current = threadId; if (memoryId) memoryRef.current = memoryId; },
      });
      pendingFileIds.current = []; // attached to the thread server-side now
      // Reconcile with authoritative terminal result.
      const authoritative = extractFeatures(res.response);
      if (authoritative.features.length) {
        putLayer({ kind: 'geojson', id: 'agent-results', source: 'kb', label: 'Agent results', data: authoritative, style: { fill: [124, 58, 237, 220], line: [255, 255, 255, 255], pointRadius: 7, lineWidth: 2 }, fitBounds: true });
        fitView(authoritative);
      }
      const html = res.error ? '' : renderMarkdown(res.answer || '_(no answer text)_', resolveUrl);
      patch({ html, text: res.error ? `⚠ ${res.error}` : undefined, artifacts: res.downloads, response: res.response, trace: [...trace], streaming: false });
      // Only when the agent did NOT place a layer itself.
      if (!mapLayerDelivered.current) void loadVectorArtifacts(res.downloads);
    } catch (e: any) {
      const stopped = e?.name === 'AbortError';
      patch({ text: stopped ? '⏹ Stopped. Anything already on the map stays; ask me something else.'
                            : `Request failed: ${e.message}`,
              streaming: false });
    } finally { setBusy(false); abortRef.current = null; }
  }, [asAgentConfig, putLayer, fitView, resolveUrl, spatial, drawnRegion, loadVectorArtifacts]);

  const drawFromToolArgs = useCallback((name: string, args: any) => {
    if (!args || typeof args !== 'object') return;
    const bbox = args.bbox || args.bounding_box || args.extent;
    if (Array.isArray(bbox) && bbox.length === 4 && bbox.every((n: any) => typeof n === 'number')) {
      const region = bboxPolygon([bbox[0], bbox[1]], [bbox[2], bbox[3]]);
      setDrawnRegion(region); fitView({ type: 'FeatureCollection', features: [{ type: 'Feature', geometry: region, properties: {} }] });
    } else if (args.geometry && args.geometry.type) {
      putLayer({ kind: 'geojson', id: `live-region-${name}`, source: 'analysis', label: `${name} region`, data: { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: args.geometry, properties: {} }] }, style: { fill: [255, 127, 14, 40], line: [255, 127, 14, 255], lineWidth: 2 } });
    }
  }, [fitView, putLayer]);

  // ---- LOCAL: the deterministic mock (offline fallback) ----
  const runLocal = useCallback(async (text: string) => {
    pushMsg({ role: 'user', text });
    const intent = parseIntent(text);
    if (intent.kind === 'clear') { setLayers([]); pushMsg({ role: 'agent', text: 'Cleared all layers.' }); return; }
    if (intent.kind === 'help') { pushMsg({ role: 'agent', text: 'Try: “find flood datasets”, “show rivers/cafés here”, “rivers that intersect the upload”, “buffer 2km”, “heatmap”.' }); return; }
    if (intent.kind === 'kb') {
      const region = intent.useRegion ? (drawnRegion ?? viewportPolygon()) : drawnRegion;
      const filter = region ? { geometry: region as Geometry, relation: 'intersects' as const } : null;
      const hits: KbHit[] = searchKb(intent.query, filter);
      const { centroids, boxes } = kbHitsToFeatureCollections(hits);
      putLayer({ kind: 'geojson', id: 'kb-boxes', source: 'kb', label: 'KB footprints', data: boxes, style: { fill: [124, 58, 237, 40], line: [124, 58, 237, 220], lineWidth: 1.5 } });
      putLayer({ kind: 'geojson', id: 'kb-centroids', source: 'kb', label: 'KB results', data: centroids, style: { fill: [124, 58, 237, 235], line: [255, 255, 255, 255], pointRadius: 7 }, fitBounds: true });
      if (hits.length) fitView(centroids);
      pushMsg({ role: 'agent', text: hits.length ? `Found ${hits.length}:\n${hits.slice(0, 5).map((h) => `• ${h.title}`).join('\n')}` : `No matches for “${intent.query}”.` });
      return;
    }
    if (intent.kind === 'overpass') {
      const region = drawnRegion ?? viewportPolygon();
      if (!region) { pushMsg({ role: 'agent', text: 'Draw a region or zoom in and say “here”.' }); return; }
      setBusy(true);
      try { const fc = await queryOverpass(intent.preset, polygonBBox(region));
        putLayer({ kind: 'geojson', id: `overpass-${intent.preset.key}`, source: 'overpass', label: `OSM: ${intent.preset.label}`, data: fc, style: { fill: [16, 185, 129, 230], line: [16, 185, 129, 255], lineWidth: 2, pointRadius: 5 } });
        pushMsg({ role: 'agent', text: `Pulled ${fc.features.length} ${intent.preset.label}. ${stats(fc, areaKm2(region))}` });
      } catch (e: any) { pushMsg({ role: 'agent', text: `Overpass failed: ${e.message}` }); } finally { setBusy(false); }
      return;
    }
    if (intent.kind === 'relate') {
      const target = pickTarget(layersRef.current, intent.targetHint);
      if (!target) { pushMsg({ role: 'agent', text: 'Upload a GeoJSON or pull a layer first.' }); return; }
      setBusy(true);
      try { const fc = await queryOverpass(intent.preset, layerBBox(target.data));
        const sel = selectRelated(fc, target.data, intent.relation); const id = `relate-${intent.preset.key}-${target.id}`;
        putLayer({ kind: 'geojson', id, source: 'analysis', label: `${intent.preset.label} ∩ ${target.label}`, data: sel.fc, style: { fill: [37, 99, 235, 90], line: [37, 99, 235, 255], lineWidth: 3, pointRadius: 5 }, fitBounds: true });
        fitView(sel.fc.features.length ? sel.fc : target.data);
        pushMsg({ role: 'agent', text: `${fc.features.length} ${intent.preset.label} found; ${sel.fc.features.length} intersect “${target.label}”.` });
      } catch (e: any) { pushMsg({ role: 'agent', text: `Failed: ${e.message}` }); } finally { setBusy(false); }
      return;
    }
    if (intent.kind === 'analysis') {
      const target = pickTarget(layersRef.current, intent.targetHint);
      if (!target) { pushMsg({ role: 'agent', text: 'No layer to analyze yet.' }); return; }
      if (intent.op === 'heatmap') { putLayer({ ...target, id: `${target.id}-heat`, label: `heatmap(${target.label})`, render: 'heatmap' }); pushMsg({ role: 'agent', text: `Heatmap of ${target.label}.` }); return; }
      let out; let id;
      if (intent.op === 'buffer') { out = bufferFC(target.data, intent.km); id = `analysis-buffer-${target.id}`; }
      else if (intent.op === 'hull') { out = convexHull(target.data); id = `analysis-hull-${target.id}`; }
      else { const region = drawnRegion ?? viewportPolygon(); if (!region) { pushMsg({ role: 'agent', text: 'Draw a region to clip to.' }); return; } out = clipToRegion(target.data, region as Geometry); id = `analysis-clip-${target.id}`; }
      putLayer({ kind: 'geojson', id, source: 'analysis', label: `${intent.op}(${target.label})`, data: out.fc, style: { fill: [245, 158, 11, 70], line: [245, 158, 11, 255], lineWidth: 2, pointRadius: 5 } });
      pushMsg({ role: 'agent', text: out.summary });
      return;
    }
    pushMsg({ role: 'agent', text: `Not sure what to map for “${text}”. Try KB search, an OSM entity, a relation, or an analysis.` });
  }, [drawnRegion, putLayer, pushMsg, fitView]);

  const runAgent = useCallback((text: string) => (mode === 'live' ? runLive(text) : runLocal(text)), [mode, runLive, runLocal]);

  const onUpload = useCallback(async (files: File[]) => {
    // Always try to show GeoJSON on the map locally for instant feedback.
    for (const f of files) {
      if (/\.(geo)?json$/i.test(f.name)) {
        try { const j = JSON.parse(await f.text());
          const fc: FeatureCollection = j.type === 'FeatureCollection' ? j : j.type === 'Feature' ? { type: 'FeatureCollection', features: [j] } : { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: j, properties: {} }] };
          const name = f.name.replace(/\.(geo)?json$/i, '');
          putLayer({ kind: 'geojson', id: `upload-${name}`, source: 'upload', label: `Upload: ${name}`, data: fc, style: { fill: [239, 68, 68, 90], line: [239, 68, 68, 255], pointRadius: 6 }, fitBounds: true });
          fitView(fc);
          // Remember the upload's extent so the agent can bound Overpass by "the geojson I uploaded".
          try { const bb = layerBBox(fc); uploadContext.current = `The uploaded file "${name}" covers bbox [${bb.map((x) => x.toFixed(4)).join(', ')}] (minLon,minLat,maxLon,maxLat, EPSG:4326).`; } catch { /* */ }
        } catch { /* not parseable geojson */ }
      }
    }
    if (mode === 'live') {
      try {
        const recs: FileRecord[] = await uploadFiles(files, asAgentConfig());
        pendingFileIds.current.push(...recs.map((r) => r.file_id));
        pushMsg({ role: 'agent', text: `Uploaded ${recs.length} file(s) to the agent: ${recs.map((r) => r.filename).join(', ')}. They're attached to this conversation — ask me about them.` });
      } catch (e: any) { pushMsg({ role: 'agent', text: `Upload to agent failed: ${e.message}` }); }
    } else {
      pushMsg({ role: 'agent', text: `Loaded ${files.length} file(s) onto the map.` });
    }
  }, [mode, asAgentConfig, putLayer, fitView, pushMsg]);

  return (
    <div className={`app ${mapVisible ? 'map-on' : 'chat-only'}`}>
      <TopNav
        mode={mode} mapVisible={mapVisible} hasLayers={layers.length > 0}
        onToggleMap={() => setMapVisible((v) => !v)}
        onToggleSettings={() => setShowSettings((s) => !s)}
      />
      <div className="workspace">
        {mapVisible && (
          <LeftPanel
            layers={layers} selected={selected}
            onToggleLayer={toggleLayer} onRemoveLayer={removeLayerById}
            onFitLayer={fitLayer} onClearSelection={() => setSelected(null)}
          />
        )}
        {/* MOUNT the map only while it is shown. Hiding it with display:none left it mounted
            at ~40x30 and MapLibre never fired `load`: window.__map stayed unset and the
            canvas painted nothing (observed 400x300 inside an 820x646 container). */}
        {mapVisible && (
          <div className="mapwrap">
            <AgentMap layers={layers} drawnRegion={drawnRegion} drawPreview={drawPreview} drawMode={drawMode} onMapClick={onMapClick} onHover={() => {}} onFeatureClick={onFeatureClick} />
          </div>
        )}
        <ChatPanel
          messages={messages} busy={busy} drawMode={drawMode} hasRegion={!!drawnRegion} layers={layers}
          mode={mode} cfg={cfg} spatial={spatial} showSettings={showSettings} resolveUrl={resolveUrl}
          onSend={runAgent}
        onStop={() => abortRef.current?.abort()}
          onToggleDraw={() => { setDrawMode((d) => !d); firstCorner.current = null; }}
          onClearRegion={() => { setDrawnRegion(null); pushMsg({ role: 'agent', text: 'Region cleared.' }); }}
          onUpload={onUpload}
          onSetMode={setMode} onSetCfg={setCfg}
          onSetSpatial={setSpatial}
          onToggleSettings={() => setShowSettings((s) => !s)}
        />
      </div>
    </div>
  );
}

function short(v: any): string { try { const s = typeof v === 'string' ? v : JSON.stringify(v); return s.length > 80 ? s.slice(0, 80) + '…' : s; } catch { return ''; } }

function pickTarget(layers: LayerArtifact[], hint: string): LayerArtifact | null {
  if (!layers.length) return null;
  const h = hint.toLowerCase();
  const pool = layers.filter((l) => l.source !== 'analysis');
  const base = pool.length ? pool : layers;
  if (/\b(upload|uploaded|geojson|my data|my layer)\b/.test(h)) { const up = [...base].reverse().find((l) => l.source === 'upload'); if (up) return up; }
  const byWord = base.find((l) => h.includes(l.source) || h.split(/\s+/).some((w) => w.length > 3 && l.label.toLowerCase().includes(w)));
  return byWord ?? base[base.length - 1];
}
