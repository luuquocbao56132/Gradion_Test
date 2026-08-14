import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import EntityCard from '../components/EntityCard';
import StylePanel from '../components/StylePanel';
import type { EntityView } from '../types';

function entity(overrides: Partial<EntityView> = {}): EntityView {
  return { id: 'c1', position: 0, name: 'Toad', prompt: 'A stout toad in a green coat',
           image_url: null, image_state: 'pending', ...overrides };
}

// Fixtures use two characters, never three: production is capped at two, and a
// three-character fixture would encode a state that cannot exist.
const bothPending: EntityView[] = [
  entity({ id: 'c1', name: 'Toad', image_state: 'generating' }),
  entity({ id: 'c2', name: 'Ratty', image_state: 'pending' }),
];
const firstReady: EntityView[] = [
  entity({ id: 'c1', name: 'Toad', image_state: 'ready',
           image_url: '/api/projects/p1/characters/c1/portrait' }),
  entity({ id: 'c2', name: 'Ratty', image_state: 'generating' }),
];

test('[null, null] renders the first as generating and the second as pending', () => {
  render(<>{bothPending.map((e) => <EntityCard key={e.id} kind="character" item={e} />)}</>);
  expect(screen.getByText(/generating portrait for toad/i)).toBeInTheDocument();
  expect(screen.getByText(/not generated yet/i)).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

test('[path, null] renders the first as ready and the second as generating', () => {
  render(<>{firstReady.map((e) => <EntityCard key={e.id} kind="character" item={e} />)}</>);
  const image = screen.getByRole('img', { name: /portrait of toad/i });
  expect(image).toHaveAttribute('src', '/api/projects/p1/characters/c1/portrait');
  expect(screen.getByText(/generating portrait for ratty/i)).toBeInTheDocument();
});

test('the name and prompt are always shown, image or not', () => {
  render(<EntityCard kind="character" item={entity()} />);
  expect(screen.getByText('Toad')).toBeInTheDocument();
  expect(screen.getByText('A stout toad in a green coat')).toBeInTheDocument();
});

test('a chapter card renders an illustration with a wider art slot', () => {
  const { container } = render(
    <EntityCard kind="chapter" item={entity({ id: 'ch1', name: 'Chapter One',
      image_state: 'ready', image_url: '/api/projects/p1/chapters/ch1/illustration' })} />);
  expect(screen.getByRole('img', { name: /illustration for chapter one/i })).toBeInTheDocument();
  expect(container.querySelector('.art.chapter')).not.toBeNull();
});

test('the style panel shows nothing before a style exists', () => {
  const { container } = render(<StylePanel styleText={null} />);
  expect(container).toBeEmptyDOMElement();
});

test('the style panel shows the generated style', () => {
  render(<StylePanel styleText="Warm hand-painted watercolour" />);
  expect(screen.getByText('Warm hand-painted watercolour')).toBeInTheDocument();
});
