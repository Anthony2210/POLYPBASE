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
  // "Forgot password" runs inside this page rather than on its own route: the
  // user only leaves it through the link received by email.
  const [isForgotMode, setIsForgotMode] = useState(false);
  const [resetEmail, setResetEmail] = useState('');
  const [resetSent, setResetSent] = useState(false);

  useEffect(() => {
    usernameRef.current?.focus();
    void apiEnsureCsrfCookie().catch(() => undefined);
  }, []);

  async function handleForgotSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    setIsSubmitting(true);
    setError(null);

    try {
      await apiEnsureCsrfCookie();
      await apiPost<void>('/api/auth/password-reset/', { email: resetEmail });
      // The API answers the same way whether or not the address is known, so the
      // confirmation must stay just as neutral here.
      setResetSent(true);
    } catch {
      setError("Envoi impossible pour le moment. Réessayez dans un instant.");
    } finally {
      setIsSubmitting(false);
    }
  }

  function backToLogin() {
    setIsForgotMode(false);
    setResetSent(false);
    setResetEmail('');
    setError(null);
  }

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

        {isForgotMode ? (
          <form className="login-form" onSubmit={handleForgotSubmit}>
            <header>
              <h1 id="login-title">Mot de passe oublié</h1>
              <p>Indiquez votre adresse e-mail pour recevoir un lien de réinitialisation.</p>
            </header>

            {resetSent ? (
              <>
                <p className="login-success" role="status">
                  Si un compte est associé à cette adresse, un e-mail vient d'être envoyé.
                  Le lien est valable une heure et ne peut servir qu'une fois.
                </p>
                <button className="login-submit" type="button" onClick={backToLogin}>
                  Retour à la connexion
                </button>
              </>
            ) : (
              <>
                <label>
                  Adresse e-mail
                  <input
                    autoComplete="email"
                    disabled={isSubmitting}
                    required
                    type="email"
                    value={resetEmail}
                    onChange={(event) => setResetEmail(event.target.value)}
                  />
                </label>

                {error ? <p className="login-error" role="alert">{error}</p> : null}

                <button className="login-submit" type="submit" disabled={isSubmitting}>
                  {isSubmitting ? 'Envoi...' : 'Envoyer le lien'}
                </button>
                <button className="login-link" type="button" onClick={backToLogin}>
                  Retour à la connexion
                </button>
              </>
            )}
          </form>
        ) : (
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

          <button
            className="login-link"
            type="button"
            onClick={() => {
              setIsForgotMode(true);
              setError(null);
            }}
          >
            Mot de passe oublié ?
          </button>
        </form>
        )}
      </section>
    </main>
  );
}
