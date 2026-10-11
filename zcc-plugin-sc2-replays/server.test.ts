// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import type { ZccPluginApi } from '@zana-ai/zcc-plugin-sdk/server';
import plugin from './server.ts';
import { confined, scanMatches, validateReplay, validateAnalysis } from './library.ts';

const roots: string[] = [];
const databases: DatabaseSync[] = [];
afterEach(async () => {
  databases.splice(0).forEach(db => db.close());
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
});
async function project() {
  const root = await mkdtemp(join(tmpdir(), 'sc2-replays-test-')); roots.push(root);
  const run = join(root, 'runs', 'group', 'match-1'); await mkdir(join(run, 'viewer'), { recursive: true });
  await writeFile(join(run, 'replay.SC2Replay'), 'recorded replay');
  await writeFile(join(run, 'result.json'), JSON.stringify({ result: 'Victory', map: 'AcropolisLE', memory_saved: true }));
  await writeFile(join(run, 'viewer', 'metadata.json'), JSON.stringify({ duration: 10 }));
  await writeFile(join(run, 'viewer', 'data.json'), JSON.stringify(fixture()));
  return { root, run };
}
function host(root: string, db: DatabaseSync) {
  const rpc = new Map<string, (input: unknown) => any>();
  const routes = new Map<string, (request: any) => any>();
  const api = {
    pluginId: 'sc2-replays', log: { info() {}, warn() {} },
    sdk: { projects: { list: async () => [{ id: 'test-project', name: 'Test SC2', path: root }] } },
    settings: { define: () => ({ get: async () => ({ sourceProject: 'test-project', exportMode: 'disabled' }), onChange() {} }) },
    storage: { database: () => ({
      migrate: (statements: string[]) => statements.forEach(s => db.exec(s)),
      prepare: (sql: string) => db.prepare(sql),
      transaction: (fn: () => any) => { db.exec('BEGIN'); try { const value = fn(); db.exec('COMMIT'); return value; }
        catch (e) { db.exec('ROLLBACK'); throw e; } }
    }) },
    realtime: { publish() {} }, rpc: { method: (name: string, fn: (input: unknown) => any) => rpc.set(name, fn) },
    http: { route: (method: string, path: string, fn: (request: any) => any) => routes.set(path, fn) },
    cli: { register() {} }, onDispose() {}
  };
  return { api: api as unknown as ZccPluginApi, call: (name: string, input?: unknown) => rpc.get(name)!(input), routes };
}
const fixture = () => ({ duration: 10, bounds: { minX: 0, minY: 0, maxX: 2, maxY: 2 },
  terrain: { width: 2, height: 2, bits: 8, data: [1, 1, 1, 1] },
  pathing: { width: 2, height: 2, bits: 1, data: [240] }, types: { '1': { name: 'SCV', structure: false } },
  frames: [{ t: 0, units: [['tag', 1, 1, 0, 0, 1, 1, 1, false]], workers: 12, army: 0, minerals: 50, gas: 0, supply: 12, cap: 15 }],
  plans: [], decisions: [] });

describe('replay discovery and confinement', () => {
  it('finds nested matches with stable IDs and skips symlinked directories', async () => {
    const { root, run } = await project();
    await symlink(run, join(root, 'runs', 'shortcut'));
    const matches = await scanMatches(root);
    expect(matches).toHaveLength(1);
    expect(matches[0]).toMatchObject({ map: 'AcropolisLE', result: 'Victory', ready: true, memorySaved: true, duration: 10 });
    expect((await scanMatches(root))[0].id).toBe(matches[0].id);
  });
  it('rejects symlink escapes and a symlinked runs root', async () => {
    const { root, run } = await project();
    await writeFile(join(root, 'private.txt'), 'must never be served');
    await symlink(join(root, 'private.txt'), join(run, 'escape'));
    await expect(confined(join(root, 'runs'), join(run, 'escape'))).rejects.toThrow('outside');
    const other = await mkdtemp(join(tmpdir(), 'sc2-replays-outside-')); roots.push(other);
    await symlink(join(root, 'runs'), join(other, 'runs'));
    await expect(scanMatches(other)).rejects.toThrow('symlink');
  });
});
describe('saved review and served replay', () => {
  it('reads completed analysis without calling models and rejects escaped paths', async () => {
    const { root, run } = await project(); const db = new DatabaseSync(':memory:'); databases.push(db);
    const analysis = { schema_version: 1, status: 'offline',
      review: { summary: 'Supply forecasting needs evaluation.', strengths: [], findings: [], missing_evidence: [] },
      evidence: { facts: { 'metrics.observed_supply_blocked_s': 30 }, warnings: [],
        general_patterns: { matches: 1, conditions: 1, maps: ['AcropolisLE'], races: ['Zerg'] } } };
    await writeFile(join(run, 'analysis.json'), JSON.stringify(analysis));
    const instance = host(root, db); await plugin(instance.api);
    const match = (await instance.call('list')).matches[0];
    expect(match.analysisStatus).toBe('offline');
    expect(await instance.call('analysis', { id: match.id })).toEqual(analysis);
    await expect(instance.call('analysis', { id: '../../.env' })).rejects.toThrow('Invalid replay id');
    expect(() => validateAnalysis({ ...analysis, review: { ...analysis.review, findings: 'invalid' } })).toThrow('Unsupported');
    await rm(join(run, 'analysis.json'));
    await writeFile(join(root, 'secret.json'), JSON.stringify(analysis));
    await symlink(join(root, 'secret.json'), join(run, 'analysis.json'));
    await expect(instance.call('analysis', { id: match.id })).rejects.toThrow('outside');
  });
  it('persists bookmarks and notes across a plugin reload and bounds annotations', async () => {
    const { root } = await project(); const db = new DatabaseSync(':memory:'); databases.push(db);
    const first = host(root, db); await plugin(first.api);
    const id = (await first.call('list')).matches[0].id;
    await first.call('annotate', { id, action: 'bookmark', t: 4, label: 'First expansion' });
    await first.call('annotate', { id, action: 'note', value: 'Review worker growth.' });
    await expect(first.call('annotate', { id, action: 'bookmark', t: 500, label: 'Outside replay' })).rejects.toThrow();
    await expect(first.call('annotate', { id: '../../.env', action: 'note', value: '' })).rejects.toThrow('Invalid replay id');
    const second = host(root, db); await plugin(second.api);
    const review = await second.call('review', { id });
    expect(review.note).toBe('Review worker growth.');
    expect(review.bookmarks[0]).toMatchObject({ t: 4, label: 'First expansion' });
    await second.call('annotate', { id, action: 'remove-bookmark', bookmarkId: review.bookmarks[0].id });
    expect((await second.call('review', { id })).bookmarks).toEqual([]);
  });
  it('serves only a registered replay and escapes metadata embedded in HTML', async () => {
    const { root, run } = await project(); const db = new DatabaseSync(':memory:'); databases.push(db);
    await writeFile(join(run, 'viewer', 'data.json'), JSON.stringify(fixture()));
    await writeFile(join(run, 'result.json'), JSON.stringify({ map: '</script><script>evil()</script>' }));
    const instance = host(root, db); await plugin(instance.api);
    const id = (await instance.call('list')).matches[0].id;
    const page = await instance.routes.get('/viewer')!({ query: { id } });
    expect(page.body).toContain('\\u003c/script>');
    expect(page.body).not.toContain('</script><script>evil()');
    expect((await instance.routes.get('/data')!({ query: { id } })).json.duration).toBe(10);
    expect((await instance.routes.get('/download')!({ query: { id: '../../.env' } })).status).toBe(404);
    await expect(instance.call('prepare', { id })).rejects.toThrow('disabled');
  });
});
describe('export validation', () => {
  it('rejects malformed terrain, reversed time, and unsupported schema', () => {
    expect(validateReplay(fixture()).frames).toHaveLength(1);
    expect(() => validateReplay({ ...fixture(), schemaVersion: 99 })).toThrow('Unsupported');
    const terrain = fixture(); terrain.terrain.data = []; expect(() => validateReplay(terrain)).toThrow('terrain');
    const time = fixture(); time.frames.push({ ...time.frames[0], t: -2 }); expect(() => validateReplay(time)).toThrow('frame');
  });
});
