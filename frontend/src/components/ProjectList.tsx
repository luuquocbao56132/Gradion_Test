import { useCallback, useEffect, useState } from 'react';
import * as api from '../api';
import type { ProjectListItem } from '../types';
import EmptyState from './EmptyState';
import ProjectRow from './ProjectRow';
import StateMessage from './StateMessage';

/**
 * The list stays REST fetch-on-open. Realtime's value is watching a long-running
 * step, which happens on the detail screen; a list subscription would need a
 * user-scoped channel with a different lifecycle for a screen you must navigate
 * away from to act (design 9.8).
 */
export default function ProjectList({ onOpen, onNew }: {
  onOpen: (id: string) => void; onNew: () => void;
}) {
  const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setProjects(null);
    try {
      setProjects(await api.listProjects());
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (error) return <StateMessage kind="error" message={error} onRetry={load} />;
  if (projects === null) return <StateMessage kind="loading" label="Loading your projects…" />;

  return (
    <>
      <div className="list-head">
        <h2>Your projects</h2>
        <button type="button" className="gd-btn gd-btn-primary" onClick={onNew}>
          + New project
        </button>
      </div>
      {projects.length === 0
        ? <EmptyState onNew={onNew} />
        : (
          <div className="project-list">
            {projects.map((p) => <ProjectRow key={p.id} project={p} onOpen={onOpen} />)}
          </div>
        )}
    </>
  );
}
