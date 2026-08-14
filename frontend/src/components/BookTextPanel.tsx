import { useEffect, useRef, useState } from 'react';
import * as api from '../api';
import StateMessage from './StateMessage';

interface LoadState {
  projectId: string;
  text: string | null;
  loading: boolean;
  error: string | null;
}

/**
 * A permanent disclosure panel, not a modal. The book is reference material you
 * read beside the prompts derived from it, and a panel always present in the
 * layout cannot have its affordance vanish the way the demo's does at
 * app-demo.html:700 (design 10.6).
 */
export default function BookTextPanel({ projectId, excerpt }: {
  projectId: string; excerpt: string;
}) {
  const [disclosure, setDisclosure] = useState({ projectId, open: false });
  const [loadState, setLoadState] = useState<LoadState>({
    projectId, text: null, loading: false, error: null,
  });
  const mounted = useRef(true);
  const currentProject = useRef(projectId);
  const requestGeneration = useRef(0);

  if (currentProject.current !== projectId) {
    currentProject.current = projectId;
    requestGeneration.current += 1;
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestGeneration.current += 1;
    };
  }, []);

  const open = disclosure.projectId === projectId && disclosure.open;
  const visibleState = loadState.projectId === projectId ? loadState : null;

  const load = async () => {
    const requestProject = projectId;
    const generation = ++requestGeneration.current;
    setLoadState({
      projectId: requestProject, text: null, loading: true, error: null,
    });
    try {
      const text = await api.getBook(requestProject);
      if (mounted.current && currentProject.current === requestProject &&
          requestGeneration.current === generation) {
        setLoadState({
          projectId: requestProject, text, loading: false, error: null,
        });
      }
    } catch (err) {
      if (mounted.current && currentProject.current === requestProject &&
          requestGeneration.current === generation) {
        setLoadState({
          projectId: requestProject, text: null, loading: false,
          error: (err as Error).message,
        });
      }
    }
  };

  const expand = () => {
    setDisclosure({ projectId, open: true });
    if (visibleState?.text === null && !visibleState.loading) void load();
    if (visibleState === null) void load();
  };

  return (
    <section className="side-note book-panel">
      <h5>Book text</h5>
      {!open && <p className="excerpt">{excerpt}</p>}
      {open && visibleState?.loading &&
        <StateMessage kind="loading" label="Loading the full text…" />}
      {open && visibleState?.error &&
        <StateMessage kind="error" message={visibleState.error} onRetry={load} />}
      {open && visibleState?.text !== null && visibleState?.text !== undefined &&
        <pre className="book-full">{visibleState.text}</pre>}
      <button type="button" className="gd-btn gd-btn-ghost gd-btn-sm"
              onClick={open ? () => setDisclosure({ projectId, open: false }) : expand}>
        {open ? 'Collapse' : 'Read full text →'}
      </button>
    </section>
  );
}
