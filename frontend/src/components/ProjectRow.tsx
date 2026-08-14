import { STEP_ORDER } from '../steps';
import type { ProjectListItem } from '../types';

const PILL_CLASS: Record<string, string> = {
  Draft: 'gd-pill gray', 'In progress': 'gd-pill', Done: 'gd-pill ink',
};

export default function ProjectRow({ project, onOpen }: {
  project: ProjectListItem; onOpen: (id: string) => void;
}) {
  const created = new Date(project.created_at).toLocaleDateString();
  return (
    <div className="project-row" role="button" tabIndex={0}
         onClick={() => onOpen(project.id)}
         onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onOpen(project.id); }}>
      <div className="title">
        <h4>{project.title}</h4>
        <span className="meta">Created {created}</span>
      </div>

      {/* Five segments, one per step, filled by completed_steps (assessment 4.4;
          mirrors app-demo.html:556). */}
      <div className="progress-mini" aria-label={`${project.completed_steps} of 5 steps complete`}>
        {STEP_ORDER.map((step, index) => (
          <span key={step} className={`seg${index < project.completed_steps ? ' on' : ''}`} />
        ))}
      </div>

      {/* Beside the pill, never instead of it — the pill vocabulary is fixed at
          three values by the assessment (design 4.2). */}
      {project.needs_attention && (
        <span className="attention-flag" title="This project needs attention">
          ⚠ Needs attention
        </span>
      )}
      <span className={PILL_CLASS[project.display_status]}>
        {project.display_status === 'In progress' && <span className="dot" />}
        {project.display_status}
      </span>
    </div>
  );
}
