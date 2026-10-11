import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { definePluginApp, useRealtime, useRpc } from '@zana-ai/zcc-plugin-sdk/app';
import type { Match, StoredReview, PostGameReport } from './library.ts';
import { styles } from './styles.ts';

type LibraryMatch = Match & StoredReview;
type Library = { project: string; matches: LibraryMatch[]; job: {
  matchId: string; state: 'running' | 'done' | 'error'; progress: number; error?: string;
} | null };
export const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

function PostGameAnalysis({ match }: { match: LibraryMatch }) {
  const rpc = useRpc();
  const [report, setReport] = useState<PostGameReport | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true; setReport(null); setError('');
    if (match.analysisStatus) void rpc.call('analysis', { id: match.id }).then(value => {
      if (active) setReport(value as PostGameReport);
    }, e => { if (active) setError(message(e)); });
    return () => { active = false; };
  }, [rpc, match.id, match.analysisStatus]);
  const sample = report?.evidence.general_patterns;
  const blocked = report?.evidence.facts['metrics.observed_supply_blocked_s'];
  return <details className="sc2-analysis">
    <summary>Post-game analysis <span>{match.analysisStatus === 'complete' ? 'OpenAI review ready' :
      match.analysisStatus === 'pending' ? 'Reviewing…' : match.analysisStatus ? 'Measured evidence ready' : 'Awaiting a report'}</span></summary>
    <div className="sc2-analysis-body">{error ? <p role="alert">{error}</p> : report ? <>
      <p className="sc2-analysis-summary">{report.review.summary}</p>
      <p className="sc2-analysis-scope">{sample?.matches} eligible matches · {sample?.conditions} distinct conditions · {sample?.maps.length} maps · {sample?.races.length} races
        {typeof blocked === 'number' && ` · ${blocked.toFixed(1)}s observed supply blocks`}</p>
      <p className="sc2-analysis-limit">Provisional hypotheses for controlled tests. Suggestions do not change the bot automatically.</p>
      <p className="sc2-analysis-limit">Test one change against the unchanged baseline across at least two maps, all three races, and held-out seeds. Keep engine, difficulty, speed and memory settings equal; check regressions alongside wins.</p>
      {report.status === 'failed' && <p>The model review was unavailable; measured analysis was retained.</p>}
      {report.review.strengths.length > 0 && <div><h3>What worked</h3><ul>{report.review.strengths.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
      {report.review.findings.map((finding, i) => <article className="sc2-finding" key={i}>
        <div className="sc2-eyebrow">{finding.priority} priority · {finding.category}</div>
        <h3>{finding.hypothesis}</h3><p><b>Try:</b> {finding.proposed_change}</p>
        <p><b>Test:</b> {finding.validation}</p><p className="sc2-analysis-limit"><b>Uncertainty:</b> {finding.uncertainty}</p>
        <details><summary>Recorded evidence</summary><ul>{finding.evidence_ids.map(ref =>
          <li key={ref}><code>{ref}</code>: {JSON.stringify(report.evidence.facts[ref])}</li>)}</ul></details>
      </article>)}
      <details><summary>Missing evidence and limits</summary><ul>{[...new Set([...report.evidence.warnings, ...report.review.missing_evidence])].map((s, i) => <li key={i}>{s}</li>)}</ul></details>
    </> : <p>{match.analysisStatus ? 'Reading the saved report…' : 'New completed games save their analysis here automatically. Existing matches can be reviewed with sc2mind-analyze.'}</p>}</div>
  </details>;
}

function Review({ match, time, busy, save, seek }: {
  match: LibraryMatch; time: number; busy: boolean;
  save: (args: object) => Promise<void>; seek: (t: number) => void;
}) {
  const [label, setLabel] = useState('');
  const [note, setNote] = useState(match.note);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    try { await save({ action: 'bookmark', t: time, label }); setLabel(''); setError(''); }
    catch (e) { setError(message(e)); }
  }
  return <section className="sc2-review" aria-label="Replay review">
    <div className="sc2-review-heading"><h2>Keep the moments that matter.</h2><span>Saved locally · Separate from bot memory</span></div>
    <div className="sc2-review-grid"><div>
      <form onSubmit={submit} className="sc2-bookmark-form">
        <label htmlFor="sc2-bookmark-label">Bookmark at {clock(time)}</label>
        <div><input id="sc2-bookmark-label" value={label} maxLength={120} placeholder="What happened here?"
          onChange={e => setLabel(e.target.value)} /><button disabled={busy || !match.ready || !label.trim()}>Save moment</button></div>
      </form>
      <div className="sc2-bookmarks">{match.bookmarks.length ? match.bookmarks.map(b =>
        <div key={b.id}><button className="sc2-moment" onClick={() => seek(b.t)}><b>{clock(b.t)}</b> {b.label}</button>
          <button className="sc2-remove" aria-label={`Remove bookmark ${b.label}`} disabled={busy}
            onClick={() => { void save({ action: 'remove-bookmark', bookmarkId: b.id }).catch(e => setError(message(e))); }}>×</button></div>
      ) : <p>Pause at an interesting decision, add a bookmark, and jump back to it anytime.</p>}</div>
    </div><form onSubmit={event => {
      event.preventDefault(); void save({ action: 'note', value: note }).then(() => { setSaved(true); setError(''); }, e => setError(message(e)));
    }}><label htmlFor="sc2-review-note">Your match notes</label><textarea id="sc2-review-note" value={note} maxLength={8000}
      placeholder="What worked? What would you change next time?" onChange={e => { setNote(e.target.value); setSaved(false); }} />
      <div className="sc2-note-footer"><span aria-live="polite">{saved ? 'Notes saved' : 'Review observations in your own words.'}</span>
        <button disabled={busy || note === match.note}>Save notes</button></div></form></div>
    {error && <p role="alert" className="sc2-error">{error}</p>}
  </section>;
}

export function ReplayStudio({ pluginId }: { pluginId: string }) {
  const rpc = useRpc();
  const [library, setLibrary] = useState<Library | null>(null);
  const [selected, setSelected] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [time, setTime] = useState(0);
  const [frameHeight, setFrameHeight] = useState(860);
  const [viewerGeneration, setViewerGeneration] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const mounted = useRef(true);
  const requestNumber = useRef(0);
  const previousJob = useRef<string>('');
  const refetch = useCallback((refresh = false) => {
    const request = ++requestNumber.current;
    void rpc.call('list', { refresh }).then(value => {
      if (!mounted.current || request !== requestNumber.current) return;
      const data = value as Library;
      setLibrary(data); setError('');
      const completed = data.job?.state === 'done' ? data.job.matchId : '';
      if (completed && completed !== previousJob.current) setViewerGeneration(n => n + 1);
      previousJob.current = completed;
      setSelected(current => data.matches.some(m => m.id === current) ? current :
        (data.matches.find(m => m.ready && m.result === 'Victory') ?? data.matches.find(m => m.ready) ?? data.matches[0])?.id ?? '');
    }, e => { if (mounted.current && request === requestNumber.current) setError(message(e)); });
  }, [rpc]);
  useEffect(() => {
    mounted.current = true; refetch();
    const timer = setInterval(() => refetch(), 20000);
    return () => { mounted.current = false; clearInterval(timer); };
  }, [refetch]);
  useRealtime('replays-changed', () => refetch());
  useEffect(() => { setTime(0); }, [selected]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== frame.current?.contentWindow ||
          event.data?.id !== selected) return;
      if (event.data.type === 'sc2-replay-time' && Number.isFinite(event.data.t)) setTime(event.data.t);
      if (event.data.type === 'sc2-replay-height' && Number.isFinite(event.data.height) &&
          event.data.height >= 300 && event.data.height <= 6000) setFrameHeight(event.data.height);
    };
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, [selected]);
  const match = library?.matches.find(m => m.id === selected);
  const matches = library?.matches.filter(m => {
    const text = `${m.map} ${m.enemy} ${m.difficulty} ${m.opening} ${m.folder} ${m.note}`.toLowerCase();
    return text.includes(query.toLowerCase()) && (filter === 'all' ||
      (filter === 'favorites' ? m.favorite : m.result.toLowerCase() === filter));
  }) ?? [];
  const completed = library?.matches.filter(m => ['Victory', 'Defeat', 'Tie'].includes(m.result)) ?? [];
  async function save(args: object) {
    if (!match) return;
    setBusy(true);
    try {
      const review = await rpc.call('annotate', { id: match.id, ...args }) as StoredReview;
      setLibrary(current => current ? { ...current, matches: current.matches.map(m => m.id === match.id ? { ...m, ...review } : m) } : null);
    } finally { setBusy(false); }
  }
  const seek = (t: number) => frame.current?.contentWindow?.postMessage({ type: 'sc2-replay-seek', id: selected, t }, location.origin);
  const prefix = `/api/v1/plugins/${encodeURIComponent(pluginId)}/http`;
  const preparing = library?.job?.state === 'running';
  const thisJob = library?.job?.matchId === selected ? library.job : null;
  return <div className="sc2-studio"><style>{styles}</style>
    <header className="sc2-header"><div><div className="sc2-eyebrow">SC2 AI · Replay studio</div><h1>Every match has a story.</h1>
      <p>Watch your matches. Understand your bot. Keep the moments that matter.</p></div>
      <div className="sc2-header-tools"><span>{completed.length} completed {completed.length === 1 ? 'match' : 'matches'}</span>
        <button onClick={() => refetch(true)} aria-label="Refresh replay library">↻ Refresh</button></div></header>
    {error && <div role="alert" className="sc2-error">{error}</div>}
    <div className="sc2-workspace"><aside className="sc2-library" aria-label="Replay library">
      <div className="sc2-library-heading"><h2>Match library</h2><small>{library?.project ?? 'Connecting…'}</small></div>
      <input type="search" aria-label="Search replays" placeholder="Search map, race, notes…" value={query} onChange={e => setQuery(e.target.value)} />
      <select aria-label="Filter replays" value={filter} onChange={e => setFilter(e.target.value)}>
        <option value="all">All replays</option><option value="victory">Victories</option><option value="defeat">Defeats</option><option value="favorites">Favorites</option></select>
      <div className="sc2-match-list">{!library && !error ? <p>Reading your recorded matches…</p> : matches.length ? matches.map(m =>
        <button key={m.id} className={`sc2-match ${m.id === selected ? 'selected' : ''}`} onClick={() => setSelected(m.id)} aria-pressed={m.id === selected}>
          <div><strong>{m.map}</strong><span className={`sc2-result ${m.result.toLowerCase()}`}>{m.result}</span></div>
          <p>{m.difficulty} {m.enemy} · {m.opening}</p><small>{new Date(m.createdAt).toLocaleDateString()} · {m.duration ? clock(m.duration) : 'Awaiting playback'} {m.favorite ? '· ★' : ''}</small>
          <div className="sc2-match-tags"><span>{m.ready ? 'Playback ready' : 'Prepare replay'}</span>{m.bookmarks.length > 0 && <span>{m.bookmarks.length} moments</span>}{m.memorySaved && <span>Memory saved</span>}</div>
        </button>) : <p>{query || filter !== 'all' ? 'No replays match this search.' : 'Completed matches saved in your project’s runs folder will appear here.'}</p>}</div>
      <div className="sc2-library-foot">Refreshes every 20 seconds.<br />Viewing replays makes no model calls.</div>
    </aside><main className="sc2-main">{match ? <>
      <div className="sc2-match-toolbar"><span>{match.offline ? 'Rule-based controller' : 'Jev + OpenAI'} · {match.opening} opening</span><div>
        <button aria-label={match.favorite ? 'Remove from favorites' : 'Add to favorites'} aria-pressed={match.favorite} disabled={busy}
          onClick={() => { void save({ action: 'favorite', value: !match.favorite }).catch(e => setError(message(e))); }}>{match.favorite ? '★ Favorited' : '☆ Favorite'}</button>
        <a href={`${prefix}/download?id=${match.id}`} download="replay.SC2Replay">Download .SC2Replay</a></div></div>
      <PostGameAnalysis key={match.id} match={match} />
      {match.ready ? <iframe key={`${match.id}:${viewerGeneration}`} ref={frame} title={`Tactical replay of ${match.map}`}
        src={`${prefix}/viewer?id=${match.id}`} className="sc2-frame" style={{ height: frameHeight }} /> : <div className="sc2-prepare"><div className="sc2-eyebrow">Recorded match</div><h2>Make this replay watchable.</h2>
        <p>Read unit movement and terrain from the saved SC2 replay. The engine runs in the background; no new match is played.</p>
        <button className="sc2-primary" disabled={preparing} onClick={() => {
          setError(''); void rpc.call('prepare', { id: match.id }).then(() => refetch(), e => setError(message(e)));
        }}>{thisJob?.state === 'running' ? `Preparing · ${Math.round(thisJob.progress * 100)}%` : preparing ? 'Another replay is being prepared' : 'Prepare replay'}</button>
        {thisJob?.state === 'error' && <p role="alert" className="sc2-error">{thisJob.error}</p>}</div>}
      <Review key={match.id} match={match} time={time} busy={busy} save={save} seek={seek} />
    </> : <div className="sc2-empty"><div className="sc2-eyebrow">Ready for the next story</div><h2>Your replay studio is ready.</h2><p>Play a match with the bot, then open its replay here. Choose the SC2 project in plugin settings if needed.</p></div>}</main></div>
    <footer className="sc2-studio-foot">Recorded evidence · Full-map 2D playback · Movement interpolated between samples</footer>
  </div>;
}

export default definePluginApp(app => {
  app.slots.navPanel({ id: 'main', title: 'SC2 Replays', icon: 'Play', path: 'sc2-replays', component: ReplayStudio });
});
