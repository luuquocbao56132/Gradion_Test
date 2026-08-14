import { afterEach, describe, expect, test, vi } from 'vitest';
import * as api from '../api';
import type { ProjectView } from '../types';

const project = { id: 'p1', title: 'W', status: 'CREATED' } as unknown as ProjectView;

function mockFetch(status: number, body: unknown) {
  const spy = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
  vi.stubGlobal('fetch', spy);
  return spy;
}

afterEach(() => vi.unstubAllGlobals());

describe('runStep', () => {
  test('202 is an accepted run carrying the new state', async () => {
    mockFetch(202, { project });
    const outcome = await api.runStep('p1', 'STYLE');
    expect(outcome).toEqual({ ok: true, project });
  });

  test('409 is a conflict carrying the truth, not an error to throw', async () => {
    mockFetch(409, { error: { code: 'CONFLICT', message: 'busy' }, project });
    const outcome = await api.runStep('p1', 'STYLE');
    expect(outcome).toEqual({ ok: false, conflict: true, project });
  });

  test('any other failure throws so the caller shows a transient banner', async () => {
    mockFetch(500, { error: { code: 'INTERNAL', message: 'boom' } });
    await expect(api.runStep('p1', 'STYLE')).rejects.toThrow('boom');
  });

  test('the optional style is sent only when provided', async () => {
    const spy = mockFetch(202, { project });
    await api.runStep('p1', 'STYLE', 'watercolour');
    expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({
      step: 'STYLE', style: 'watercolour',
    });
    await api.runStep('p1', 'CHARACTERS');
    expect(JSON.parse(spy.mock.calls[1][1].body)).toEqual({ step: 'CHARACTERS' });
  });
});

describe('getSession', () => {
  test('401 means signed out, not an error', async () => {
    mockFetch(401, {});
    await expect(api.getSession()).resolves.toBeNull();
  });

  test('200 returns the session', async () => {
    mockFetch(200, { user_id: 'u1', name: 'Ada', email: 'a@b.co' });
    await expect(api.getSession()).resolves.toEqual({
      user_id: 'u1', name: 'Ada', email: 'a@b.co',
    });
  });
});
