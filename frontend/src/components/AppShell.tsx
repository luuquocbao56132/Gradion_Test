import type { SessionView } from '../types';

interface Props {
  session: SessionView;
  onSignOut: () => void;
  onHome: () => void;
  children: React.ReactNode;
}

export default function AppShell({ session, onSignOut, onHome, children }: Props) {
  const initials = (session.name || '?')
    .split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  return (
    <>
      <header className="gd-nav">
        <div className="gd-nav-inner">
          <button type="button" className="gd-nav-logo" onClick={onHome}>
            Book Illustration Studio
          </button>
          <div className="gd-nav-user">
            <span className="gd-nav-avatar" aria-hidden="true">{initials}</span>
            <span>{session.name}</span>
            <button type="button" className="gd-btn gd-btn-ghost gd-btn-sm" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="app-body">{children}</main>
    </>
  );
}
