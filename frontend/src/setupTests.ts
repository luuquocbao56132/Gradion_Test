import '@testing-library/jest-dom/vitest';

// jsdom ships a WebSocket that opens real TCP connections; a component test
// that mounts useProjectSocket would race actual localhost failures and
// reconnect timers. The baseline is a socket that never connects and never
// fires events - the hook's own tests stub their FakeSocket over it.
class QuietSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  close(): void {}
  constructor(_url: string) {}
}
globalThis.WebSocket = QuietSocket as unknown as typeof WebSocket;
