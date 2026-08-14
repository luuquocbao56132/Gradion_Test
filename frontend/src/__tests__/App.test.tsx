import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import App from '../App';
import * as api from '../api';
import type { ProjectView, RunOutcome, SessionView } from '../types';

function project(id: string, title: string,
                 overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    id, title, created_at: '2026-08-14T10:00:00+00:00',
    status: 'STYLE_SET', step_state: 'IDLE', current_step: 'CHARACTERS',
    display_status: 'In progress', needs_attention: false, is_interrupted: false,
    completed_steps: 1, style_text: 'Warm watercolour', book_excerpt: 'Once…',
    failure: null, characters: [], chapters: [], ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((onResolve) => { resolve = onResolve; });
  return { promise, resolve };
}

afterEach(() => {
  vi.restoreAllMocks();
  window.location.hash = '#/';
});

test('the sign-in button is disabled while session creation is pending', async () => {
  vi.spyOn(api, 'getSession').mockResolvedValue(null);
  const pendingSession = new Promise<SessionView>(() => {});
  vi.spyOn(api, 'createSession').mockReturnValue(pendingSession);
  const user = userEvent.setup();

  render(<App />);

  await user.type(await screen.findByLabelText(/full name/i), 'Ada');
  await user.type(screen.getByLabelText(/email/i), 'ada@example.com');
  await user.click(screen.getByRole('button', { name: /continue/i }));

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /signing in|continue/i })).toBeDisabled();
  });
});

test('a rejected sign-in shows the error and releases the disabled state', async () => {
  vi.spyOn(api, 'getSession').mockResolvedValue(null);
  vi.spyOn(api, 'createSession').mockRejectedValue(new Error('Sign in failed'));
  const user = userEvent.setup();

  render(<App />);

  await user.type(await screen.findByLabelText(/full name/i), 'Ada');
  await user.type(screen.getByLabelText(/email/i), 'ada@example.com');
  await user.click(screen.getByRole('button', { name: /continue/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Sign in failed');
  expect(screen.getByRole('button', { name: /continue/i })).toBeEnabled();
});

test('a successful bootstrap retry returns to signed-out without the stale error', async () => {
  const getSession = vi.spyOn(api, 'getSession')
    .mockRejectedValueOnce(new Error('Network down'))
    .mockResolvedValueOnce(null);

  render(<App />);

  expect(await screen.findByRole('alert')).toHaveTextContent('Network down');
  await userEvent.click(screen.getByRole('button', { name: /try again/i }));

  expect(await screen.findByLabelText(/full name/i)).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(getSession).toHaveBeenCalledTimes(2);
});

test('the detail hash route loads the project and its back action returns to the list', async () => {
  window.location.hash = '#/projects/p1';
  vi.spyOn(api, 'getSession').mockResolvedValue({
    user_id: 'u1', name: 'Ada Lovelace', email: 'ada@example.com',
  });
  vi.spyOn(api, 'getProject').mockResolvedValue(project('p1', 'Project one'));
  const listProjects = vi.spyOn(api, 'listProjects').mockResolvedValue([]);

  render(<App />);

  expect(await screen.findByRole('heading', { name: 'Project one' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /back to projects/i }));
  expect(await screen.findByRole('heading', { name: /your projects/i })).toBeInTheDocument();
  expect(listProjects).toHaveBeenCalledOnce();
});

test('detail routes remount so local input and a late run cannot leak between projects', async () => {
  window.location.hash = '#/projects/p1';
  vi.spyOn(api, 'getSession').mockResolvedValue({
    user_id: 'u1', name: 'Ada Lovelace', email: 'ada@example.com',
  });
  vi.spyOn(api, 'getProject').mockImplementation(async (id) => (
    id === 'p1'
      ? project('p1', 'Project one', {
          status: 'CREATED', current_step: 'STYLE', completed_steps: 0, style_text: null,
        })
      : project('p2', 'Project two', {
          status: 'CREATED', current_step: 'STYLE', completed_steps: 0, style_text: null,
        })
  ));
  const lateRun = deferred<RunOutcome>();
  vi.spyOn(api, 'runStep').mockReturnValue(lateRun.promise);

  render(<App />);
  await userEvent.type(await screen.findByLabelText(/art style/i), 'p1 private draft');
  await userEvent.click(screen.getByRole('button', { name: /generate style/i }));

  await act(async () => {
    window.location.hash = '#/projects/p2';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(await screen.findByRole('heading', { name: 'Project two' })).toBeInTheDocument();
  expect(screen.getByLabelText(/art style/i)).toHaveValue('');

  await act(async () => {
    lateRun.resolve({ ok: true, project: project('p1', 'Late project one', {
      status: 'CREATED', current_step: 'STYLE', completed_steps: 0, style_text: null,
    }) });
  });
  await waitFor(() => {
    expect(screen.getByRole('heading', { name: 'Project two' })).toBeInTheDocument();
  });
  expect(screen.queryByText('Late project one')).not.toBeInTheDocument();
});
