import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const html = readFileSync('viewer.html', 'utf8');
function runViewerFunction(name: string, replay: unknown, time: number) {
  const source = html.split('\n').find(line => line.startsWith(`function ${name}(`));
  return new Function('document', 'replay', 't', '$', `${source};return ${name}();`)(
    document, replay, time, (id: string) => document.getElementById(id));
}

describe('recorded squad playback', () => {
  it('updates ownership when a scout rejoins the army', () => {
    const replay = { squadRosters: [
      { t: 1, role: 'scout', tags: ['9'] },
      { t: 5, role: 'scout', tags: [] },
      { t: 5, role: 'main', tags: ['9'] }
    ] };
    expect(runViewerFunction('activeSquadTags', replay, 2).get('9')).toBe('scout');
    expect(runViewerFunction('activeSquadTags', replay, 6).get('9')).toBe('main');
  });
  it('renders orders safely and handles older replays', () => {
    document.body.innerHTML = '<section id="squadCard"><div id="squadRows"></div></section>';
    // The actual viewer uses this simple formatter; keep it in the function scope.
    const source = html.split('\n').find(line => line.startsWith('function renderSquads('));
    const render = new Function('document', 'replay', 't', '$', 'name', 'clock', `${source};renderSquads();`);
    const call = (replay: unknown, t = 3) => render(document, replay, t,
      (id: string) => document.getElementById(id), (s: string) => s, (n: number) => String(n));
    call({ squadRosters: [{ t: 1, role: 'harass', tags: ['1', '2'] }],
      squadOrders: [{ t: 2, role: 'harass', tags: ['1','2'], mode: 'kite', focus: 'workers', goal: '<img src=x onerror=alert(1)>' }] });
    expect(document.getElementById('squadRows')?.textContent).toContain('harass · 2 units · kite');
    expect(document.querySelector('img')).toBeNull();
    call({});
    expect((document.getElementById('squadCard') as HTMLElement).hidden).toBe(true);
  });
});
