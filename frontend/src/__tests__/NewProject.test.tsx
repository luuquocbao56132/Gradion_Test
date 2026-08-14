import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import NewProject from '../components/NewProject';
import * as api from '../api';
import type { ProjectView } from '../types';

const created = { id: 'p-new' } as ProjectView;

afterEach(() => vi.restoreAllMocks());

test('the paste path creates a project', async () => {
  const spy = vi.spyOn(api, 'createProject').mockResolvedValue(created);
  const onCreated = vi.fn();
  render(<NewProject onCreated={onCreated} onCancel={vi.fn()} />);

  await userEvent.type(screen.getByLabelText(/project title/i), 'Willows');
  await userEvent.type(screen.getByLabelText(/book text/i), 'Once upon a time.');
  await userEvent.click(screen.getByRole('button', { name: /create project/i }));

  expect(spy).toHaveBeenCalledWith('Willows', 'Once upon a time.');
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith('p-new'));
});

test('the .txt path reads the file into the same field and submits identically', async () => {
  const spy = vi.spyOn(api, 'createProject').mockResolvedValue(created);
  render(<NewProject onCreated={vi.fn()} onCancel={vi.fn()} />);

  await userEvent.type(screen.getByLabelText(/project title/i), 'From file');
  const file = new File(['Chapter 1. The river bank.'], 'book.txt', { type: 'text/plain' });
  await userEvent.upload(screen.getByLabelText(/choose a \.txt file/i), file);

  const textarea = screen.getByLabelText(/book text/i) as HTMLTextAreaElement;
  await waitFor(() => expect(textarea.value).toBe('Chapter 1. The river bank.'));
  expect(screen.getByText(/book\.txt loaded/i)).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /create project/i }));
  expect(spy).toHaveBeenCalledWith('From file', 'Chapter 1. The river bank.');
});

test('a missing title or empty text blocks submission', async () => {
  const spy = vi.spyOn(api, 'createProject');
  render(<NewProject onCreated={vi.fn()} onCancel={vi.fn()} />);

  await userEvent.click(screen.getByRole('button', { name: /create project/i }));
  expect(spy).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/title/i);

  await userEvent.type(screen.getByLabelText(/project title/i), 'Willows');
  await userEvent.click(screen.getByRole('button', { name: /create project/i }));
  expect(spy).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/book text/i);
});

test('a server failure is shown and does not navigate away', async () => {
  vi.spyOn(api, 'createProject').mockRejectedValue(new Error('Disk full'));
  const onCreated = vi.fn();
  render(<NewProject onCreated={onCreated} onCancel={vi.fn()} />);

  await userEvent.type(screen.getByLabelText(/project title/i), 'Willows');
  await userEvent.type(screen.getByLabelText(/book text/i), 'text');
  await userEvent.click(screen.getByRole('button', { name: /create project/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Disk full');
  expect(onCreated).not.toHaveBeenCalled();
});
