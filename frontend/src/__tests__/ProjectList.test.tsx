import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import ProjectList from '../components/ProjectList';
import ProjectRow from '../components/ProjectRow';
import * as api from '../api';
import type { ProjectListItem } from '../types';

function item(overrides: Partial<ProjectListItem> = {}): ProjectListItem {
  return {
    id: 'p1', title: 'Willows', created_at: '2026-08-14T10:00:00+00:00',
    status: 'CHARACTERS_GENERATED', current_step: 'PORTRAITS',
    display_status: 'In progress', needs_attention: false, is_interrupted: false,
    completed_steps: 2, ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

test('a loading state shows while the fetch is in flight', () => {
  vi.spyOn(api, 'listProjects').mockReturnValue(new Promise(() => {}));
  render(<ProjectList onOpen={vi.fn()} onNew={vi.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
});

test('the empty state appears when there are no projects', async () => {
  vi.spyOn(api, 'listProjects').mockResolvedValue([]);
  render(<ProjectList onOpen={vi.fn()} onNew={vi.fn()} />);
  expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument();
});

test('a fetch failure offers a retry and does not invent project state', async () => {
  const spy = vi.spyOn(api, 'listProjects').mockRejectedValue(new Error('Network down'));
  render(<ProjectList onOpen={vi.fn()} onNew={vi.fn()} />);

  expect(await screen.findByRole('alert')).toHaveTextContent('Network down');
  spy.mockResolvedValue([item()]);
  await userEvent.click(screen.getByRole('button', { name: /try again/i }));
  expect(await screen.findByText('Willows')).toBeInTheDocument();
});

test('projects render with title, date and pill', async () => {
  vi.spyOn(api, 'listProjects').mockResolvedValue([item()]);
  render(<ProjectList onOpen={vi.fn()} onNew={vi.fn()} />);

  expect(await screen.findByText('Willows')).toBeInTheDocument();
  expect(screen.getByText('In progress')).toBeInTheDocument();
});

test('the five-step indicator fills one segment per completed step', () => {
  const { container } = render(<ProjectRow project={item({ completed_steps: 2 })}
                                          onOpen={vi.fn()} />);
  const segments = container.querySelectorAll('.progress-mini .seg');
  expect(segments).toHaveLength(5);
  expect(container.querySelectorAll('.progress-mini .seg.on')).toHaveLength(2);
});

test('a finished project fills all five segments and reads Done', () => {
  const { container } = render(
    <ProjectRow project={item({ status: 'DONE', display_status: 'Done',
                                current_step: null, completed_steps: 5 })}
                onOpen={vi.fn()} />);
  expect(container.querySelectorAll('.progress-mini .seg.on')).toHaveLength(5);
  expect(screen.getByText('Done')).toBeInTheDocument();
});

test('needs_attention shows a warning beside the pill, never instead of it', () => {
  render(<ProjectRow project={item({ needs_attention: true, display_status: 'In progress' })}
                     onOpen={vi.fn()} />);
  expect(screen.getByText('In progress')).toBeInTheDocument();
  expect(screen.getByText(/needs attention/i)).toBeInTheDocument();
});

test('a row opens on click and on Enter', async () => {
  const onOpen = vi.fn();
  render(<ProjectRow project={item()} onOpen={onOpen} />);
  const row = screen.getByRole('button', { name: /willows/i });
  await userEvent.click(row);
  row.focus();
  await userEvent.keyboard('{Enter}');
  expect(onOpen).toHaveBeenCalledTimes(2);
  expect(onOpen).toHaveBeenCalledWith('p1');
});
