import { useEffect, useRef, useState } from 'react';
import type { ConnectionState, ProjectView } from '../types';

// No jitter: jitter de-synchronises a thundering herd, and we have one or two
// tabs on localhost (design 10.5).
export const BACKOFF_MS = [500, 1000, 2000, 5000];

export function useProjectSocket(
  projectId: string | null,
  onState: (project: ProjectView) => void,
): ConnectionState {
  const [state, setState] = useState<ConnectionState>('connecting');
  const onStateRef = useRef(onState);
  onStateRef.current = onState;

  useEffect(() => {
    if (!projectId) return;
    let attempt = 0;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      setState((current) => (current === 'live' ? 'reconnecting' : current));
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/projects/${projectId}`);

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message?.type !== 'project.state' || !message.project) return;
          attempt = 0;
          setState('live');
          onStateRef.current(message.project as ProjectView);
        } catch {
          // A message we cannot parse tells us nothing; it is not a failure.
        }
      };

      socket.onclose = (event) => {
        if (disposed) return;
        // Binary rule: policy rejection means stop and consult the session.
        // Anything else means reconnect, receive state, continue (design 9.6).
        if (event.code === 1008) {
          setState('closed');
          return;
        }
        setState('reconnecting');
        const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [projectId]);

  return state;
}
