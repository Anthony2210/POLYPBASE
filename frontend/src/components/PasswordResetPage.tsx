import { type FormEvent, useEffect, useRef, useState } from 'react';

import { ApiError, apiEnsureCsrfCookie, apiPost } from '../api/client';
import type { Translator } from '../i18n';

type Props = {
  uid: string;
  token: string;
  onDone: () => void;
  t: Translator;
};

/**
 * Screen behind the link emailed by the "forgot password" flow.
 *
 * The link carries the account id and a single-use token; this page only
 * collects the new password and hands the three back to the API, which is what
 * validates the token and the password strength.
 */
export default function PasswordResetPage({ uid, token, onDone, t }: Props) {
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
      setError(t('passwordResetMismatch'));
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      await apiEnsureCsrfCookie();
      await apiPost<void>('/api/auth/password-reset/confirm/', { uid, token, password });
      setIsDone(true);
    } catch (requestError) {
      setError(getResetError(requestError, t));
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
            <strong>{t('laboratoryTracking')}</strong>
          </div>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <header>
            <h1 id="reset-title">{t('passwordResetTitle')}</h1>
            <p>{t('passwordResetIntro')}</p>
          </header>

          {isDone ? (
            <>
              <p className="login-success" role="status">
                {t('passwordResetDone')}
              </p>
              <button className="login-submit" type="button" onClick={onDone}>
                {t('passwordResetGoToLogin')}
              </button>
            </>
          ) : (
            <>
              <label>
                {t('passwordResetNewPassword')}
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
                {t('passwordResetConfirmPassword')}
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
                {isSubmitting ? t('passwordResetSaving') : t('passwordResetSave')}
              </button>
              <button className="login-link" type="button" onClick={onDone}>
                {t('backToLogin')}
              </button>
            </>
          )}
        </form>
      </section>
    </main>
  );
}

function getResetError(error: unknown, t: Translator) {
  // The client already lifts the useful sentence out of the response body:
  // either why the link is unusable, or why the password was refused (too
  // short, too common, entirely numeric...).
  if (error instanceof ApiError && error.status === 400) {
    return error.message;
  }

  return t('passwordResetUnavailable');
}
