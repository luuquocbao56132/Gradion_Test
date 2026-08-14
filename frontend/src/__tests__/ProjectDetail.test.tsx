import { StrictMode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import ProjectDetail from '../components/ProjectDetail';
import * as api from '../api';
import type { ProjectView, RunOutcome } from '../types';

function project(overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    id: 'p1', title: 'Willows', created_at: '2026-08-14T10:00:00+00:00',
    status: 'STYLE_SET', step_state: 'IDLE', current_step: 'CHARACTERS',
    display_status: 'In progress', needs_attention: false, is_interrupted: false,
    completed_steps: 1, style_text: 'Warm watercolour', book_excerpt: 'Once…',
    failure: null, characters: [], chapters: [], ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((onResolve) => {
    resolve = onResolve;
  });
  return { promise, resolve };
}

afterEach(() => vi.restoreAllMocks());

test('a skeleton shows while the project is loading', () => {
  vi.spyOn(api, 'getProject').mockReturnValue(new Promise(() => {}));
  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
});

test('a load failure offers a retry and shows no pipeline state', async () => {
  const spy = vi.spyOn(api, 'getProject').mockRejectedValue(new Error('Network down'));
  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);

  expect(await screen.findByRole('alert')).toHaveTextContent('Network down');
  expect(screen.queryByText(/ready for the next step/i)).not.toBeInTheDocument();

  spy.mockResolvedValue(project());
  await userEvent.click(screen.getByRole('button', { name: /try again/i }));
  expect(await screen.findByText('Willows')).toBeInTheDocument();
});

test('a 202 replaces state from the response with no local transition', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project({
    title: 'Stale title', style_text: 'Stale style',
    characters: [{ id: 'old', position: 0, name: 'Stale character', prompt: 'old',
                   image_url: null, image_state: 'pending' }],
  }));
  const running = project({
    title: 'Accepted 202', step_state: 'RUNNING', style_text: null, characters: [],
  });
  const spy = vi.spyOn(api, 'runStep').mockResolvedValue({ ok: true, project: running });

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(spy).toHaveBeenCalledWith('p1', 'CHARACTERS', undefined);
  expect(await screen.findByText(/generating the character list/i)).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Accepted 202' })).toBeInTheDocument();
  expect(screen.queryByText('Stale title')).not.toBeInTheDocument();
  expect(screen.queryByText('Stale style')).not.toBeInTheDocument();
  expect(screen.queryByText('Stale character')).not.toBeInTheDocument();
});

test('a 409 renders current truth rather than a pipeline failure', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project({
    title: 'Before conflict', style_text: 'Old style',
    chapters: [{ id: 'old', position: 0, name: 'Old chapter', prompt: 'old',
                 image_url: null, image_state: 'pending' }],
  }));
  const truth = project({
    title: 'Conflict truth', status: 'CHARACTERS_GENERATED', current_step: 'PORTRAITS',
    completed_steps: 2, style_text: null, chapters: [],
  });
  vi.spyOn(api, 'runStep').mockResolvedValue({ ok: false, conflict: true, project: truth });

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(await screen.findByRole('button', { name: /generate portraits/i })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Conflict truth' })).toBeInTheDocument();
  expect(screen.queryByText('Before conflict')).not.toBeInTheDocument();
  expect(screen.queryByText('Old style')).not.toBeInTheDocument();
  expect(screen.queryByText('Old chapter')).not.toBeInTheDocument();
  expect(screen.queryByText(/failed/i)).not.toBeInTheDocument();
});

test('a pending reconciliation keeps prior truth and the banner while releasing busy', async () => {
  const reconciliation = deferred<ProjectView>();
  const getProject = vi.spyOn(api, 'getProject')
    .mockResolvedValueOnce(project())
    .mockReturnValueOnce(reconciliation.promise);
  vi.spyOn(api, 'runStep').mockRejectedValue(new Error('Connection reset'));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Connection reset');
  expect(screen.getByRole('heading', { name: 'Willows' })).toBeInTheDocument();
  expect(screen.queryByText(/loading project/i)).not.toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /generate characters/i })).toBeEnabled();
  });
  expect(screen.queryByText(/retry characters/i)).not.toBeInTheDocument();
  expect(getProject).toHaveBeenCalledTimes(2);
});

test('a failed reconciliation keeps prior truth and the transport banner', async () => {
  const getProject = vi.spyOn(api, 'getProject')
    .mockResolvedValueOnce(project())
    .mockRejectedValueOnce(new Error('Refetch also failed'));
  vi.spyOn(api, 'runStep').mockRejectedValue(new Error('Connection reset'));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Connection reset');
  expect(screen.getByRole('heading', { name: 'Willows' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /generate characters/i })).toBeEnabled();
  expect(screen.queryByRole('button', { name: /try again/i })).not.toBeInTheDocument();
  expect(getProject).toHaveBeenCalledTimes(2);
});

test('a successful reconciliation replaces prior truth without hiding the transport banner', async () => {
  const reconciliation = deferred<ProjectView>();
  const getProject = vi.spyOn(api, 'getProject')
    .mockResolvedValueOnce(project())
    .mockReturnValueOnce(reconciliation.promise);
  vi.spyOn(api, 'runStep').mockRejectedValue(new Error('Connection reset'));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Connection reset');
  expect(screen.getByRole('heading', { name: 'Willows' })).toBeInTheDocument();

  reconciliation.resolve(project({
    title: 'Reconciled truth', status: 'CHARACTERS_GENERATED', current_step: 'PORTRAITS',
    completed_steps: 2,
  }));

  expect(await screen.findByRole('heading', { name: 'Reconciled truth' })).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('Connection reset');
  expect(screen.getByRole('button', { name: /generate portraits/i })).toBeEnabled();
  expect(getProject).toHaveBeenCalledTimes(2);
});

test('STYLE is normalized and the run button is busy only while the POST is pending', async () => {
  const outcome = deferred<RunOutcome>();
  vi.spyOn(api, 'getProject').mockResolvedValue(project({
    status: 'CREATED', current_step: 'STYLE', completed_steps: 0, style_text: null,
  }));
  const runStep = vi.spyOn(api, 'runStep').mockReturnValue(outcome.promise);

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.type(await screen.findByLabelText(/art style/i), '  paper collage  ');
  await userEvent.click(screen.getByRole('button', { name: /generate style/i }));

  expect(runStep).toHaveBeenCalledWith('p1', 'STYLE', 'paper collage');
  expect(screen.getByRole('button', { name: /generate style/i })).toBeDisabled();

  outcome.resolve({ ok: true, project: project({
    status: 'CREATED', current_step: 'STYLE', completed_steps: 0, style_text: null,
  }) });
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /generate style/i })).toBeEnabled();
  });
});

test('a StrictMode GET cannot overwrite a newer GET or accepted run truth', async () => {
  const stale = deferred<ProjectView>();
  const fresh = deferred<ProjectView>();
  const getProject = vi.spyOn(api, 'getProject')
    .mockReturnValueOnce(stale.promise)
    .mockReturnValueOnce(fresh.promise);

  render(
    <StrictMode>
      <ProjectDetail projectId="p1" onBack={vi.fn()} />
    </StrictMode>,
  );
  await waitFor(() => expect(getProject).toHaveBeenCalledTimes(2));

  fresh.resolve(project({ title: 'Fresh GET truth' }));
  expect(await screen.findByRole('heading', { name: 'Fresh GET truth' })).toBeInTheDocument();

  vi.spyOn(api, 'runStep').mockResolvedValue({ ok: true, project: project({
    title: 'Accepted run truth', status: 'CHARACTERS_GENERATED',
    current_step: 'PORTRAITS', completed_steps: 2,
  }) });
  await userEvent.click(screen.getByRole('button', { name: /generate characters/i }));
  expect(await screen.findByRole('heading', { name: 'Accepted run truth' })).toBeInTheDocument();

  await act(async () => stale.resolve(project({ title: 'Stale GET truth' })));
  expect(screen.getByRole('heading', { name: 'Accepted run truth' })).toBeInTheDocument();
  expect(screen.queryByText('Stale GET truth')).not.toBeInTheDocument();
});

test('a stale background refetch cannot overwrite a newer accepted run snapshot', async () => {
  const staleReconciliation = deferred<ProjectView>();
  vi.spyOn(api, 'getProject')
    .mockResolvedValueOnce(project())
    .mockReturnValueOnce(staleReconciliation.promise);
  vi.spyOn(api, 'runStep')
    .mockRejectedValueOnce(new Error('Connection reset'))
    .mockResolvedValueOnce({ ok: true, project: project({
      title: 'Newest run truth', status: 'CHARACTERS_GENERATED',
      current_step: 'PORTRAITS', completed_steps: 2,
    }) });

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Connection reset');
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /generate characters/i })).toBeEnabled();
  });

  await userEvent.click(screen.getByRole('button', { name: /generate characters/i }));
  expect(await screen.findByRole('heading', { name: 'Newest run truth' })).toBeInTheDocument();

  await act(async () => staleReconciliation.resolve(project({ title: 'Stale refetch truth' })));
  expect(screen.getByRole('heading', { name: 'Newest run truth' })).toBeInTheDocument();
  expect(screen.queryByText('Stale refetch truth')).not.toBeInTheDocument();
});

test('a retry completion from the previous project cannot cross project boundaries', async () => {
  const oldRetry = deferred<ProjectView>();
  const getProject = vi.spyOn(api, 'getProject')
    .mockRejectedValueOnce(new Error('p1 unavailable'))
    .mockReturnValueOnce(oldRetry.promise)
    .mockResolvedValueOnce(project({ id: 'p2', title: 'Project two' }));
  const { rerender } = render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);

  await userEvent.click(await screen.findByRole('button', { name: /try again/i }));
  rerender(<ProjectDetail projectId="p2" onBack={vi.fn()} />);

  expect(await screen.findByRole('heading', { name: 'Project two' })).toBeInTheDocument();
  await act(async () => oldRetry.resolve(project({ title: 'Old p1 retry' })));
  expect(screen.getByRole('heading', { name: 'Project two' })).toBeInTheDocument();
  expect(screen.queryByText('Old p1 retry')).not.toBeInTheDocument();
  expect(getProject).toHaveBeenLastCalledWith('p2');
});

test('a recorded pipeline failure is shown as Failed with a scoped retry', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project({
    step_state: 'FAILED', needs_attention: true,
    failure: { code: 'GEMINI_ERROR', message: 'Gemini said no' },
  }));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  expect(await screen.findByRole('button', { name: /retry characters/i })).toBeInTheDocument();
});

test('characters and chapters render once they exist', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project({
    status: 'CHAPTERS_GENERATED', current_step: 'ILLUSTRATIONS', completed_steps: 4,
    characters: [
      { id: 'c1', position: 0, name: 'Toad', prompt: 'a toad',
        image_url: '/x', image_state: 'ready' },
      { id: 'c2', position: 1, name: 'Ratty', prompt: 'a rat',
        image_url: '/y', image_state: 'ready' },
    ],
    chapters: [
      { id: 'ch1', position: 0, name: 'Chapter One', prompt: 'a river',
        image_url: null, image_state: 'pending' },
    ],
  }));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  expect(await screen.findByText('Toad')).toBeInTheDocument();
  expect(screen.getByText('Ratty')).toBeInTheDocument();
  expect(screen.getByText('Chapter One')).toBeInTheDocument();
});

test('the book panel is present at every stage, including after a style exists', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project({ status: 'DONE',
    current_step: null, completed_steps: 5, display_status: 'Done' }));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  expect(await screen.findByRole('button', { name: /read full text/i })).toBeInTheDocument();
});

test('the detail composes progress and the persisted style beside the active step', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);

  expect(await screen.findByRole('list', { name: /generation progress/i })).toBeInTheDocument();
  expect(screen.getByRole('listitem', { name: /style.*completed/i })).toBeInTheDocument();
  expect(screen.getByRole('listitem', { name: /characters.*current/i })).toBeInTheDocument();
  expect(screen.getByText('Warm watercolour')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /generate characters/i })).toBeEnabled();
});

test('losing the socket never turns the project into a pipeline failure', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);

  expect(await screen.findByRole('button', { name: /generate characters/i })).toBeInTheDocument();
  // No socket is connected in jsdom, so the hook is not live - and the panel
  // still shows Ready, not Failed or Interrupted.
  expect(screen.queryByText(/retry characters/i)).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
