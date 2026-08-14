import type { ConnectionState } from './types';

const connectionStates: ConnectionState[] = [
  'connecting',
  'live',
  'reconnecting',
  'closed',
];

void connectionStates;

// @ts-expect-error The connection state vocabulary is intentionally closed.
const invalidConnectionState: ConnectionState = 'offline';

void invalidConnectionState;
