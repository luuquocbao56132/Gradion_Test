import type { EntityView } from '../types';

interface Props {
  kind: 'character' | 'chapter';
  item: EntityView;
}

export default function EntityCard({ kind, item }: Props) {
  const noun = kind === 'character' ? 'portrait' : 'illustration';
  const alt = kind === 'character'
    ? `Portrait of ${item.name}` : `Illustration for ${item.name}`;

  return (
    <article className="entity-card">
      <div className={`art${kind === 'chapter' ? ' chapter' : ''}` +
                      (item.image_state === 'ready' ? '' : ' pending')}>
        {item.image_state === 'ready' && item.image_url && (
          <img src={item.image_url} alt={alt} />
        )}
        {item.image_state === 'generating' && (
          <div role="status" aria-live="polite">
            <span className="spinner" aria-hidden="true" />
            <p className="gen-caption">Generating {noun} for {item.name}…</p>
          </div>
        )}
        {item.image_state === 'pending' && (
          <span className="placeholder-label muted">Not generated yet</span>
        )}
      </div>
      <div className="body">
        <h5>{item.name}</h5>
        <p>{item.prompt}</p>
      </div>
    </article>
  );
}
