import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import EntityCard from '../components/EntityCard';
import ProjectRow from '../components/ProjectRow';
import SignIn from '../components/SignIn';
import StepPanel from '../components/StepPanel';
import type { EntityView, ProjectListItem, ProjectView } from '../types';

const listItem: ProjectListItem = {
  id: 'p1', title: 'Willows', created_at: '2026-08-14T10:00:00+00:00',
  status: 'STYLE_SET', current_step: 'CHARACTERS', display_status: 'In progress',
  needs_attention: false, is_interrupted: false, completed_steps: 1,
};

const runningProject: ProjectView = {
  ...listItem, step_state: 'RUNNING', style_text: null, book_excerpt: 'Once…',
  failure: null, characters: [], chapters: [],
};

test('every interactive element in the sign-in form is reachable by keyboard', async () => {
  render(<SignIn onSubmit={vi.fn()} error={null} busy={false} />);
  await userEvent.tab();
  expect(screen.getByLabelText(/full name/i)).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByLabelText(/email/i)).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByRole('button', { name: /continue/i })).toHaveFocus();
});

test('a project row is focusable and activates from the keyboard', async () => {
  const onOpen = vi.fn();
  render(<ProjectRow project={listItem} onOpen={onOpen} />);
  await userEvent.tab();
  expect(screen.getByRole('button', { name: /willows/i })).toHaveFocus();
  await userEvent.keyboard('{Enter}');
  expect(onOpen).toHaveBeenCalledWith('p1');
});

test('the running status is announced to assistive technology', () => {
  render(<StepPanel project={runningProject} onRun={vi.fn()} busy={false} />);
  const status = screen.getByRole('status');
  expect(status).toHaveAttribute('aria-live', 'polite');
  expect(status).toHaveTextContent(/generating the character list/i);
});

test('the spinner is decorative; the caption carries the meaning', () => {
  const { container } = render(
    <StepPanel project={runningProject} onRun={vi.fn()} busy={false} />);
  expect(container.querySelector('.spinner')).toHaveAttribute('aria-hidden', 'true');
});

test('the progress indicator has a text equivalent', () => {
  render(<ProjectRow project={listItem} onOpen={vi.fn()} />);
  expect(screen.getByLabelText('1 of 5 steps complete')).toBeInTheDocument();
});

test('images carry descriptive alt text', () => {
  const item: EntityView = { id: 'c1', position: 0, name: 'Toad', prompt: 'a toad',
                             image_url: '/x', image_state: 'ready' };
  render(<EntityCard kind="character" item={item} />);
  expect(screen.getByAltText('Portrait of Toad')).toBeInTheDocument();
});

test('the art slot keeps its box before the image lands, so nothing reflows', () => {
  const pending: EntityView = { id: 'c1', position: 0, name: 'Toad', prompt: 'a toad',
                                image_url: null, image_state: 'pending' };
  const { container } = render(<EntityCard kind="character" item={pending} />);
  expect(container.querySelector('.art')).not.toBeNull();
});
