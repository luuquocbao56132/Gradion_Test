import { useState } from 'react';
import * as api from '../api';
import StateMessage from './StateMessage';

/**
 * A permanent disclosure panel, not a modal. The book is reference material you
 * read beside the prompts derived from it, and a panel always present in the
 * layout cannot have its affordance vanish the way the demo's does at
 * app-demo.html:700 (design 10.6).
 */
export default function BookTextPanel({ projectId, excerpt }: {
  projectId: string; excerpt: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setText(await api.getBook(projectId));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const expand = async () => {
    setOpen(true);
    if (text === null && !loading) await load();
  };

  return (
    <section className="side-note book-panel">
      <h5>Book text</h5>
      {!open && <p className="excerpt">{excerpt}</p>}
      {open && loading && <StateMessage kind="loading" label="Loading the full text…" />}
      {open && error && <StateMessage kind="error" message={error} onRetry={load} />}
      {open && text !== null && <pre className="book-full">{text}</pre>}
      <button type="button" className="gd-btn gd-btn-ghost gd-btn-sm"
              onClick={open ? () => setOpen(false) : expand}>
        {open ? 'Collapse' : 'Read full text →'}
      </button>
    </section>
  );
}
