import { useEffect, useState } from 'react';
import AppShell from './components/AppShell';
import NewProject from './components/NewProject';
import ProjectList from './components/ProjectList';
import SignIn from './components/SignIn';
import StateMessage from './components/StateMessage';
import { useSession } from './hooks/useSession';

type Route =
  | { name: 'list' } | { name: 'new' } | { name: 'detail'; id: string };

function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, '');
  if (path === 'projects/new') return { name: 'new' };
  const match = path.match(/^projects\/([A-Za-z0-9]+)$/);
  return match ? { name: 'detail', id: match[1] } : { name: 'list' };
}

export function navigate(hash: string) { window.location.hash = hash; }

export default function App() {
  const { session, status, error, isSigningIn, signIn, signOut, retry } = useSession();
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onHashChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  if (status === 'loading') return <StateMessage kind="loading" label="Loading…" />;
  if (status === 'error') {
    return <StateMessage kind="error" message={error ?? 'Could not reach the server.'}
                         onRetry={retry} />;
  }
  if (!session) return <SignIn onSubmit={signIn} error={error} busy={isSigningIn} />;

  return (
    <AppShell session={session} onSignOut={signOut} onHome={() => navigate('#/projects')}>
      {route.name === 'list' && (
        <ProjectList onOpen={(id) => navigate(`#/projects/${id}`)}
                     onNew={() => navigate('#/projects/new')} />
      )}
      {route.name === 'new' && (
        <NewProject onCreated={(id) => navigate(`#/projects/${id}`)}
                    onCancel={() => navigate('#/projects')} />
      )}
      {route.name === 'detail' && <p>Project {route.id}</p>}
    </AppShell>
  );
}
