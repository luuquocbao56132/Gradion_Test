import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import App from '../App';
import * as api from '../api';
import type { SessionView } from '../types';

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
