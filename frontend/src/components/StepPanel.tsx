import { useState } from 'react';
import { STEP_LABELS, STEP_RUNNING_CAPTIONS } from '../steps';
import type { ProjectView, StepName } from '../types';

interface Props {
  project: ProjectView;
  onRun: (step: StepName, style?: string) => void;
  busy: boolean;
}

export default function StepPanel({ project, onRun, busy }: Props) {
  const [style, setStyle] = useState('');
  const step = project.current_step;

  if (step === null) {
    return (
      <section className="step-panel">
        <p className="status-line">✓ All 5 steps complete — nothing left to generate.</p>
        <p className="help">
          This project is done. Reopen it any time; nothing here regenerates automatically.
        </p>
      </section>
    );
  }

  const label = STEP_LABELS[step];

  // Interrupted is checked before Running: a live spinner on a step whose
  // process is gone would be a lie (design 10.4).
  if (project.is_interrupted) {
    return (
      <section className="step-panel">
        <p className="status-line" role="alert">
          This step was interrupted when the server restarted, and never finished.
        </p>
        <p className="help">
          Nothing before it was affected — everything already generated is saved.
          Retrying is safe.
        </p>
        <button type="button" className="gd-btn gd-btn-secondary" disabled={busy}
                onClick={() => onRun(step)}>
          Retry {label}
        </button>
      </section>
    );
  }

  if (project.step_state === 'FAILED') {
    return (
      <section className="step-panel">
        <p className="status-line" role="alert">
          {project.failure?.message ?? 'This step failed.'}
        </p>
        <p className="help">
          Only this step failed. Everything already generated is saved and untouched.
        </p>
        <button type="button" className="gd-btn gd-btn-secondary" disabled={busy}
                onClick={() => onRun(step)}>
          Retry {label}
        </button>
      </section>
    );
  }

  if (project.step_state === 'RUNNING') {
    return (
      <section className="step-panel">
        <p className="status-line" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          {STEP_RUNNING_CAPTIONS[step]} — real Gemini calls take 10–30s, longer for images.
        </p>
        <button type="button" className="gd-btn gd-btn-primary" disabled>
          Generating…
        </button>
      </section>
    );
  }

  return (
    <section className="step-panel">
      <p className="status-line" role="status" aria-live="polite">
        Ready for the next step: <b>{label}</b>.
      </p>
      {step === 'STYLE' && (
        <div className="gd-field">
          <label htmlFor="style-input">Art style (optional)</label>
          <input id="style-input" value={style} onChange={(e) => setStyle(e.target.value)}
                 placeholder="Leave blank to let Gemini choose a style based on your book" />
        </div>
      )}
      <p className="help">
        Reopening this page mid-step won’t fire a second request — it shows the same
        in-flight state until it lands.
      </p>
      <button type="button" className="gd-btn gd-btn-primary" disabled={busy}
              onClick={() => onRun(step, step === 'STYLE' && style.trim()
                ? style.trim() : undefined)}>
        Generate {label}
      </button>
    </section>
  );
}
