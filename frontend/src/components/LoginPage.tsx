import { type FormEvent, useEffect, useRef, useState } from 'react';

import { ApiError, apiEnsureCsrfCookie, apiPost } from '../api/client';

type Props = {
  onAuthenticated: () => void;
};

export default function LoginPage({ onAuthenticated }: Props) {
  const usernameRef = useRef<HTMLInputElement | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    usernameRef.current?.focus();
    void apiEnsureCsrfCookie().catch(() => undefined);
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    setIsSubmitting(true);
    setError(null);

    try {
      await apiEnsureCsrfCookie();
      await apiPost<void>('/api/auth/session/', { username, password });
      onAuthenticated();
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 400) {
        setError('Identifiant ou mot de passe incorrect.');
      } else {
        setError('Connexion impossible pour le moment.');
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="login-screen">
      <section className="login-content" aria-labelledby="login-title">
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
            <h1 id="login-title">Connexion</h1>
            <p>Accédez à vos cultures et relevés.</p>
          </header>

          <label>
            Identifiant
            <input
              ref={usernameRef}
              autoComplete="username"
              disabled={isSubmitting}
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>

          <label>
            Mot de passe
            <input
              autoComplete="current-password"
              disabled={isSubmitting}
              required
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>

          {error ? <p className="login-error" role="alert">{error}</p> : null}

          <button className="login-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting ? 'Connexion...' : 'Se connecter'}
          </button>
        </form>
      </section>
    </main>
  );
}
