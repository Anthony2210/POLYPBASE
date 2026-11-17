import { useEffect, useMemo, useRef, useState } from 'react';
import { LockKeyhole } from 'lucide-react';
import { ApiError, apiPost, setActiveOrganizationContext } from '../../api/client';
import { recipientRequestOptions, sharingApi } from '../../api/boxSharing';
import { readForRecipient, readRecipientProfile, SHARING_SESSION_INVALIDATED } from '../../api/recipientSession';
import { useSharingResource } from '../../hooks/useSharingResource';
import { createTranslator, getStoredInterfaceLanguage, resolveLanguage, setStoredInterfaceLanguage, type Language, type Translator } from '../../i18n';
import type { UserProfile } from '../../types';
import { fieldText, navigateShared, parseInvitationToken, sharedRoute, shareDate, shareStateLabel, sharingError, snapshotSupported } from '../../utils/boxSharing';
import LoginPage from '../LoginPage';
import ScientificSnapshotView from './ScientificSnapshotView';

type ReaderProps = { profile: UserProfile; language: Language; t: Translator; onSessionLost: () => void };
export default function SharedAccessApp({ path }: { path: string }) {
  const route = sharedRoute(path);
  const [token, setToken] = useState(() => route === 'accept' ? parseInvitationToken(window.location.hash) : null);
  const [hidden, setHidden] = useState(document.visibilityState === 'hidden');
  const [sessionKey, setSessionKey] = useState(0);
  const [logoutError, setLogoutError] = useState(false);
  const session = useSharingResource(`recipient-session:${sessionKey}`, readRecipientProfile);
  const language = resolveLanguage(session.data?.interface_language ?? getStoredInterfaceLanguage());
  const t = useMemo(() => createTranslator(language), [language]);
  const invalidateSession = () => setSessionKey(value => value + 1);
  useEffect(() => {
    document.documentElement.lang = language;
    if (session.data) setStoredInterfaceLanguage(session.data.interface_language);
  }, [language, session.data]);
  useEffect(() => {
    const suspend = () => { setHidden(true); };
    const resume = () => { if (document.visibilityState !== 'hidden') { invalidateSession(); setHidden(false); } };
    const visibility = () => document.visibilityState === 'hidden' ? suspend() : resume();
    const fragment = () => {
      if (route !== 'accept' || window.location.hash === '#shared-content') return;
      setToken(parseInvitationToken(window.location.hash));
      invalidateSession();
    };
    window.addEventListener(SHARING_SESSION_INVALIDATED, invalidateSession);
    window.addEventListener('blur', suspend);
    window.addEventListener('focus', resume);
    window.addEventListener('pagehide', suspend);
    window.addEventListener('pageshow', resume);
    window.addEventListener('hashchange', fragment);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      window.removeEventListener(SHARING_SESSION_INVALIDATED, invalidateSession);
      window.removeEventListener('blur', suspend); window.removeEventListener('focus', resume);
      window.removeEventListener('pagehide', suspend); window.removeEventListener('pageshow', resume);
      window.removeEventListener('hashchange', fragment); document.removeEventListener('visibilitychange', visibility);
    };
  }, [route]);
  const unauthenticated = session.error instanceof ApiError && (session.error.status === 401 || session.error.status === 403);
  async function logout() {
    setHidden(true);
    setLogoutError(false);
    try {
      await apiPost('/api/auth/logout/', {}, recipientRequestOptions());
      setActiveOrganizationContext(null);
      // The invitation can be reopened from the original email after switching accounts.
      setToken(null);
      window.history.replaceState(null, '', path);
    } catch { setLogoutError(true); }
    finally { invalidateSession(); setHidden(false); }
  }
  if (unauthenticated && !hidden) return <>
    <div className="sharing-login-help"><p>{t(route === 'accept' ? 'shareInvitationSignIn' : 'shareReaderSignIn')}</p><p>{t('shareNoAccount')}</p></div>
    <LoginPage onAuthenticated={invalidateSession} t={t} skipOrganizationContext />
  </>;
  return <div className="sharing-access">
    <a className="sharing-skip" href="#shared-content" onClick={event => {
          event.preventDefault();
          document.getElementById('shared-content')?.focus();
        }}>{t('shareSkip')}</a>
    <header className="sharing-access-header">
      <a className="sharing-brand" href="/profile" onClick={event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigateShared('/profile'); }}><img src="/jellyfish.svg" alt="" />Polypbase</a>
      <nav className="sharing-actions" aria-label={t('account')}>
        <a href="/shared-boxes" onClick={event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigateShared('/shared-boxes'); }}>{t('shareReceived')}</a>
        {session.data ? <><span className="sharing-account"><bdi>{session.data.email}</bdi></span><button className="secondary-button" type="button" onClick={() => void logout()}>{t('logoutAction')}</button></> : null}
      </nav>
    </header>
    <main className="sharing-access-main" id="shared-content" tabIndex={-1}>
      {logoutError ? <p className="inline-error" role="alert">{t('logoutError')}</p> : null}
      {hidden || session.loading ? <p role="status">{t('shareCheckingAccess')}</p>
        : session.error ? <SharingFailure error={session.error} onRetry={session.refresh} t={t} />
        : session.data ? <div key={`${sessionKey}:${session.data.id}:${session.data.email}`}>
          {route === 'list' ? <ReceivedShares profile={session.data} language={language} t={t} onSessionLost={invalidateSession} />
            : route === 'accept' ? <AcceptInvitation token={token} clearToken={() => { setToken(null); window.history.replaceState(null, '', '/shared-boxes/accept'); }} profile={session.data} language={language} t={t} onSessionLost={invalidateSession} />
              : route === 'invalid' ? <SharingFailure error={new ApiError(404, '')} onRetry={() => navigateShared('/shared-boxes')} t={t} />
                : <SharedBoxReader id={route} profile={session.data} language={language} t={t} onSessionLost={invalidateSession} />}
        </div> : null}
    </main>
  </div>;
}
function useSessionFailure(error: unknown, onSessionLost: () => void) {
  const lost = useRef(onSessionLost);
  lost.current = onSessionLost;
  useEffect(() => { if (error instanceof ApiError && error.status === 401) lost.current(); }, [error]);
}
export function SharingFailure({ error, onRetry, t }: { error: unknown; onRetry: () => void; t: Translator }) {
  return <section className="sharing-unavailable" role="alert"><h1>{t('shareUnavailableTitle')}</h1><p>{sharingError(error, t)}</p><button className="secondary-button" type="button" onClick={onRetry}>{t('reloadAction')}</button></section>;
}
export function ReceivedShares({ profile, language, t, onSessionLost }: ReaderProps) {
  const result = useSharingResource(`received:${profile.id}:${profile.email}`, signal => readForRecipient(profile, signal, () => sharingApi.received(signal)));
  const [page, setPage] = useState(0);
  useSessionFailure(result.error, onSessionLost);
  return <section className="sharing-received"><header className="sharing-toolbar"><h1>{t('shareReceived')}</h1><button className="secondary-button" type="button" onClick={result.refresh}>{t('reloadAction')}</button></header>
    <p className="sharing-help">{t('shareReceivedHelp')}</p>
    {result.loading ? <p role="status">{t('shareCheckingAccess')}</p> : null}
    {result.error ? <SharingFailure error={result.error} onRetry={result.refresh} t={t} /> : null}
    {result.data?.shares.length === 0 ? <p className="sharing-empty">{t('shareNoReceived')}</p> : null}
    <ul className="sharing-rows">{result.data?.shares.slice(page * 25, (page + 1) * 25).map(share => <li className="sharing-row" key={share.id}>
      <div className="sharing-row-main"><div><strong><bdi>{share.source_institution.name}</bdi></strong><small>{t(share.mode === 'live' ? 'shareLive' : 'shareFrozen')}</small></div><div><strong>{shareStateLabel(share.state, t)}</strong><small>{t('shareAccessExpiry')}: {shareDate(share.access_expires_at, language)}</small></div></div>
      <a className="secondary-button" href={`/shared-boxes/${share.id}`} onClick={event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigateShared(`/shared-boxes/${share.id}`); }}>{t('shareRead')}</a>
    </li>)}</ul>
    {result.data && result.data.shares.length > 25 ? <nav className="sharing-actions" aria-label={t('shareReceivedPages')}><button className="secondary-button" type="button" disabled={page === 0} onClick={() => setPage(value => value - 1)}>{t('sharePrevious')}</button><span>{t('sharePage').replace('{page}', String(page + 1))}</span><button className="secondary-button" type="button" disabled={(page + 1) * 25 >= result.data.shares.length} onClick={() => setPage(value => value + 1)}>{t('shareNext')}</button></nav> : null}
  </section>;
}
function AcceptInvitation({ token, clearToken, profile, language, t, onSessionLost }: ReaderProps & { token: string | null; clearToken: () => void }) {
  const info = useSharingResource(`invitation:${profile.id}:${profile.email}`, signal => token ? readForRecipient(profile, signal, () => sharingApi.inspect(token, signal)) : Promise.reject(new ApiError(404, '', { code: 'invitation_unavailable' })));
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const controller = useRef<AbortController>();
  useEffect(() => () => controller.current?.abort(), []);
  useSessionFailure(info.error ?? error, onSessionLost);
  async function accept() {
    if (!token || !info.data || lock.current) return;
    lock.current = true; setBusy(true); setError(undefined);
    const request = new AbortController(); controller.current = request;
    try {
      const result = await readForRecipient(profile, request.signal, async () => {
        const accepted = await sharingApi.accept(token, request.signal);
        if (!request.signal.aborted) clearToken();
        return accepted;
      });
      if (request.signal.aborted) return;
      navigateShared(`/shared-boxes/${result.share.id}`, true);
    } catch (failure) { if (!request.signal.aborted) setError(failure); }
    finally { lock.current = false; if (!request.signal.aborted) setBusy(false); }
  }
  return <section className="sharing-invitation"><h1>{t('shareInvitationTitle')}</h1>
    {info.loading ? <p role="status">{t('shareCheckingAccess')}</p> : null}
    {info.error ? <SharingFailure error={info.error} onRetry={info.refresh} t={t} /> : null}
    {info.data ? <>
      <p>{t('shareInvitationFrom').replace('{institution}', info.data.invitation.source_institution.name)}</p>
      <p>{t('shareInvitationReadOnly')}</p>
      <dl className="sharing-facts"><div><dt>{t('shareRecipient')}</dt><dd><bdi>{profile.email}</bdi></dd></div>
        <div><dt>{t('shareMode')}</dt><dd>{t(info.data.invitation.mode === 'live' ? 'shareLive' : 'shareFrozen')}</dd></div>
        <div><dt>{t('shareInvitationExpiry')}</dt><dd>{shareDate(info.data.invitation.invitation_expires_at, language)}</dd></div>
        <div><dt>{t('shareAccessExpiry')}</dt><dd>{shareDate(info.data.invitation.access_expires_at, language)}</dd></div></dl>
      <p>{t(info.data.invitation.mode === 'live' ? 'shareLiveHelp' : 'shareFrozenHelp')}</p>
      <button className="primary-button" type="button" disabled={busy || !token} onClick={() => void accept()}>{t(busy ? 'saving' : 'shareAccept')}</button>
    </> : null}
    {error ? <p className="inline-error" role="alert">{sharingError(error, t)}</p> : null}
    <p className="sharing-help">{t('shareNoAccount')}</p>
  </section>;
}
function SharedBoxReader({ id, profile, language, t, onSessionLost }: ReaderProps & { id: string }) {
  const result = useSharingResource(`reader:${profile.id}:${profile.email}:${id}`, signal => readForRecipient(profile, signal, () => sharingApi.read(id, signal)));
  useSessionFailure(result.error, onSessionLost);
  useEffect(() => {
    const interval = window.setInterval(result.refresh, 60000);
    const expires = result.data ? Date.parse(result.data.share.access_expires_at) - Date.now() + 1 : null;
    const timer = expires !== null ? window.setTimeout(result.refresh, Math.max(0, Math.min(expires, 2147483647))) : undefined;
    return () => { window.clearInterval(interval); window.clearTimeout(timer); };
  }, [result.refresh, result.data]);
  if (result.error) return <SharingFailure error={result.error} onRetry={result.refresh} t={t} />;
  if (!result.data) return <p role="status">{t('shareCheckingAccess')}</p>;
  const { share, content, snapshot } = result.data;
  if (!snapshotSupported(snapshot)) return <p className="inline-error" role="alert">{t('shareUnsupported')}</p>;
  return <article className="sharing-reader">
    <aside className="sharing-readonly-banner" aria-label={t('shareReadOnly')}>
      <LockKeyhole size={21} aria-hidden="true" />
      <div><strong>{t('shareReadOnly')}</strong><p>{t('shareSharedBy').replace('{institution}', share.source_institution.name)}</p><small>{t('shareReaderScope')}</small></div>
    </aside>
    <header className="sharing-toolbar"><div><h1>{snapshot.disclosed.includes('source.box_code') ? fieldText(snapshot.source?.box_code, t) : t('shareSharedBox')}</h1><p>{t(share.mode === 'live' ? 'shareLive' : 'shareFrozen')}</p></div>
      <button className="secondary-button" type="button" onClick={result.refresh}>{t('reloadAction')}</button></header>
    <p className="sharing-help">{t(share.mode === 'live' ? 'shareLiveHelp' : 'shareFrozenHelp')}</p>
    <dl className="sharing-facts"><div><dt>{t('shareAccessExpiry')}</dt><dd>{shareDate(share.access_expires_at, language)}</dd></div>
      <div><dt>{t(content.kind === 'frozen_capture' ? 'shareCapturedAt' : 'shareProjectedAt')}</dt><dd>{shareDate(content.kind === 'frozen_capture' ? content.captured_at : content.projected_at, language)}</dd></div>
      <div><dt>{t('shareRevision')}</dt><dd>{share.policy_revision}</dd></div></dl>
    {content.kind === 'live_projection' ? <p className="sharing-help">{t('shareNonAtomic')}</p> : <details><summary>{t('shareCaptureHash')}</summary><p className="sharing-hash">{content.content_sha256}</p></details>}
    <ScientificSnapshotView key={`${id}:${share.policy_revision}`} snapshot={snapshot} t={t} />
  </article>;
}
