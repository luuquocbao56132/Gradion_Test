/**
 * Mirrors backend/app/models.py and backend/app/steps.py field-for-field.
 * The backend is authoritative — these types must never drift from it, since
 * a mismatch fails silently at runtime rather than at compile time.
 */

export type StepName = 'STYLE' | 'CHARACTERS' | 'PORTRAITS' | 'CHAPTERS' | 'ILLUSTRATIONS';

export type ProjectStatus =
  | 'CREATED'
  | 'STYLE_SET'
  | 'CHARACTERS_GENERATED'
  | 'PORTRAITS_GENERATED'
  | 'CHAPTERS_GENERATED'
  | 'DONE';

export type StepState = 'IDLE' | 'RUNNING' | 'FAILED';

/** Exactly three values (assessment 4.4). Failure and interruption ride on
 *  needs_attention, not on a fourth pill (design 4.2). */
export type DisplayStatus = 'Draft' | 'In progress' | 'Done';

export type ImageState = 'ready' | 'generating' | 'pending';

export type FailureCode = 'GEMINI_ERROR' | 'INVALID_OUTPUT' | 'INTERNAL';

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'closed';

export interface SessionView {
  user_id: string;
  name: string;
  email: string;
}

export interface Failure {
  code: FailureCode;
  message: string;
}

export interface EntityView {
  id: string;
  position: number;
  name: string;
  prompt: string;
  image_url: string | null;
  image_state: ImageState;
}

export interface ProjectListItem {
  id: string;
  title: string;
  created_at: string;
  status: ProjectStatus;
  current_step: StepName | null;
  display_status: DisplayStatus;
  needs_attention: boolean;
  is_interrupted: boolean;
  completed_steps: number;
}

export interface ProjectView {
  id: string;
  title: string;
  created_at: string;
  status: ProjectStatus;
  step_state: StepState;
  current_step: StepName | null;
  display_status: DisplayStatus;
  needs_attention: boolean;
  is_interrupted: boolean;
  completed_steps: number;
  style_text: string | null;
  book_excerpt: string;
  failure: Failure | null;
  characters: EntityView[];
  chapters: EntityView[];
}

export interface BookView {
  text: string;
}

/**
 * 202 and 409 are both truth-carrying outcomes, never errors. A rejected
 * duplicate should look like a UI that was already correct (design 8, 10.5).
 */
export type RunOutcome =
  | { ok: true; project: ProjectView }
  | { ok: false; conflict: true; project: ProjectView };
