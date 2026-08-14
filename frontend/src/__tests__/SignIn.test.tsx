import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import SignIn from '../components/SignIn';

test('name and email are trimmed and the email is lowercased before submission', async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<SignIn onSubmit={onSubmit} error={null} busy={false} />);

  await userEvent.type(screen.getByLabelText(/full name/i), '  Ada Lovelace  ');
  await userEvent.type(screen.getByLabelText(/email/i), '  ADA@Example.COM  ');
  await userEvent.click(screen.getByRole('button', { name: /continue/i }));

  expect(onSubmit).toHaveBeenCalledWith('Ada Lovelace', 'ada@example.com');
});

test('an empty name blocks submission and explains why', async () => {
  const onSubmit = vi.fn();
  render(<SignIn onSubmit={onSubmit} error={null} busy={false} />);

  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com');
  await userEvent.click(screen.getByRole('button', { name: /continue/i }));

  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/name/i);
});

test('a malformed email blocks submission', async () => {
  const onSubmit = vi.fn();
  render(<SignIn onSubmit={onSubmit} error={null} busy={false} />);

  await userEvent.type(screen.getByLabelText(/full name/i), 'Ada');
  await userEvent.type(screen.getByLabelText(/email/i), 'nope');
  await userEvent.click(screen.getByRole('button', { name: /continue/i }));

  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/valid email/i);
});

test('a server error is shown to the user', () => {
  render(<SignIn onSubmit={vi.fn()} error="Service unavailable" busy={false} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Service unavailable');
});

test('the button is disabled while the request is in flight', () => {
  render(<SignIn onSubmit={vi.fn()} error={null} busy />);
  expect(screen.getByRole('button', { name: /signing in/i })).toBeDisabled();
});
