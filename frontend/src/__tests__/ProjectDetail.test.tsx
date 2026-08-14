import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import ProjectDetail from '../components/ProjectDetail';
import * as api from '../api';
import type { ProjectView } from '../types';

function project(overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    id: 'p1', title: 'Willows', created_at: '2026-08-14T10:00:00+00:00',
    status: 'STYLE_SET', step_state: 'IDLE', current_step: 'CHARACTERS',
    display_status: 'In progress', needs_attention: false, is_interrupted: false,
    completed_steps: 1, style_text: 'Warm watercolour', book_excerpt: 'Once…',
    failure: null, characters: [], chapters: [], ...overrides,
  };
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
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  const running = project({ step_state: 'RUNNING' });
  const spy = vi.spyOn(api, 'runStep').mockResolvedValue({ ok: true, project: running });

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(spy).toHaveBeenCalledWith('p1', 'CHARACTERS', undefined);
  expect(await screen.findByText(/generating the character list/i)).toBeInTheDocument();
});

test('a 409 renders current truth rather than a pipeline failure', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  const truth = project({ status: 'CHARACTERS_GENERATED', current_step: 'PORTRAITS',
                          completed_steps: 2 });
  vi.spyOn(api, 'runStep').mockResolvedValue({ ok: false, conflict: true, project: truth });

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(await screen.findByRole('button', { name: /generate portraits/i })).toBeInTheDocument();
  expect(screen.queryByText(/failed/i)).not.toBeInTheDocument();
});

test('a transport failure shows a banner and never invents FAILED', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  vi.spyOn(api, 'runStep').mockRejectedValue(new Error('Connection reset'));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Connection reset');
  // The pipeline state is untouched: still Ready for Characters.
  expect(screen.getByRole('button', { name: /generate characters/i })).toBeInTheDocument();
  expect(screen.queryByText(/retry characters/i)).not.toBeInTheDocument();
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
