import { type FormEvent, useEffect, useRef, useState } from 'react';

import { ApiError, apiEnsureCsrfCookie, apiPost } from '../api/client';

type Props = {
  uid: string;
  token: string;
  onDone: () => void;
};

/**
 * Screen behind the link emailed by the "forgot password" flow.
 *
 * The link carries the account id and a single-use token; this page only
 * collects the new password and hands the three back to the API, which is what
 * validates the token and the password strength.
 */
export default function PasswordResetPage({ uid, token, onDone }: Props) {
  const passwordRef = useRef<HTMLInputElement | null>(null);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isDone, setIsDone] = useState(false);

  useEffect(() => {
    passwordRef.current?.focus();
    void apiEnsureCsrfCookie().catch(() => undefined);
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    if (password !== confirmation) {
      setError('Les deux mots de passe ne correspondent pas.');
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      await apiEnsureCsrfCookie();
      await apiPost<void>('/api/auth/password-reset/confirm/', { uid, token, password });
      setIsDone(true);
    } catch (requestError) {
      setError(getResetError(requestError));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="login-screen">
      <section className="login-content" aria-labelledby="reset-title">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true">
            <img src="/jellyfish.svg" alt="" />
          </span>
          <div>
            <p className="eyebrow">Polypbase</p>
            <strong>Suivi laboratoire</strong>
          </div>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <header>
            <h1 id="reset-title">Nouveau mot de passe</h1>
            <p>Choisissez un mot de passe personnel pour votre compte.</p>
          </header>

          {isDone ? (
            <>
              <p className="login-success" role="status">
                Votre mot de passe a été enregistré. Vous pouvez vous connecter.
              </p>
              <button className="login-submit" type="button" onClick={onDone}>
                Aller à la connexion
              </button>
            </>
          ) : (
            <>
              <label>
                Nouveau mot de passe
                <input
                  ref={passwordRef}
                  autoComplete="new-password"
                  disabled={isSubmitting}
                  required
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>

              <label>
                Confirmer le mot de passe
                <input
                  autoComplete="new-password"
                  disabled={isSubmitting}
                  required
                  type="password"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                />
              </label>

              {error ? <p className="login-error" role="alert">{error}</p> : null}

              <button className="login-submit" type="submit" disabled={isSubmitting}>
                {isSubmitting ? 'Enregistrement...' : 'Enregistrer'}
              </button>
              <button className="login-link" type="button" onClick={onDone}>
                Retour à la connexion
              </button>
            </>
          )}
        </form>
      </section>
    </main>
  );
}

function getResetError(error: unknown) {
  // The client already lifts the useful sentence out of the response body:
  // either why the link is unusable, or why the password was refused (too
  // short, too common, entirely numeric...).
  if (error instanceof ApiError && error.status === 400) {
    return error.message;
  }

  return 'Enregistrement impossible pour le moment.';
}
