import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import BookTextPanel from '../components/BookTextPanel';
import * as api from '../api';

const stateSetTracker = vi.hoisted(() => ({
  componentMounted: true,
  callsAfterUnmount: 0,
}));

vi.mock('react', async (importOriginal) => {
  const react = await importOriginal<typeof import('react')>();
  return {
    ...react,
    useState<T>(initialState: T | (() => T)) {
      const [value, setValue] = react.useState(initialState);
      const trackedSetter: typeof setValue = (nextValue) => {
        if (!stateSetTracker.componentMounted) stateSetTracker.callsAfterUnmount += 1;
        setValue(nextValue);
      };
      return [value, trackedSetter] as const;
    },
  };
});

afterEach(() => {
  stateSetTracker.componentMounted = true;
  stateSetTracker.callsAfterUnmount = 0;
  vi.restoreAllMocks();
});

test('the excerpt is visible without any fetch', () => {
  const spy = vi.spyOn(api, 'getBook');
  render(<BookTextPanel projectId="p1" excerpt="Once upon a time…" />);
  expect(screen.getByText('Once upon a time…')).toBeInTheDocument();
  expect(spy).not.toHaveBeenCalled();
});

test('expanding fetches the full text lazily and shows a loading state', async () => {
  let resolve!: (value: string) => void;
  vi.spyOn(api, 'getBook').mockReturnValue(new Promise((r) => { resolve = r; }));
  render(<BookTextPanel projectId="p1" excerpt="Once…" />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  expect(screen.getByRole('status')).toHaveTextContent(/loading/i);

  resolve('The whole book, all of it.');
  expect(await screen.findByText('The whole book, all of it.')).toBeInTheDocument();
});

test('a failed fetch offers a retry and keeps the panel usable', async () => {
  const spy = vi.spyOn(api, 'getBook').mockRejectedValue(new Error('Network down'));
  render(<BookTextPanel projectId="p1" excerpt="Once…" />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Network down');

  spy.mockResolvedValue('Recovered text.');
  await userEvent.click(screen.getByRole('button', { name: /try again/i }));
  expect(await screen.findByText('Recovered text.')).toBeInTheDocument();
});

test('the full text is fetched once, not on every expand', async () => {
  const spy = vi.spyOn(api, 'getBook').mockResolvedValue('Full text.');
  render(<BookTextPanel projectId="p1" excerpt="Once…" />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  expect(await screen.findByText('Full text.')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /collapse/i }));
  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));

  expect(spy).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Full text.')).toBeInTheDocument();
});

test('switching projects hides loaded text and lazily fetches the new project', async () => {
  const spy = vi.spyOn(api, 'getBook')
    .mockResolvedValueOnce('Project one full text.')
    .mockResolvedValueOnce('Project two full text.');
  const { rerender } = render(<BookTextPanel projectId="p1" excerpt="Project one excerpt." />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  expect(await screen.findByText('Project one full text.')).toBeInTheDocument();

  rerender(<BookTextPanel projectId="p2" excerpt="Project two excerpt." />);

  expect(screen.queryByText('Project one full text.')).not.toBeInTheDocument();
  expect(screen.getByText('Project two excerpt.')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));

  expect(await screen.findByText('Project two full text.')).toBeInTheDocument();
  expect(spy).toHaveBeenNthCalledWith(1, 'p1');
  expect(spy).toHaveBeenNthCalledWith(2, 'p2');
});

test('an obsolete project request cannot overwrite the active project', async () => {
  let resolveProjectOne!: (value: string) => void;
  let resolveProjectTwo!: (value: string) => void;
  vi.spyOn(api, 'getBook').mockImplementation((projectId) => new Promise((resolve) => {
    if (projectId === 'p1') resolveProjectOne = resolve;
    else resolveProjectTwo = resolve;
  }));
  const { rerender } = render(<BookTextPanel projectId="p1" excerpt="Project one excerpt." />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  rerender(<BookTextPanel projectId="p2" excerpt="Project two excerpt." />);
  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));

  await act(async () => { resolveProjectTwo('Project two wins.'); });
  expect(await screen.findByText('Project two wins.')).toBeInTheDocument();

  await act(async () => { resolveProjectOne('Late project one text.'); });

  expect(screen.getByText('Project two wins.')).toBeInTheDocument();
  expect(screen.queryByText('Late project one text.')).not.toBeInTheDocument();
});

test('a request completion after unmount does not attempt a state update', async () => {
  let resolve!: (value: string) => void;
  const request = new Promise<string>((requestResolve) => { resolve = requestResolve; });
  vi.spyOn(api, 'getBook').mockReturnValue(request);
  const { unmount } = render(<BookTextPanel projectId="p1" excerpt="Once…" />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  unmount();
  stateSetTracker.componentMounted = false;

  resolve('Too late.');
  await request;
  await Promise.resolve();

  expect(stateSetTracker.callsAfterUnmount).toBe(0);
  stateSetTracker.componentMounted = true;
});
