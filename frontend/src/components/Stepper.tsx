import { STEP_LABELS, STEP_ORDER } from '../steps';
import type { ProjectView } from '../types';

export default function Stepper({ project }: { project: ProjectView }) {
  return (
    <ol className="stepper" aria-label="Generation progress">
      {STEP_ORDER.map((step, index) => {
        const done = index < project.completed_steps;
        const current = step === project.current_step;
        const state = done ? 'done' : current ? 'current' : 'pending';
        const accessibleState = state === 'done' ? 'completed' : state;
        return (
          <li key={step} className={`step ${state}`}
              aria-label={`${STEP_LABELS[step]}: ${accessibleState}`}
              aria-current={state === 'current' ? 'step' : undefined}>
            <span className={`gd-num-square ${done ? 'done' : current ? '' : 'gray'}`}>
              {done ? '✓' : index + 1}
            </span>
            <span className="lbl">{STEP_LABELS[step]}</span>
          </li>
        );
      })}
    </ol>
  );
}
