import type { StepName } from './types';

export const STEP_ORDER: StepName[] = [
  'STYLE', 'CHARACTERS', 'PORTRAITS', 'CHAPTERS', 'ILLUSTRATIONS',
];

export const STEP_LABELS: Record<StepName, string> = {
  STYLE: 'Style',
  CHARACTERS: 'Characters',
  PORTRAITS: 'Portraits',
  CHAPTERS: 'Chapters',
  ILLUSTRATIONS: 'Illustrations',
};

/** What the running caption names, so the user never sees a bare spinner
 *  (assessment 4.3). */
export const STEP_RUNNING_CAPTIONS: Record<StepName, string> = {
  STYLE: 'Reading your book text and defining an art style',
  CHARACTERS: 'Generating the character list from your book’s text',
  PORTRAITS: 'Generating character portraits',
  CHAPTERS: 'Generating a chapter illustration prompt',
  ILLUSTRATIONS: 'Generating the chapter illustration',
};
