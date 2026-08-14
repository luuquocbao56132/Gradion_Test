import { useCallback, useEffect, useState } from 'react';
import * as api from '../api';
import type { SessionView } from '../types';

export type SessionStatus = 'loading' | 'ready' | 'error';

/**
 * Identity comes from the server, never from client storage. The app boots by
 * asking GET /api/session who the user is, so a refresh or a returning visit
 * restores the session from the HttpOnly cookie.
 */
export function useSession() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [status, setStatus] = useState<SessionStatus>('loading');
  const [error, setError] = useState<string | null>(null);

  const bootstrap = useCallback(async () => {
    setStatus('loading');
    try {
      setSession(await api.getSession());
      setStatus('ready');
    } catch (err) {
      setError((err as Error).message);
      setStatus('error');
    }
  }, []);

  useEffect(() => { void bootstrap(); }, [bootstrap]);

  const signIn = useCallback(async (name: string, email: string) => {
    setError(null);
    try {
      setSession(await api.createSession(name, email));
      window.location.hash = '#/projects';
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  const signOut = useCallback(async () => {
    await api.deleteSession();
    setSession(null);
    window.location.hash = '#/';
  }, []);

  return { session, status, error, signIn, signOut, retry: bootstrap };
}
