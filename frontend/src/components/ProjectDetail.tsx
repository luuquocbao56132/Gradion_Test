import { useCallback, useEffect, useState } from 'react';
import * as api from '../api';
import type { ProjectView, StepName } from '../types';
import BookTextPanel from './BookTextPanel';
import EntityCard from './EntityCard';
import StateMessage from './StateMessage';
import StepPanel from './StepPanel';
import Stepper from './Stepper';
import StylePanel from './StylePanel';

export default function ProjectDetail({ projectId, onBack }: {
  projectId: string; onBack: () => void;
}) {
  const [project, setProject] = useState<ProjectView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [transportError, setTransportError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    setProject(null);
    try {
      setProject(await api.getProject(projectId));
    } catch (err) {
      setLoadError((err as Error).message);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  const run = async (step: StepName, style?: string) => {
    setBusy(true);
    setTransportError(null);
    try {
      // 202 and 409 both carry authoritative state. Neither is a local
      // transition, and neither is a pipeline failure (design 10.5).
      const outcome = await api.runStep(projectId, step, style);
      setProject(outcome.project);
    } catch (err) {
      // A transport failure means we may be behind, never that the step failed.
      setTransportError((err as Error).message);
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (loadError) return <StateMessage kind="error" message={loadError} onRetry={load} />;
  if (project === null) return <StateMessage kind="loading" label="Loading project…" />;

  return (
    <>
      <button type="button" className="back-link" onClick={onBack}>← Back to projects</button>
      <h2>{project.title}</h2>
      <p className="meta">Created {new Date(project.created_at).toLocaleDateString()}</p>

      <Stepper project={project} />

      {transportError && (
        <p className="banner" role="alert">
          {transportError} — showing the last state we could read from the server.
        </p>
      )}

      <div className="detail-grid">
        <div>
          <StepPanel project={project} onRun={run} busy={busy} />

          {project.chapters.length > 0 && (
            <>
              <div className="panel-title"><h3>Chapters ({project.chapters.length})</h3></div>
              <div className="entity-grid single">
                {project.chapters.map((c) => (
                  <EntityCard key={c.id} kind="chapter" item={c} />
                ))}
              </div>
            </>
          )}

          {project.characters.length > 0 && (
            <>
              <div className="panel-title"><h3>Characters ({project.characters.length})</h3></div>
              <div className="entity-grid">
                {project.characters.map((c) => (
                  <EntityCard key={c.id} kind="character" item={c} />
                ))}
              </div>
            </>
          )}
        </div>

        <aside>
          <StylePanel styleText={project.style_text} />
          <BookTextPanel projectId={project.id} excerpt={project.book_excerpt} />
        </aside>
      </div>
    </>
  );
}
