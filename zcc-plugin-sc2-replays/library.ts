import { createHash } from 'node:crypto';
import { lstat, readdir, readFile, realpath, stat } from 'node:fs/promises';
import { isAbsolute, join, relative, sep } from 'node:path';

export type Match = {
  id: string; folder: string; map: string; result: string; enemy: string;
  difficulty: string; opening: string; createdAt: number; duration: number;
  ready: boolean; memorySaved: boolean; offline: boolean; replayFile: string;
  analysisStatus?: string;
};
export type PostGameReport = {
  schema_version: number; status: string;
  review: { summary: string; strengths: string[]; missing_evidence: string[]; findings: {
    category: string; priority: string; hypothesis: string; evidence_ids: string[];
    proposed_change: string; validation: string; uncertainty: string;
  }[] };
  evidence: { facts: Record<string, unknown>; warnings: string[]; general_patterns: {
    matches: number; conditions: number; maps: string[]; races: string[];
  } };
};

export function validateAnalysis(data: any): PostGameReport {
  const text = (value: unknown) => typeof value === 'string' && value.length <= 3000;
  const texts = (value: unknown) => Array.isArray(value) && value.length <= 20 && value.every(text);
  const review = data?.review, evidence = data?.evidence, sample = evidence?.general_patterns;
  if (data?.schema_version !== 1 || !['pending', 'offline', 'complete', 'failed'].includes(data.status) ||
      !review || !text(review.summary) || !texts(review.strengths) || !texts(review.missing_evidence) ||
      !Array.isArray(review.findings) || review.findings.length > 6 || !review.findings.every((f: any) =>
        f && ['category', 'priority', 'hypothesis', 'proposed_change', 'validation', 'uncertainty'].every(key => text(f[key])) && texts(f.evidence_ids)) ||
      !evidence || !texts(evidence.warnings) || !evidence.facts || typeof evidence.facts !== 'object' ||
      Array.isArray(evidence.facts) || !sample || ![sample.matches, sample.conditions].every(n =>
        typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 50) ||
      !Array.isArray(sample.maps) || !sample.maps.every(text) || !Array.isArray(sample.races) || !sample.races.every(text)) {
    throw new Error('Unsupported or incomplete post-game analysis');
  }
  return data;
}
export type StoredReview = { favorite: boolean; note: string; bookmarks: Bookmark[] };
export type Bookmark = { id: string; t: number; label: string };
export type IndexedMatch = Match & { directory: string; project: string };

export async function confined(root: string, filename: string): Promise<string> {
  const base = await realpath(root);
  const target = await realpath(filename);
  const rel = relative(base, target);
  if (rel === '..' || rel.startsWith('..' + sep) || isAbsolute(rel)) {
    throw new Error('Replay file is outside the configured runs directory');
  }
  if (!(await stat(target)).isFile()) throw new Error('Expected a replay file');
  return target;
}

export async function readBounded(root: string, filename: string, maxBytes: number) {
  const target = await confined(root, filename);
  if ((await stat(target)).size > maxBytes) throw new Error('Replay file is too large');
  const bytes = await readFile(target);
  if (bytes.length > maxBytes) throw new Error('Replay file is too large');
  return bytes;
}

export function validateReplay(data: any) {
  const finite = (n: unknown) => typeof n === 'number' && Number.isFinite(n);
  if (!data || !finite(data.duration) || data.duration <= 0 || data.duration > 86400 ||
      !Array.isArray(data.frames) || !data.frames.length || data.frames.length > 50000 ||
      (data.schemaVersion !== undefined && data.schemaVersion !== 1)) {
    throw new Error('Unsupported or incomplete replay export');
  }
  const b = data.bounds;
  if (!b || !['minX', 'minY', 'maxX', 'maxY'].every(k => finite(b[k])) ||
      b.maxX <= b.minX || b.maxY <= b.minY) throw new Error('Invalid map bounds');
  for (const [key, bits] of [['terrain', 8], ['pathing', 0]] as const) {
    const g = data[key];
    if (!g || !Number.isInteger(g.width) || !Number.isInteger(g.height) ||
        g.width <= 0 || g.height <= 0 || g.width > 1024 || g.height > 1024 ||
        ![1, 8].includes(g.bits) || (bits && g.bits !== bits) || !Array.isArray(g.data) ||
        g.data.length !== Math.ceil(g.width * g.height * g.bits / 8) ||
        !g.data.every((n: number) => Number.isInteger(n) && n >= 0 && n <= 255)) {
      throw new Error('Invalid replay terrain');
    }
  }
  if (data.terrain.width !== data.pathing.width || data.terrain.height !== data.pathing.height ||
      b.minX < 0 || b.minY < 0 || b.maxX > data.terrain.width || b.maxY > data.terrain.height) {
    throw new Error('Map bounds do not match terrain');
  }
  let last = -1;
  for (const f of data.frames) {
    if (!finite(f.t) || f.t < 0 || f.t < last || f.t > data.duration + 2 ||
        !Array.isArray(f.units) || f.units.length > 10000 ||
        !['workers', 'army', 'minerals', 'gas', 'supply', 'cap'].every(k => finite(f[k]))) {
      throw new Error('Invalid replay frame');
    }
    for (const u of f.units) {
      if (!Array.isArray(u) || u.length !== 9 || typeof u[0] !== 'string' ||
          !u.slice(1, 8).every(finite) || u[5] < 0 || u[5] > 50 ||
          u[6] < 0 || u[6] > 1 || u[7] < 0 || u[7] > 1 || typeof u[8] !== 'boolean') {
        throw new Error('Invalid unit in replay');
      }
    }
    last = f.t;
  }
  if (!data.types || typeof data.types !== 'object' || Array.isArray(data.types) ||
      !Object.values(data.types).every((t: any) => t && typeof t.name === 'string' &&
        typeof t.structure === 'boolean')) throw new Error('Invalid replay unit types');
  data.plans = Array.isArray(data.plans) ? data.plans.filter((p: any) =>
    p && finite(p.t) && typeof p.summary === 'string').sort((a: any, b: any) => a.t - b.t) : [];
  data.decisions = Array.isArray(data.decisions) ? data.decisions.filter((p: any) =>
    p && finite(p.t) && Array.isArray(p.applied) && p.applied.length === 2 &&
    p.applied.every((s: unknown) => typeof s === 'string')).sort((a: any, b: any) => a.t - b.t) : [];
  return data;
}

async function optionalJson(root: string, file: string, limit = 65536): Promise<any> {
  try { const data = JSON.parse((await readBounded(root, file, limit)).toString('utf8'));
    return data && typeof data === 'object' && !Array.isArray(data) ? data : {}; }
  catch { return {}; }
}

export async function scanMatches(project: string): Promise<IndexedMatch[]> {
  const runs = join(project, 'runs');
  try { if ((await lstat(runs)).isSymbolicLink()) throw new Error('Runs directory must not be a symlink'); }
  catch (e: any) { if (e.code === 'ENOENT') return []; throw e; }
  const root = await realpath(runs);
  const matches: IndexedMatch[] = [];
  let visited = 0;
  async function visit(directory: string, depth: number) {
    if (++visited > 2500 || depth > 5) return;
    const entries = await readdir(directory, { withFileTypes: true });
    const replay = entries.find(e => e.isFile() && e.name.endsWith('.SC2Replay'));
    if (replay) {
      const result = await optionalJson(root, join(directory, 'result.json'));
      const analysis = await optionalJson(root, join(directory, 'analysis.json'), 1024 * 1024);
      let metadata = await optionalJson(root, join(directory, 'viewer', 'metadata.json'));
      // The first viewer predates the compact index. Read its existing export once per scan.
      if (!metadata.duration) metadata = await optionalJson(root, join(directory, 'viewer', 'data.json'), 128 * 1024 * 1024);
      const folder = relative(root, directory);
      const id = createHash('sha256').update(project + '\0' + folder).digest('hex').slice(0, 24);
      const ready = entries.some(e => e.isDirectory() && e.name === 'viewer') &&
        await confined(root, join(directory, 'viewer', 'data.json')).then(() => true, () => false);
      matches.push({ id, folder, project, directory, replayFile: replay.name,
        map: String(metadata.map ?? result.map ?? 'Unknown map'),
        result: String(result.result ?? metadata.result ?? 'Unknown'),
        enemy: String(result.enemy ?? metadata.enemy ?? 'unknown'),
        difficulty: String(result.difficulty ?? metadata.difficulty ?? 'Unknown'),
        opening: String(result.opening ?? metadata.opening ?? 'Unknown'),
        duration: Number.isFinite(Number(metadata.duration)) && Number(metadata.duration) > 0 ? Number(metadata.duration) : 0, memorySaved: Boolean(result.memory_saved ?? metadata.memorySaved),
        offline: Boolean(result.offline ?? metadata.offline), ready,
        analysisStatus: ['pending', 'offline', 'complete', 'failed'].includes(analysis.status) ? analysis.status : undefined,
        createdAt: (await stat(join(directory, replay.name))).mtimeMs });
      return;
    }
    for (const entry of entries) {
      if (entry.isDirectory() && !entry.name.startsWith('.') && entry.name !== 'viewer') {
        await visit(join(directory, entry.name), depth + 1);
      }
    }
  }
  await visit(root, 0);
  return matches.sort((a, b) => b.createdAt - a.createdAt);
}

export const emptyReview = (): StoredReview => ({ favorite: false, note: '', bookmarks: [] });
