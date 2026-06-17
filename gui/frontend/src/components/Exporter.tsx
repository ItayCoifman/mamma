import { useEffect, useState, useCallback } from 'react';
import { Download, Check, Loader2, AlertTriangle, Box, FolderOpen, ChevronDown, ChevronRight } from 'lucide-react';

/** SMPL-X Exporter tab: in-tab setup (Blender + add-on downloads), a readiness
 *  check, a completed-sequence picker, format + param selection, and the export
 *  run. Downloads live here (not the Home page) to keep the start uncluttered. */

interface Readiness { blender: { present: boolean; path: string }; addon: { present: boolean; path: string }; }
interface Seq { tag: string; capture: string; seq: string; people: number; ma_3d_dir: string; ma_cap_dir: string; already_exported: boolean; }
interface Job { id: string; kind: string; state: 'running' | 'ready' | 'error'; log_tail: string[]; outputs: string[]; error: string | null; }

const ALL_FORMATS = [
  { id: 'npz', label: 'npz', hint: 'Add-on native (no Blender)', blender: false },
  { id: 'fbx', label: 'FBX', hint: 'Rigged, game engines', blender: true },
  { id: 'abc', label: 'Alembic', hint: 'Vertex cache, render engines', blender: true },
  { id: 'bvh', label: 'BVH', hint: 'Skeleton motion', blender: true },
  { id: 'usd', label: 'USD', hint: 'UE5 / Unity / Houdini', blender: true },
];

async function jget<T>(url: string): Promise<T> { const r = await fetch(url); return r.json(); }
async function jpost<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}

export function Exporter() {
  const [ready, setReady] = useState<Readiness | null>(null);
  const [seqs, setSeqs] = useState<Seq[]>([]);
  const [sel, setSel] = useState<string>('');
  const [formats, setFormats] = useState<Record<string, boolean>>({ npz: true, fbx: false, abc: false, bvh: false, usd: false });
  const [upAxis, setUpAxis] = useState('z');
  const [fps, setFps] = useState('');
  const [fbxTarget, setFbxTarget] = useState('UNITY');
  const [job, setJob] = useState<Job | null>(null);
  const [dlJob, setDlJob] = useState<Job | null>(null);
  const [showAddonForm, setShowAddonForm] = useState(false);
  const [creds, setCreds] = useState({ username: '', password: '' });
  const [setupOpen, setSetupOpen] = useState(true);

  const refreshReady = useCallback(async () => setReady(await jget<Readiness>('/api/exporter/readiness')), []);
  const refreshSeqs = useCallback(async () => setSeqs((await jget<{ sequences: Seq[] }>('/api/exporter/sequences')).sequences), []);

  useEffect(() => { refreshReady(); refreshSeqs(); }, [refreshReady, refreshSeqs]);

  const toolsReady = !!ready?.blender.present && !!ready?.addon.present;
  // Collapse setup once everything is ready (but let the user reopen it).
  useEffect(() => { if (toolsReady) setSetupOpen(false); }, [toolsReady]);

  // Poll a download job until done, then refresh readiness.
  useEffect(() => {
    if (!dlJob || dlJob.state !== 'running') return;
    const t = setInterval(async () => {
      const j = await jget<Job>(`/api/exporter/job/${dlJob.id}`);
      setDlJob(j);
      if (j.state !== 'running') { refreshReady(); }
    }, 1500);
    return () => clearInterval(t);
  }, [dlJob, refreshReady]);

  // Poll the export job.
  useEffect(() => {
    if (!job || job.state !== 'running') return;
    const t = setInterval(async () => setJob(await jget<Job>(`/api/exporter/job/${job.id}`)), 1500);
    return () => clearInterval(t);
  }, [job]);

  const startBlender = async () => setDlJob(await jpost<Job>('/api/exporter/download-blender').then(r => ({ ...(r as { job_id: string }), id: (r as { job_id: string }).job_id, state: 'running', log_tail: [], outputs: [], error: null, kind: 'blender' } as Job)));
  const startAddon = async () => {
    const r = await jpost<{ job_id: string }>('/api/exporter/download-addon', creds);
    setShowAddonForm(false); setCreds({ username: '', password: '' });
    setDlJob({ id: r.job_id, state: 'running', log_tail: [], outputs: [], error: null, kind: 'addon' });
  };

  const selSeq = seqs.find(s => `${s.tag}/${s.capture}/${s.seq}` === sel);
  const chosen = ALL_FORMATS.filter(f => formats[f.id]).map(f => f.id);
  const needsBlender = chosen.some(f => ALL_FORMATS.find(x => x.id === f)?.blender);
  const canExport = !!selSeq && chosen.length > 0 && (!needsBlender || toolsReady) && job?.state !== 'running';

  const runExport = async () => {
    if (!selSeq) return;
    const r = await jpost<{ job_id: string }>('/api/exporter/export', {
      tag: selSeq.tag, capture: selSeq.capture, seq: selSeq.seq,
      ma_3d_dir: selSeq.ma_3d_dir, ma_cap_dir: selSeq.ma_cap_dir,
      formats: chosen, up_axis: upAxis, fps: fps ? Number(fps) : undefined, fbx_target: fbxTarget,
    });
    setJob({ id: r.job_id, state: 'running', log_tail: [], outputs: [], error: null, kind: 'export' });
  };

  const card = 'bg-surface-1 border border-border-subtle rounded-xl p-5 shadow-sm shadow-black/30';
  const ToolRow = ({ name, present, onDl }: { name: string; present: boolean; onDl: () => void }) => (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-sm text-foreground">{name}</span>
      {present ? (
        <span className="inline-flex items-center gap-1.5 text-status-completed text-xs"><Check className="w-4 h-4" /> Ready</span>
      ) : (
        <button onClick={onDl} className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-primary text-primary-foreground rounded-md text-xs font-medium">
          <Download className="w-3.5 h-3.5" /> Download
        </button>
      )}
    </div>
  );

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-4">
      <div>
        <h1 className="text-foreground text-xl font-semibold">Exporter</h1>
        <p className="text-foreground-muted text-sm mt-1">Export SMPL-X fits to Blender / engine formats (npz, FBX, Alembic, BVH, USD).</p>
      </div>

      {/* ① Export tools */}
      <div className={card}>
        <button onClick={() => setSetupOpen(o => !o)} className="flex items-center gap-2 w-full text-left">
          {setupOpen ? <ChevronDown className="w-4 h-4 text-foreground-subtle" /> : <ChevronRight className="w-4 h-4 text-foreground-subtle" />}
          <span className="text-sm font-medium text-foreground">Export tools</span>
          {toolsReady
            ? <span className="ml-auto inline-flex items-center gap-1.5 text-status-completed text-xs"><Check className="w-4 h-4" /> Blender + add-on ready</span>
            : <span className="ml-auto text-status-pending text-xs">setup needed for FBX/ABC/BVH/USD</span>}
        </button>
        {setupOpen && (
          <div className="mt-3 pl-6 space-y-1">
            <ToolRow name="Portable Blender 4.5 LTS" present={!!ready?.blender.present} onDl={startBlender} />
            <div>
              <ToolRow name="SMPL-X add-on (gated)" present={!!ready?.addon.present} onDl={() => setShowAddonForm(s => !s)} />
              {showAddonForm && !ready?.addon.present && (
                <div className="ml-0 mt-1 rounded-md border border-border-subtle bg-surface-2/60 p-3 space-y-2">
                  <p className="text-foreground-subtle text-xs">Sign in with your SMPL-X account (smpl-x.is.tue.mpg.de).</p>
                  <input className="w-full bg-surface-2 border border-border rounded-md px-2 py-1 text-xs text-foreground" placeholder="Username" value={creds.username} onChange={e => setCreds(c => ({ ...c, username: e.target.value }))} />
                  <input type="password" className="w-full bg-surface-2 border border-border rounded-md px-2 py-1 text-xs text-foreground" placeholder="Password" value={creds.password} onChange={e => setCreds(c => ({ ...c, password: e.target.value }))} />
                  <button onClick={startAddon} disabled={!creds.username || !creds.password} className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-primary text-primary-foreground rounded-md text-xs font-medium disabled:opacity-40">
                    <Download className="w-3.5 h-3.5" /> Download add-on
                  </button>
                </div>
              )}
            </div>
            <p className="text-foreground-faint text-xs pt-1">The <code className="font-mono">npz</code> format needs none of this; FBX/ABC/BVH/USD need both.</p>
            {dlJob && (
              <div className="text-xs pt-1">
                {dlJob.state === 'running' && <span className="inline-flex items-center gap-1.5 text-status-running"><Loader2 className="w-3.5 h-3.5 animate-spin" /> downloading {dlJob.kind}…</span>}
                {dlJob.state === 'error' && <span className="inline-flex items-center gap-1.5 text-status-failed"><AlertTriangle className="w-3.5 h-3.5" /> {dlJob.error}</span>}
                {dlJob.state === 'ready' && <span className="inline-flex items-center gap-1.5 text-status-completed"><Check className="w-3.5 h-3.5" /> {dlJob.kind} ready</span>}
                {dlJob.state !== 'ready' && dlJob.log_tail.length > 0 && <span className="text-foreground-faint ml-2 font-mono">{dlJob.log_tail[dlJob.log_tail.length - 1].slice(0, 80)}</span>}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ② What to export */}
      <div className={card}>
        <div className="text-sm font-medium text-foreground mb-2">What to export</div>
        {seqs.length === 0 ? (
          <p className="text-foreground-muted text-sm">No completed <code className="font-mono">ma_3d</code> results found. Run the pipeline first.</p>
        ) : (
          <select value={sel} onChange={e => setSel(e.target.value)} className="w-full bg-surface-2 border border-border rounded-md px-2 py-2 text-sm text-foreground">
            <option value="">Select a sequence…</option>
            {seqs.map(s => {
              const k = `${s.tag}/${s.capture}/${s.seq}`;
              return <option key={k} value={k}>{s.capture} / {s.seq} — {s.people} {s.people === 1 ? 'person' : 'people'} (run {s.tag}){s.already_exported ? ' · exported' : ''}</option>;
            })}
          </select>
        )}
      </div>

      {/* ③ Formats & options */}
      <div className={card}>
        <div className="text-sm font-medium text-foreground mb-2">Formats & options</div>
        <div className="flex flex-wrap gap-2">
          {ALL_FORMATS.map(f => {
            const disabled = f.blender && !toolsReady;
            const on = formats[f.id];
            return (
              <button key={f.id} disabled={disabled} title={disabled ? 'Needs Blender — set up Export tools above' : f.hint}
                onClick={() => setFormats(p => ({ ...p, [f.id]: !p[f.id] }))}
                className={`inline-flex flex-col items-start px-3 py-1.5 rounded-md border text-xs transition-colors ${on ? 'bg-primary-muted border-primary/40 text-foreground' : 'bg-surface-2 border-border text-foreground-muted'} ${disabled ? 'opacity-40 cursor-not-allowed' : 'hover:border-primary/40'}`}>
                <span className="font-medium">{on ? '✓ ' : ''}{f.label}</span>
                <span className="text-[10px] text-foreground-faint">{disabled ? 'needs Blender' : f.hint}</span>
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-4 mt-3 text-xs text-foreground-muted">
          <label className="flex items-center gap-1.5">Up-axis
            <select value={upAxis} onChange={e => setUpAxis(e.target.value)} className="bg-surface-2 border border-border rounded px-1.5 py-0.5 text-foreground">
              <option value="z">z</option><option value="y">y</option><option value="x">x</option>
            </select>
          </label>
          <label className="flex items-center gap-1.5">FPS
            <input value={fps} onChange={e => setFps(e.target.value)} placeholder="auto" className="w-16 bg-surface-2 border border-border rounded px-1.5 py-0.5 text-foreground" />
          </label>
          {formats.fbx && (
            <label className="flex items-center gap-1.5">FBX target
              <select value={fbxTarget} onChange={e => setFbxTarget(e.target.value)} className="bg-surface-2 border border-border rounded px-1.5 py-0.5 text-foreground">
                <option value="UNITY">Unity</option><option value="UNREAL">Unreal</option>
              </select>
            </label>
          )}
        </div>
      </div>

      {/* ④ Export */}
      <div className={card}>
        <button onClick={runExport} disabled={!canExport}
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed">
          <Box className="w-4 h-4" /> Export
        </button>
        {!canExport && needsBlender && !toolsReady && <span className="ml-3 text-status-pending text-xs">set up Export tools for the Blender formats</span>}
        {job && (
          <div className="mt-3 text-sm">
            {job.state === 'running' && <span className="inline-flex items-center gap-2 text-status-running"><Loader2 className="w-4 h-4 animate-spin" /> exporting…</span>}
            {job.state === 'error' && <span className="inline-flex items-center gap-2 text-status-failed"><AlertTriangle className="w-4 h-4" /> {job.error}</span>}
            {job.state === 'ready' && (
              <div>
                <span className="inline-flex items-center gap-2 text-status-completed"><Check className="w-4 h-4" /> wrote {job.outputs.length} file(s)</span>
                <ul className="mt-2 space-y-0.5">
                  {job.outputs.map(o => (
                    <li key={o} className="flex items-center gap-1.5 text-foreground-faint text-xs font-mono"><FolderOpen className="w-3 h-3 flex-shrink-0" /> {o}</li>
                  ))}
                </ul>
              </div>
            )}
            {job.state === 'running' && job.log_tail.length > 0 && (
              <pre className="mt-2 text-[11px] text-foreground-faint font-mono max-h-32 overflow-auto whitespace-pre-wrap">{job.log_tail.slice(-8).join('\n')}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
