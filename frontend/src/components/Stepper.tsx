import { STEP_LABELS, STEP_ORDER } from '../steps';
import type { ProjectView } from '../types';

export default function Stepper({ project }: { project: ProjectView }) {
  return (
    <ol className="stepper">
      {STEP_ORDER.map((step, index) => {
        const done = index < project.completed_steps;
        const current = step === project.current_step;
        const state = done ? 'done' : current ? 'current' : 'pending';
        return (
          <li key={step} className={`step ${state}`} aria-current={current ? 'step' : undefined}>
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
