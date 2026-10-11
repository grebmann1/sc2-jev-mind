// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { loadPluginApp, renderSlot } from '@zana-ai/zcc-plugin-sdk/testing/app';
import { fireEvent, screen, waitFor } from '@testing-library/react';

const app = await loadPluginApp(() => import('./app.tsx'), 'sc2-replays');
const match = { id: '123', folder: 'first/match', map: 'AcropolisLE', result: 'Victory', enemy: 'Zerg',
  difficulty: 'Medium', opening: 'balanced', createdAt: 1791749985412, duration: 658.35,
  ready: true, memorySaved: true, offline: false, replayFile: 'replay.SC2Replay', favorite: false, note: '', bookmarks: [] };

describe('replay studio', () => {
  it('shows evidence, uncertainty and controlled validation for post-game findings', async () => {
    const slot = renderSlot(app.navPanels[0]!, { pluginId: 'sc2-replays', subPath: '' }, {
      rpc: { list: () => ({ project: 'SC2 test', matches: [{ ...match, analysisStatus: 'complete' }], job: null }),
        analysis: () => ({ status: 'complete', review: { summary: 'One win is not general evidence.', strengths: [], missing_evidence: [],
          findings: [{ priority: 'high', category: 'supply', hypothesis: 'Forecast production-driven supply needs.',
            proposed_change: 'Account for pending depots.', validation: 'Test across held-out seeds and maps.',
            uncertainty: 'No causal effect established.', evidence_ids: ['metrics.observed_supply_blocked_s'] }] },
          evidence: { facts: { 'metrics.observed_supply_blocked_s': 30 }, warnings: [],
            general_patterns: { matches: 1, conditions: 1, maps: ['AcropolisLE'], races: ['Zerg'] } } }) }
    });
    await slot.findByText('OpenAI review ready');
    fireEvent.click(screen.getByText('OpenAI review ready').parentElement!);
    await slot.findByText('Forecast production-driven supply needs.');
    expect(screen.getByText(/No causal effect established/)).toBeDefined();
    expect(screen.getByText(/held-out seeds and maps/)).toBeDefined();
    expect(screen.getByText(/Suggestions do not change the bot automatically/)).toBeDefined();
    slot.lifecycle.unmount();
  });
  it('shows a playable match and lets search filter the library', async () => {
    const slot = renderSlot(app.navPanels[0]!, { pluginId: 'sc2-replays', subPath: '' }, {
      rpc: { list: () => ({ project: 'SC2 test', matches: [match], job: null }) }
    });
    await slot.findByText('AcropolisLE');
    expect(document.querySelector('iframe')?.getAttribute('src')).toContain('/viewer?id=123');
    fireEvent.change(screen.getByLabelText('Search replays'), { target: { value: 'missing-map' } });
    await slot.findByText('No replays match this search.');
    slot.lifecycle.unmount();
  });
  it('saves review notes through the plugin without touching bot memory', async () => {
    let saved: any;
    const slot = renderSlot(app.navPanels[0]!, { pluginId: 'sc2-replays', subPath: '' }, {
      rpc: { list: () => ({ project: 'SC2 test', matches: [match], job: null }),
        annotate: input => { saved = input; return { favorite: false, note: 'Watch expansion timing.', bookmarks: [] }; } }
    });
    await slot.findByText('AcropolisLE');
    fireEvent.change(screen.getByLabelText('Your match notes'), { target: { value: 'Watch expansion timing.' } });
    fireEvent.click(screen.getByText('Save notes'));
    await waitFor(() => expect(saved).toEqual({ id: '123', action: 'note', value: 'Watch expansion timing.' }));
    await slot.findByText('Notes saved');
    slot.lifecycle.unmount();
  });
  it('shows a useful empty state', async () => {
    const slot = renderSlot(app.navPanels[0]!, { pluginId: 'sc2-replays', subPath: '' }, {
      rpc: { list: () => ({ project: 'SC2 test', matches: [], job: null }) }
    });
    await slot.findByText('Your replay studio is ready.');
    slot.lifecycle.unmount();
  });
});
