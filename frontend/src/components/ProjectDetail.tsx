import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api';
import { useProjectSocket } from '../hooks/useProjectSocket';
import type { ProjectView, StepName } from '../types';
import BookTextPanel from './BookTextPanel';
import ConnectionBadge from './ConnectionBadge';
import EntityCard from './EntityCard';
import StateMessage from './StateMessage';
import StepPanel from './StepPanel';
import Stepper from './Stepper';
import StylePanel from './StylePanel';

export default function ProjectDetail({ projectId, onBack }: {
  projectId: string; onBack: () => void;
}) {
  const [projectState, setProjectState] = useState<{
    projectId: string; value: ProjectView;
  } | null>(null);
  const [loadErrorState, setLoadErrorState] = useState<{
    projectId: string; message: string;
  } | null>(null);
  const [transportErrorState, setTransportErrorState] = useState<{
    projectId: string; message: string;
  } | null>(null);
  const [busyProjectId, setBusyProjectId] = useState<string | null>(null);
  const mounted = useRef(false);
  const currentProjectId = useRef(projectId);
  const truthGeneration = useRef(0);
  const runGeneration = useRef(0);

  // Invalidate the previous project's work during render, before a late
  // completion gets an opportunity to publish into the new route.
  if (currentProjectId.current !== projectId) {
    currentProjectId.current = projectId;
    truthGeneration.current += 1;
    runGeneration.current += 1;
  }

  const load = useCallback(async () => {
    const requestProjectId = projectId;
    const generation = ++truthGeneration.current;
    setLoadErrorState(null);
    setTransportErrorState(null);
    setProjectState(null);
    try {
      const nextProject = await api.getProject(requestProjectId);
      if (mounted.current && currentProjectId.current === requestProjectId &&
          truthGeneration.current === generation) {
        setProjectState({ projectId: requestProjectId, value: nextProject });
      }
    } catch (err) {
      if (mounted.current && currentProjectId.current === requestProjectId &&
          truthGeneration.current === generation) {
        setLoadErrorState({ projectId: requestProjectId, message: (err as Error).message });
      }
    }
  }, [projectId]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      truthGeneration.current += 1;
      runGeneration.current += 1;
    };
  }, [load]);

  // Replaces project state wholesale on every message. There is no client-side
  // event-sourced state machine (design 9.1). The message's own project id is
  // the guard: a frame from a socket that outlived its route can never publish
  // into the current one.
  const connection = useProjectSocket(projectId, (next: ProjectView) => {
    if (!mounted.current || next.id !== currentProjectId.current) return;
    // Socket truth postdates any in-flight GET or run outcome; seal them off.
    truthGeneration.current += 1;
    setProjectState({ projectId: next.id, value: next });
  });

  const run = async (step: StepName, style?: string) => {
    const requestProjectId = projectId;
    const requestRunGeneration = ++runGeneration.current;
    const generation = ++truthGeneration.current;
    setBusyProjectId(requestProjectId);
    setTransportErrorState(null);
    try {
      // 202 and 409 both carry authoritative state. Neither is a local
      // transition, and neither is a pipeline failure (design 10.5).
      const outcome = await api.runStep(requestProjectId, step, style);
      if (mounted.current && currentProjectId.current === requestProjectId &&
          truthGeneration.current === generation) {
        // Seal this accepted snapshot against every load that predates the run.
        truthGeneration.current += 1;
        setProjectState({ projectId: requestProjectId, value: outcome.project });
      }
    } catch (err) {
      if (mounted.current && currentProjectId.current === requestProjectId &&
          truthGeneration.current === generation) {
        // A transport failure means we may be behind, never that the step
        // failed. Preserve the last snapshot and reconcile once in the
        // background; this GET neither becomes a loading screen nor retries.
        setTransportErrorState({
          projectId: requestProjectId, message: (err as Error).message,
        });
        const reconciliationGeneration = ++truthGeneration.current;
        void api.getProject(requestProjectId).then((nextProject) => {
          if (mounted.current && currentProjectId.current === requestProjectId &&
              truthGeneration.current === reconciliationGeneration) {
            setProjectState({ projectId: requestProjectId, value: nextProject });
          }
        }).catch(() => {
          // The prior authoritative snapshot and transport banner stay usable.
        });
      }
    } finally {
      if (mounted.current && currentProjectId.current === requestProjectId &&
          runGeneration.current === requestRunGeneration) {
        setBusyProjectId(null);
      }
    }
  };

  const project = projectState?.projectId === projectId ? projectState.value : null;
  const loadError = loadErrorState?.projectId === projectId ? loadErrorState.message : null;
  const transportError = transportErrorState?.projectId === projectId
    ? transportErrorState.message : null;
  const busy = busyProjectId === projectId;

  if (loadError) return <StateMessage kind="error" message={loadError} onRetry={load} />;
  if (project === null) return <StateMessage kind="loading" label="Loading project…" />;

  return (
    <>
      <button type="button" className="back-link" onClick={onBack}>← Back to projects</button>
      <h2>{project.title}</h2>
      <p className="meta">Created {new Date(project.created_at).toLocaleDateString()}</p>

      <Stepper project={project} />
      <ConnectionBadge state={connection} onRefresh={load} />

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
