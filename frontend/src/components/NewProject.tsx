import { useState } from 'react';
import * as api from '../api';

/**
 * Both assessment 4.4 input modes go through one endpoint: the file input reads
 * with FileReader.readAsText into the same textarea the paste path fills, so
 * there is one submit path and one thing to validate. The .txt upload is a
 * frontend input mode, not a second transport (design 8).
 *
 * No invented size limits: the File API's documented ceiling is 2 GB and we come
 * nowhere near it, so there is no constraint to encode (design 10.7).
 */
export default function NewProject({ onCreated, onCancel }: {
  onCreated: (id: string) => void; onCancel: () => void;
}) {
  const [title, setTitle] = useState('');
  const [bookText, setBookText] = useState('');
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const readFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setBookText(String(reader.result ?? ''));
      setFileName(file.name);
    };
    reader.onerror = () => setError('That file could not be read.');
    reader.readAsText(file);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return setError('Give the project a title.');
    if (!bookText.trim()) {
      return setError('Provide the book text — paste it or upload a .txt file.');
    }
    setError(null);
    setBusy(true);
    try {
      const project = await api.createProject(title.trim(), bookText);
      onCreated(project.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="app-body narrow" onSubmit={submit} noValidate>
      <button type="button" className="back-link" onClick={onCancel}>← Back to projects</button>
      <h2>Start a new illustration project</h2>
      <p className="meta">Give it a title, then paste the book’s text or upload a .txt file.</p>

      <div className="gd-field">
        <label htmlFor="new-title">Project title <span className="req">*</span></label>
        <input id="new-title" value={title} onChange={(e) => setTitle(e.target.value)}
               placeholder="e.g. The Wind in the Willows — cottage-core" />
      </div>

      <div className="gd-field">
        <label htmlFor="new-file">Choose a .txt file</label>
        <input id="new-file" type="file" accept=".txt,text/plain" onChange={readFile} />
        {fileName && <p className="meta">✓ {fileName} loaded</p>}
      </div>

      <div className="divider-or">or paste text</div>

      <div className="gd-field">
        <label htmlFor="new-book">Book text <span className="req">*</span></label>
        <textarea id="new-book" rows={8} value={bookText}
                  onChange={(e) => setBookText(e.target.value)}
                  placeholder="Once upon a time, in a small burrow by the river…" />
      </div>

      {error && <p className="gd-field err" role="alert">{error}</p>}

      <button type="submit" className="gd-btn gd-btn-primary" disabled={busy}>
        {busy ? 'Creating…' : 'Create project'}
      </button>
    </form>
  );
}
