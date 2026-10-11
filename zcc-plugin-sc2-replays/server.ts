import { spawn, type ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { readFile, realpath } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { ZccPluginApi } from '@zana-ai/zcc-plugin-sdk/server';
import { confined, emptyReview, readBounded, scanMatches, validateReplay,
  type IndexedMatch, type StoredReview } from './library.ts';

const pluginDirectory = dirname(fileURLToPath(import.meta.url));
const defaultProject = dirname(pluginDirectory);
const argsObject = (args: unknown): Record<string, any> =>
  args && typeof args === 'object' && !Array.isArray(args) ? args as Record<string, any> : {};

type Job = { matchId: string; state: 'running' | 'done' | 'error'; progress: number; error?: string };

export default async function plugin(zcc: ZccPluginApi) {
  const projects = await zcc.sdk.projects.list();
  const ownProject = projects.find(p => p.path === defaultProject);
  const settings = zcc.settings.define({
    sourceProject: { type: 'project', label: 'SC2 replay project', default: ownProject?.id ?? '' },
    exportMode: { type: 'select', label: 'Replay preparation engine', options: ['docker', 'native', 'disabled'], default: 'docker' }
  });
  const db = zcc.storage.database();
  db.migrate(['CREATE TABLE IF NOT EXISTS reviews (match_id TEXT PRIMARY KEY, payload TEXT NOT NULL)']);
  const readReview = (id: string): StoredReview => {
    const row = db.prepare('SELECT payload FROM reviews WHERE match_id=?').get(id) as { payload: string } | undefined;
    return row ? JSON.parse(row.payload) : emptyReview();
  };
  const writeReview = (id: string, review: StoredReview) => {
    db.prepare('INSERT INTO reviews VALUES (?,?) ON CONFLICT(match_id) DO UPDATE SET payload=excluded.payload')
      .run(id, JSON.stringify(review));
    zcc.realtime.publish('replays-changed', { id });
    return review;
  };
  let cached: IndexedMatch[] = [], cachedPath = '', scannedAt = 0;
  let active: ChildProcess | undefined;
  let cancelActive: (() => void) | undefined;
  let job: Job | null = null;
  let disposed = false;
  async function source() {
    const selected = (await settings.get()).sourceProject;
    const available = await zcc.sdk.projects.list();
    const project = available.find(p => p.id === selected) ??
      available.find(p => p.path === defaultProject && !selected);
    if (!project?.path) throw new Error('Choose a local SC2 project in this plugin’s settings');
    return { name: project.name, path: await realpath(project.path) };
  }
  async function index(force = false) {
    const project = await source();
    if (force || cachedPath !== project.path || Date.now() - scannedAt > 5000) {
      const found = await scanMatches(project.path);
      cached = found; cachedPath = project.path; scannedAt = Date.now();
    }
    return { project, matches: cached };
  }
  async function matchFor(input: unknown) {
    const id = argsObject(input).id;
    if (typeof id !== 'string' || !/^[a-f0-9]{24}$/.test(id)) throw new Error('Invalid replay id');
    const match = (await index()).matches.find(m => m.id === id);
    if (!match) throw new Error('Replay no longer exists in the configured project');
    return match;
  }
  async function load(match: IndexedMatch) {
    const root = join(match.project, 'runs');
    const bytes = await readBounded(root, join(match.directory, 'viewer', 'data.json'), 128 * 1024 * 1024);
    return validateReplay(JSON.parse(bytes.toString('utf8')));
  }
  zcc.rpc.method('list', async input => {
    const { project, matches } = await index(Boolean(argsObject(input).refresh));
    return { project: project.name, matches: matches.map(({ directory, project: ignored, ...match }) =>
      ({ ...match, ...readReview(match.id) })), job };
  });
  zcc.rpc.method('review', async input => {
    const match = await matchFor(input);
    return readReview(match.id);
  });
  zcc.rpc.method('annotate', async input => {
    const args = argsObject(input), match = await matchFor(args);
    return db.transaction(() => {
      const review = readReview(match.id);
      if (args.action === 'favorite' && typeof args.value === 'boolean') review.favorite = args.value;
      else if (args.action === 'note' && typeof args.value === 'string' && args.value.length <= 8000) review.note = args.value;
      else if (args.action === 'bookmark' && typeof args.t === 'number' && Number.isFinite(args.t) &&
          args.t >= 0 && args.t <= match.duration && typeof args.label === 'string' &&
          args.label.trim().length > 0 && args.label.trim().length <= 120 && review.bookmarks.length < 200) {
        review.bookmarks.push({ id: randomUUID(), t: Math.round(args.t * 10) / 10, label: args.label.trim() });
        review.bookmarks.sort((a, b) => a.t - b.t);
      } else if (args.action === 'remove-bookmark' && typeof args.bookmarkId === 'string') {
        review.bookmarks = review.bookmarks.filter(b => b.id !== args.bookmarkId);
      } else throw new Error('Invalid replay annotation');
      return writeReview(match.id, review);
    });
  });
  async function prepare(input: unknown) {
    const match = await matchFor(input);
    if (active) throw new Error('A replay is already being prepared');
    const mode = (await settings.get()).exportMode;
    if (mode === 'disabled') throw new Error('Replay preparation is disabled in settings');
    const root = join(match.project, 'runs');
    await confined(root, join(match.directory, match.replayFile));
    let command: string, argv: string[];
    if (mode === 'native') {
      command = 'uv';
      argv = ['run', '--offline', 'python', '-m', 'sc2mind.replay', join(match.directory, match.replayFile)];
    } else {
      // Fixed argv, no shell, no user-provided command. Reuse the working SC2 engine.
      command = 'docker';
      argv = ['compose', '-f', join(match.project, 'compose.yaml'), 'run', '--rm', '--no-deps',
        '--entrypoint', '/app/.venv/bin/python', '-v', `${join(match.project, 'src')}:/app/src:ro`,
        'bot', '-m', 'sc2mind.replay', `/app/runs/${match.folder}/${match.replayFile}`];
    }
    if (active) throw new Error('A replay is already being prepared');
    job = { matchId: match.id, state: 'running', progress: 0 };
    const child = spawn(command, argv, { cwd: match.project, shell: false, stdio: ['ignore', 'pipe', 'pipe'] });
    active = child;
    zcc.realtime.publish('replays-changed', job);
    let buffer = '', diagnostics = '', killed = false;
    const timeout = setTimeout(() => { killed = true; child.kill('SIGTERM'); }, 10 * 60 * 1000);
    cancelActive = () => { clearTimeout(timeout); active = undefined; child.kill('SIGTERM'); };
    const finish = async (error?: string) => {
      clearTimeout(timeout);
      if (active !== child) return;
      active = undefined; cancelActive = undefined;
      if (!error) {
        try { await load(match); } catch { error = 'The engine did not produce a valid replay export'; }
      }
      if (disposed) return;
      job = { matchId: match.id, state: error ? 'error' : 'done', progress: error ? job?.progress ?? 0 : 1, error };
      scannedAt = 0;
      if (error) zcc.log.warn(`Replay preparation failed: ${error}. ${diagnostics.slice(-1800)}`);
      if (!disposed) zcc.realtime.publish('replays-changed', job);
    };
    child.stdout?.on('data', chunk => {
      buffer = (buffer + chunk.toString()).slice(-16000);
      const lines = buffer.split('\n'); buffer = lines.pop() ?? '';
      for (const line of lines) {
        try {
          const update = JSON.parse(line);
          if (job?.state === 'running' && typeof update.progress === 'number') {
            job.progress = Math.max(0, Math.min(1, update.progress));
            zcc.realtime.publish('replays-changed', job);
          }
        } catch { /* Engine messages are not progress records. */ }
      }
    });
    child.stderr?.on('data', chunk => { diagnostics = (diagnostics + chunk.toString()).slice(-4000); });
    child.on('error', error => { void finish(`Could not start ${command}: ${error.message}`); });
    child.on('close', code => { void finish(code === 0 && !killed ? undefined :
      killed ? 'Replay preparation exceeded ten minutes' : 'SC2 could not prepare this replay. Check the engine version and plugin logs.'); });
    return job;
  }
  zcc.rpc.method('prepare', prepare);
  zcc.http.route('GET', '/data', async request => {
    try { return { json: await load(await matchFor({ id: request.query.id })),
      headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } }; }
    catch (error) { return { status: 400, json: { error: error instanceof Error ? error.message : 'Replay unavailable' } }; }
  });
  zcc.http.route('GET', '/viewer', async request => {
    try {
      const match = await matchFor({ id: request.query.id });
      const config = { id: match.id, map: match.map, result: match.result, enemy: match.enemy,
        difficulty: match.difficulty, opening: match.opening, memorySaved: match.memorySaved,
        offline: match.offline, dataUrl: `/api/v1/plugins/${zcc.pluginId}/http/data?id=${match.id}` };
      const template = await readFile(join(pluginDirectory, 'viewer.html'), 'utf8');
      return { body: template.replace('/*__REPLAY_CONFIG__*/',
        `window.replayConfig=${JSON.stringify(config).replace(/</g, '\\u003c')};`),
        headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
          'X-Content-Type-Options': 'nosniff',
          'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'" } };
    } catch { return { status: 404, body: 'Replay unavailable' }; }
  });
  zcc.http.route('GET', '/download', async request => {
    try {
      const match = await matchFor({ id: request.query.id });
      return { body: await readBounded(join(match.project, 'runs'), join(match.directory, match.replayFile), 64 * 1024 * 1024),
        headers: { 'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="replay.SC2Replay"',
          'X-Content-Type-Options': 'nosniff' } };
    } catch { return { status: 404, body: 'Replay unavailable' }; }
  });
  zcc.cli.register({ name: 'sc2-replays', summary: 'List recorded SC2 matches and prepare tactical playback',
    commands: [{ name: 'list', summary: 'List replays in the configured project', usage: 'zcc sc2-replays list' },
      { name: 'prepare', summary: 'Export a replay through SC2, without models or learning writes', usage: 'zcc sc2-replays prepare <id>' }],
    async run(argv) {
      try {
        if (argv[0] === 'list') {
          const matches = (await index(true)).matches.map(({ directory, project, ...match }) => match);
          return { exitCode: 0, stdout: JSON.stringify(matches, null, 2) };
        }
        if (argv[0] === 'prepare' && argv[1]) return { exitCode: 0, stdout: JSON.stringify(await prepare({ id: argv[1] })) };
        return { exitCode: 2, stderr: 'Usage: zcc sc2-replays list | prepare <id>' };
      } catch (e) { return { exitCode: 1, stderr: e instanceof Error ? e.message : String(e) }; }
    }
  });
  zcc.onDispose(() => { disposed = true; cancelActive?.(); });
  settings.onChange(() => { scannedAt = 0; zcc.realtime.publish('replays-changed', {}); });
  zcc.log.info('SC2 Replays loaded');
}
