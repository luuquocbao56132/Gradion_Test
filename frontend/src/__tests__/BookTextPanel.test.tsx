import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import BookTextPanel from '../components/BookTextPanel';
import * as api from '../api';

afterEach(() => vi.restoreAllMocks());

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
});
