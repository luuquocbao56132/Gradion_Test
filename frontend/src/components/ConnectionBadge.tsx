import type { ConnectionState } from '../types';

const LABELS: Record<ConnectionState, string> = {
  connecting: 'Connecting…',
  live: 'Live',
  reconnecting: 'Reconnecting — this view may be behind',
  closed: 'Disconnected',
};

export default function ConnectionBadge({ state, onRefresh }: {
  state: ConnectionState; onRefresh: () => void;
}) {
  if (state === 'live') return null;   // visible but quiet: silence when healthy
  return (
    <p className="connection-badge" role="status" aria-live="polite">
      {LABELS[state]}
      <button type="button" className="gd-btn gd-btn-ghost gd-btn-sm" onClick={onRefresh}>
        Refresh
      </button>
    </p>
  );
}
