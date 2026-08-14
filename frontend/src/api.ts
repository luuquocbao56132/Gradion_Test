import type {
  BookView, ProjectListItem, ProjectView, RunOutcome, SessionView, StepName,
} from './types';

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body?.error?.message ?? body?.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export async function getSession(): Promise<SessionView | null> {
  const response = await fetch('/api/session');
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as SessionView;
}

export const createSession = (name: string, email: string) =>
  request<SessionView>('/api/session', { method: 'POST', body: JSON.stringify({ name, email }) });

export const deleteSession = () => request<void>('/api/session', { method: 'DELETE' });

export const listProjects = () => request<ProjectListItem[]>('/api/projects');

export const createProject = (title: string, bookText: string) =>
  request<ProjectView>('/api/projects', {
    method: 'POST', body: JSON.stringify({ title, book_text: bookText }),
  });

export const getProject = (id: string) => request<ProjectView>(`/api/projects/${id}`);

export const getBook = async (id: string) =>
  (await request<BookView>(`/api/projects/${id}/book`)).text;

/**
 * 202 and 409 are both truth-carrying outcomes, never errors. A rejected
 * duplicate should look like a UI that was already correct (design 8, 10.5).
 */
export async function runStep(id: string, step: StepName, style?: string): Promise<RunOutcome> {
  const body = style ? { step, style } : { step };
  const response = await fetch(`/api/projects/${id}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (response.status === 202) return { ok: true, project: (await response.json()).project };
  if (response.status === 409) {
    return { ok: false, conflict: true, project: (await response.json()).project };
  }
  throw new Error(await readError(response));
}
