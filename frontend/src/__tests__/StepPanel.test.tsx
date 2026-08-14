import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import StepPanel from '../components/StepPanel';
import Stepper from '../components/Stepper';
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

test('Ready names the next step and offers its action', () => {
  render(<StepPanel project={project()} onRun={vi.fn()} busy={false} />);
  expect(screen.getByText(/ready for the next step/i)).toHaveTextContent('Characters');
  expect(screen.getByRole('button', { name: /generate characters/i })).toBeEnabled();
});

test('step 1 offers the optional style input; later steps do not', () => {
  const { rerender } = render(
    <StepPanel project={project({ status: 'CREATED', current_step: 'STYLE',
                                  completed_steps: 0, style_text: null })}
               onRun={vi.fn()} busy={false} />);
  expect(screen.getByLabelText(/art style \(optional\)/i)).toBeInTheDocument();

  rerender(<StepPanel project={project()} onRun={vi.fn()} busy={false} />);
  expect(screen.queryByLabelText(/art style/i)).not.toBeInTheDocument();
});

test('the optional style is trimmed and passed through on step 1', async () => {
  const onRun = vi.fn();
  render(<StepPanel project={project({ status: 'CREATED', current_step: 'STYLE',
                                       completed_steps: 0, style_text: null })}
                    onRun={onRun} busy={false} />);
  await userEvent.type(screen.getByLabelText(/art style/i), '  bold linocut  ');
  await userEvent.click(screen.getByRole('button', { name: /generate style/i }));
  expect(onRun).toHaveBeenCalledWith('STYLE', 'bold linocut');
});

test('a blank optional style is passed through as undefined', async () => {
  const onRun = vi.fn();
  render(<StepPanel project={project({ status: 'CREATED', current_step: 'STYLE',
                                       completed_steps: 0, style_text: null })}
                    onRun={onRun} busy={false} />);
  await userEvent.type(screen.getByLabelText(/art style/i), '   ');
  await userEvent.click(screen.getByRole('button', { name: /generate style/i }));
  expect(onRun).toHaveBeenCalledOnce();
  expect(onRun).toHaveBeenCalledWith('STYLE', undefined);
});

test('Running names the step rather than showing a bare spinner', () => {
  render(<StepPanel project={project({ step_state: 'RUNNING' })} onRun={vi.fn()} busy={false} />);
  const status = screen.getByRole('status');
  expect(status).toHaveTextContent(/generating the character list/i);
  expect(status).toHaveAttribute('aria-live', 'polite');
  expect(screen.getByRole('button', { name: /generating/i })).toBeDisabled();
});

test('Failed STYLE preserves its optional input and retries with the trimmed choice', async () => {
  const onRun = vi.fn();
  const readyStyle = project({ status: 'CREATED', current_step: 'STYLE', completed_steps: 0,
                               style_text: null });
  const { rerender } = render(<StepPanel project={readyStyle} onRun={onRun} busy={false} />);
  await userEvent.type(screen.getByLabelText(/art style/i), '  bold linocut  ');

  rerender(<StepPanel project={project({ ...readyStyle, step_state: 'FAILED',
                                        needs_attention: true,
                                        failure: { code: 'GEMINI_ERROR',
                                                   message: 'Gemini said no' } })}
                      onRun={onRun} busy={false} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Gemini said no');
  expect(screen.getByLabelText(/art style/i)).toHaveValue('  bold linocut  ');
  expect(screen.getByText(/already generated is saved/i)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /retry style/i }));
  expect(onRun).toHaveBeenCalledOnce();
  expect(onRun).toHaveBeenCalledWith('STYLE', 'bold linocut');
});

test('Interrupted STYLE preserves its optional input and retries with the trimmed choice', async () => {
  const onRun = vi.fn();
  const readyStyle = project({ status: 'CREATED', current_step: 'STYLE', completed_steps: 0,
                               style_text: null });
  const { rerender } = render(<StepPanel project={readyStyle} onRun={onRun} busy={false} />);
  await userEvent.type(screen.getByLabelText(/art style/i), '  paper collage  ');

  rerender(<StepPanel project={project({ ...readyStyle, step_state: 'RUNNING',
                                        is_interrupted: true, needs_attention: true })}
                      onRun={onRun} busy={false} />);
  expect(screen.getByRole('alert')).toHaveTextContent(/interrupted/i);
  expect(screen.getByLabelText(/art style/i)).toHaveValue('  paper collage  ');
  await userEvent.click(screen.getByRole('button', { name: /retry style/i }));
  expect(onRun).toHaveBeenCalledOnce();
  expect(onRun).toHaveBeenCalledWith('STYLE', 'paper collage');
});

test('a freshly loaded failed STYLE starts blank and accepts a new choice', async () => {
  const onRun = vi.fn();
  render(<StepPanel project={project({ status: 'CREATED', current_step: 'STYLE',
                                      completed_steps: 0, style_text: null,
                                      step_state: 'FAILED', needs_attention: true })}
                    onRun={onRun} busy={false} />);
  const input = screen.getByLabelText(/art style/i);
  expect(input).toHaveValue('');
  await userEvent.type(input, 'chalk pastel');
  await userEvent.click(screen.getByRole('button', { name: /retry style/i }));
  expect(onRun).toHaveBeenCalledOnce();
  expect(onRun).toHaveBeenCalledWith('STYLE', 'chalk pastel');
});

test('Interrupted wins over Running, because a live spinner would be a lie', () => {
  render(<StepPanel project={project({ step_state: 'RUNNING', is_interrupted: true })}
                    onRun={vi.fn()} busy={false} />);
  expect(screen.queryByText(/generating the character list/i)).not.toBeInTheDocument();
});

test('Complete offers no action and says nothing regenerates', () => {
  render(<StepPanel project={project({ status: 'DONE', current_step: null,
                                       display_status: 'Done', completed_steps: 5 })}
                    onRun={vi.fn()} busy={false} />);
  const status = screen.getByRole('status');
  expect(status).toHaveTextContent(/all 5 steps complete/i);
  expect(status).toHaveAttribute('aria-live', 'polite');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test('the button is disabled while a run request is in flight', () => {
  render(<StepPanel project={project()} onRun={vi.fn()} busy />);
  expect(screen.getByRole('button')).toBeDisabled();
});

test('busy disables both failed and interrupted retry actions', () => {
  const failed = project({ step_state: 'FAILED', needs_attention: true });
  const { rerender } = render(<StepPanel project={failed} onRun={vi.fn()} busy />);
  expect(screen.getByRole('button', { name: /retry characters/i })).toBeDisabled();

  rerender(<StepPanel project={project({ step_state: 'RUNNING', is_interrupted: true,
                                        needs_attention: true })}
                      onRun={vi.fn()} busy />);
  expect(screen.getByRole('button', { name: /retry characters/i })).toBeDisabled();
});

test('the stepper marks steps done, current and pending', () => {
  const { container } = render(<Stepper project={project()} />);
  const progress = screen.getByRole('list', { name: /generation progress/i });
  expect(progress).toBeInTheDocument();
  expect(screen.getByRole('listitem', { name: /style.*completed/i })).toBeInTheDocument();
  expect(screen.getByRole('listitem', { name: /characters.*current/i })).toBeInTheDocument();
  expect(screen.getByRole('listitem', { name: /chapters.*pending/i })).toBeInTheDocument();
  const steps = container.querySelectorAll('.stepper .step');
  expect(steps).toHaveLength(5);
  expect(steps[0].className).toContain('done');
  expect(steps[1].className).toContain('current');
  expect(steps[2].className).toContain('pending');
  expect(container.querySelectorAll('[aria-current="step"]')).toHaveLength(1);
});

test('a finished project has no current step in the stepper', () => {
  const { container } = render(
    <Stepper project={project({ status: 'DONE', current_step: null, completed_steps: 5 })} />);
  expect(container.querySelectorAll('.stepper .step.done')).toHaveLength(5);
  expect(container.querySelectorAll('.stepper .step.current')).toHaveLength(0);
  expect(container.querySelectorAll('[aria-current="step"]')).toHaveLength(0);
  expect(screen.getAllByRole('listitem', { name: /completed/i })).toHaveLength(5);
});
