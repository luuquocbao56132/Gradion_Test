import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import ConnectionBadge from '../components/ConnectionBadge';
import { useProjectSocket } from '../hooks/useProjectSocket';
import type { ProjectView } from '../types';

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  close = vi.fn();
  constructor(public url: string) { FakeSocket.instances.push(this); }
}

function Probe({ onState }: { onState: (p: ProjectView) => void }) {
  const state = useProjectSocket('p1', onState);
  return <span data-testid="state">{state}</span>;
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket);
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test('it connects to the project socket on the same origin', () => {
  render(<Probe onState={vi.fn()} />);
  expect(FakeSocket.instances[0].url).toMatch(/\/ws\/projects\/p1$/);
  expect(screen.getByTestId('state')).toHaveTextContent('connecting');
});

test('the first message makes the connection live and delivers state', () => {
  const onState = vi.fn();
  render(<Probe onState={onState} />);
  const socket = FakeSocket.instances[0];

  act(() => {
    socket.onopen?.();
    socket.onmessage?.({
      data: JSON.stringify({ type: 'project.state', project: { id: 'p1' } }),
    });
  });

  expect(onState).toHaveBeenCalledWith({ id: 'p1' });
  expect(screen.getByTestId('state')).toHaveTextContent('live');
});

test('an abnormal close reconnects with a bounded backoff', () => {
  render(<Probe onState={vi.fn()} />);
  act(() => { FakeSocket.instances[0].onclose?.({ code: 1006 }); });
  expect(screen.getByTestId('state')).toHaveTextContent('reconnecting');

  act(() => { vi.advanceTimersByTime(500); });
  expect(FakeSocket.instances).toHaveLength(2);

  act(() => { FakeSocket.instances[1].onclose?.({ code: 1006 }); });
  act(() => { vi.advanceTimersByTime(999); });
  expect(FakeSocket.instances).toHaveLength(2);        // 1s, not 500ms
  act(() => { vi.advanceTimersByTime(1); });
  expect(FakeSocket.instances).toHaveLength(3);
});

test('a 1008 policy close does not reconnect', () => {
  render(<Probe onState={vi.fn()} />);
  act(() => { FakeSocket.instances[0].onclose?.({ code: 1008 }); });

  expect(screen.getByTestId('state')).toHaveTextContent('closed');
  act(() => { vi.advanceTimersByTime(30_000); });
  expect(FakeSocket.instances).toHaveLength(1);
});

test('a malformed message is ignored rather than crashing the hook', () => {
  const onState = vi.fn();
  render(<Probe onState={onState} />);
  act(() => { FakeSocket.instances[0].onmessage?.({ data: 'not json' }); });
  expect(onState).not.toHaveBeenCalled();
});

test('unmounting closes the socket and cancels any pending reconnect', () => {
  const { unmount } = render(<Probe onState={vi.fn()} />);
  const socket = FakeSocket.instances[0];
  act(() => { socket.onclose?.({ code: 1006 }); });
  unmount();
  act(() => { vi.advanceTimersByTime(30_000); });
  expect(FakeSocket.instances).toHaveLength(1);
});

test('the badge stays quiet when live and offers a refresh when not', () => {
  const onRefresh = vi.fn();
  const { rerender } = render(<ConnectionBadge state="live" onRefresh={onRefresh} />);
  expect(screen.queryByRole('button', { name: /refresh/i })).not.toBeInTheDocument();

  rerender(<ConnectionBadge state="reconnecting" onRefresh={onRefresh} />);
  expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
});
