import { useState } from 'react';

const EMAIL_RE = /^[^@\s]+@[^@\s.]+\.[^@\s]+$/;

interface Props {
  onSubmit: (name: string, email: string) => Promise<void>;
  error: string | null;
  busy: boolean;
}

/**
 * Validation is duplicated on purpose: here for UX, and again on the server as
 * the trust boundary. The backend rejects a blank name independently.
 */
export default function SignIn({ onSubmit, error, busy }: Props) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedEmail = email.trim().toLowerCase();
    if (!trimmedName) return setLocalError('Enter your name to continue.');
    if (!EMAIL_RE.test(trimmedEmail)) return setLocalError('Enter a valid email address.');
    setLocalError(null);
    await onSubmit(trimmedName, trimmedEmail);
  };

  const message = localError ?? error;

  return (
    <div className="center-page">
      <form className="auth-card" onSubmit={submit} noValidate>
        <h1>Book Illustration Studio</h1>
        <p className="lede">Enter your details to start or resume an illustration project.</p>

        <div className="gd-field">
          <label htmlFor="signin-name">Full name <span className="req">*</span></label>
          <input id="signin-name" value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Mira Hassan" autoComplete="name" />
        </div>

        <div className="gd-field">
          <label htmlFor="signin-email">Email <span className="req">*</span></label>
          <input id="signin-email" type="email" value={email} autoComplete="email"
                 onChange={(e) => setEmail(e.target.value)} placeholder="mira@example.com" />
        </div>

        {message && <p className="gd-field err" role="alert">{message}</p>}

        <button type="submit" className="gd-btn gd-btn-primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Continue'}
        </button>
        <p className="meta">
          No password — this is a lightweight identity check. Using an email that already
          has projects resumes them exactly where you left off.
        </p>
      </form>
    </div>
  );
}
