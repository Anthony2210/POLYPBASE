import { useEffect, useRef, useState } from 'react';
import { sharingApi, type AdminShare } from '../../api/boxSharing';
import { readForSourceAdmin } from '../../api/sourceSharingSession';
import { SHARING_SESSION_INVALIDATED } from '../../api/recipientSession';
import { useSharingResource } from '../../hooks/useSharingResource';
import type { Language, Translator } from '../../i18n';
import type { UserProfile } from '../../types';
import { navigateShared, selectorLabel, shareDate, shareStateLabel, sharingError, utcInput, validExpiry } from '../../utils/boxSharing';
import { formatReadableUserIdentity } from '../../utils/userIdentity';
import { useSharingConfirmation } from '../../hooks/useSharingConfirmation';
import BoxTrackingPreview from '../BoxTrackingPreview';
import ShareEditor from './ShareEditor';
import SourceBoxLookup, { type SourceBox } from './SourceBoxLookup';
import { ExpiryField } from './SharingControls';

const deliveryKeys = { pending: 'shareDeliveryPending', sent: 'shareDeliverySent', failed: 'shareDeliveryFailed', uncertain: 'shareDeliveryUncertain' } as const;
export default function BoxSharingAdminSection({ organizationId, profile, language, onOpenBox, t }: {
  organizationId: number; profile: UserProfile; language: Language; onOpenBox: (id: number, code: string) => void; t: Translator;
}) {
  // Sharing deliberately has no technical-superuser shortcut.
  const allowed = profile.memberships.some(item => item.organization.id === organizationId && item.role === 'admin');
  return <section className="admin-section sharing-admin" id="admin-transfers">
    <header className="admin-section-heading"><h2>{t('shareWorkspace')}</h2><p>{t('shareWorkspaceHelp')}</p></header>
    <nav className="sharing-actions" aria-label={t('shareAreas')}>
      <span className="sharing-current">{t('shareOutgoing')}</span>
      <a className="secondary-button" href="/shared-boxes" onClick={event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigateShared('/shared-boxes'); }}>{t('shareReceived')}</a>
    </nav>
    {allowed ? <SourceSessionBoundary key={`${profile.id}:${organizationId}`} profile={profile} organizationId={organizationId} language={language} onOpenBox={onOpenBox} t={t} />
      : <p className="inline-error" role="alert">{t('shareErrorPermission')}</p>}
  </section>;
}
type SourceWorkspaceProps = { profile: UserProfile; organizationId: number; language: Language; onOpenBox: (id: number, code: string) => void; t: Translator };
function SourceSessionBoundary(props: SourceWorkspaceProps) {
  const [hidden, setHidden] = useState(document.visibilityState === 'hidden');
  const [epoch, setEpoch] = useState(0);
  const [invalidated, setInvalidated] = useState(false);
  const session = useSharingResource(`source-session:${props.profile.id}:${props.organizationId}:${epoch}`, signal =>
    readForSourceAdmin(props.profile, props.organizationId, signal, () => Promise.resolve(true)));
  useEffect(() => {
    const suspend = () => setHidden(true);
    const invalidate = () => setInvalidated(true);
    const resume = () => { if (document.visibilityState !== 'hidden') { setEpoch(value => value + 1); setHidden(false); } };
    const visibility = () => document.visibilityState === 'hidden' ? suspend() : resume();
    window.addEventListener(SHARING_SESSION_INVALIDATED, invalidate);
    window.addEventListener('blur', suspend); window.addEventListener('focus', resume);
    window.addEventListener('pagehide', suspend); window.addEventListener('pageshow', resume);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      window.removeEventListener(SHARING_SESSION_INVALIDATED, invalidate);
      window.removeEventListener('blur', suspend); window.removeEventListener('focus', resume);
      window.removeEventListener('pagehide', suspend); window.removeEventListener('pageshow', resume);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);
  if (invalidated || session.error) return <div role="alert"><p className="inline-error">{invalidated ? props.t('shareErrorPermission') : sharingError(session.error, props.t)}</p>
    <button className="secondary-button" type="button" onClick={() => window.location.reload()}>{props.t('reloadAction')}</button></div>;
  if (hidden || session.loading) return <p role="status">{props.t('shareCheckingAccess')}</p>;
  return <OutgoingShares key={epoch} {...props} />;
}
function OutgoingShares({ profile, organizationId, language, onOpenBox, t }: SourceWorkspaceProps) {
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState<SourceBox | null>(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [editor, setEditor] = useState<AdminShare | 'new' | null>(null);
  const [reissue, setReissue] = useState<number | null>(null);
  const [invitationExpiry, setInvitationExpiry] = useState('');
  const [mutationError, setMutationError] = useState<unknown>();
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const task = useRef(false);
  const alive = useRef(true);
  const lifecycle = useRef(new AbortController());
  useEffect(() => {
    alive.current = true; lifecycle.current = new AbortController();
    return () => { alive.current = false; lifecycle.current.abort(); };
  }, []);
  const { confirmAction, confirmActionModal } = useSharingConfirmation();
  const options = useSharingResource(`options:${organizationId}`, signal => readForSourceAdmin(profile, organizationId, signal, () => sharingApi.options(organizationId, signal)));
  const page = useSharingResource(`shares:${organizationId}:${offset}:${filter?.id}`, signal => readForSourceAdmin(profile, organizationId, signal, () => sharingApi.list(organizationId, offset, filter?.id, signal)));
  const [updates, setUpdates] = useState<Record<number, AdminShare>>({});
  const update = (share: AdminShare) => { setUpdates(current => ({ ...current, [share.id]: share })); setReissue(null); };
  async function mutate(share: AdminShare, action: 'revoke' | 'reissue') {
    if (task.current) return;
    setMutationError(undefined);
    if (action === 'reissue' && (!options.data || !validExpiry(invitationExpiry, options.data.lifetime_policy.invitation_max_lifetime_seconds)
      || Date.parse(utcInput(invitationExpiry)!) > Date.parse(share.access_expires_at))) { setMutationError({ dates: true }); return; }
    task.current = true;
    setBusy(true);
    try {
      const confirmed = await confirmAction({ title: t(action === 'revoke' ? 'shareRevoke' : 'shareReissue'),
        message: t(action === 'revoke' ? 'shareRevokeHelp' : 'shareReissueHelp'),
        confirmLabel: t(action === 'revoke' ? 'shareRevoke' : 'shareReissue'), cancelLabel: t('cancel'), variant: action === 'revoke' ? 'danger' : 'default',
        details: [{ label: t('shareSelectedBox'), value: share.box.global_code }, { label: t('shareRecipient'), value: share.recipient_email },
          { label: t('shareAccessExpiry'), value: shareDate(share.access_expires_at, language) },
          ...(action === 'reissue' ? [{ label: t('shareInvitationExpiry'), value: shareDate(utcInput(invitationExpiry), language) }] : [])],
      });
      if (!confirmed || !alive.current) return;
      const result = await readForSourceAdmin(profile, organizationId, lifecycle.current.signal, () => action === 'revoke'
        ? sharingApi.revoke(organizationId, share.id) : sharingApi.reissue(organizationId, share.id, utcInput(invitationExpiry)!));
      if (alive.current) { update(result.share); setNotice(t(action === 'revoke' ? 'shareRevokedSuccess' : 'shareSaved')); }
    } catch (error) { if (alive.current) setMutationError(error); }
    finally { task.current = false; if (alive.current) setBusy(false); }
  }
  const refresh = () => { setUpdates({}); setNotice(''); setMutationError(undefined); options.refresh(); page.refresh(); };
  if (options.error || page.error) return <div role="alert"><p className="inline-error">{sharingError(options.error ?? page.error, t)}</p><button className="secondary-button" type="button" onClick={refresh}>{t('reloadAction')}</button></div>;
  if (!options.data) return <p role="status">{t('loading')}</p>;
  return <>
    {!options.data.lifetime_policy.configured ? <p className="sharing-notice" role="status">{t('shareErrorConfiguration')}</p> : null}
    <div className="sharing-toolbar">
      <h3>{t('shareOutgoing')}</h3>
      <div className="sharing-actions">
        <button className="secondary-button" type="button" disabled={busy || editor !== null} onClick={() => setFilterOpen(value => !value)} aria-expanded={filterOpen}>{t('shareFilterBox')}</button>
        <button className="secondary-button" type="button" disabled={busy || editor !== null} onClick={refresh}>{t('reloadAction')}</button>
        <button className="primary-button" type="button" disabled={!options.data.lifetime_policy.configured || busy || editor !== null || options.data.schema_version !== '1.0'} onClick={() => { setEditor('new'); setNotice(''); }}>{t('shareCreate')}</button>
      </div>
    </div>
    {filterOpen ? <div className="sharing-filter"><SourceBoxLookup profile={profile} organizationId={organizationId} selected={filter} onSelect={box => { setFilter(box); setOffset(0); setUpdates({}); }} t={t} /></div> : null}
    {editor !== null ? <ShareEditor key={editor === 'new' ? 'new' : `${editor.id}:${editor.policy_revision}`} profile={profile} organizationId={organizationId} options={options.data}
      share={editor === 'new' ? undefined : editor} language={language} t={t} onCancel={() => setEditor(null)}
      onReload={editor === 'new' ? undefined : async () => { const result = await readForSourceAdmin(profile, organizationId, lifecycle.current.signal, () => sharingApi.detail(organizationId, editor.id)); if (alive.current) { update(result.share); setEditor(result.share); } }}
      onSaved={share => { setEditor(null); update(share); setNotice(t('shareSaved')); if (editor === 'new') { setFilter(null); setOffset(0); page.refresh(); } }} /> : null}
    <p role="status">{notice}</p>
    {mutationError ? <p className="inline-error" role="alert">{typeof mutationError === 'object' && mutationError !== null && 'dates' in mutationError ? t('shareErrorDates') : sharingError(mutationError, t)}</p> : null}
    <p className="sharing-help">{t('shareTrackingHelp')}</p>
    {page.loading ? <p role="status">{t('loading')}</p> : null}
    {page.data?.shares.length === 0 ? <p className="sharing-empty">{t(filter ? 'shareNoMatchingOutgoing' : 'shareNoOutgoing')}</p> : null}
    <ul className="sharing-rows">{page.data?.shares.map(original => {
      const share = updates[original.id] ?? original;
      return <li key={share.id} className="sharing-row">
        <div className="sharing-row-main"><div>
          <BoxTrackingPreview boxId={share.box.id} code={share.box.global_code} onOpenBox={onOpenBox} language={language} t={t} />
          <p><bdi>{share.recipient_email}</bdi></p>
        </div><div><strong>{shareStateLabel(share.state, t)}</strong><small>{t(share.mode === 'live' ? 'shareLive' : 'shareFrozen')}</small><small>{t('shareAccessExpiry')}: {shareDate(share.access_expires_at, language)}</small></div></div>
        <div className="sharing-delivery"><span>{t(deliveryKeys[share.invitation.delivery.status])}</span><small>{t(share.invitation.delivery.status === 'sent' ? 'shareSentHelp' : share.invitation.delivery.status === 'uncertain' ? 'shareUncertainHelp' : 'shareDeliveryHelp')}</small></div>
        <details><summary>{t('shareTracking')}</summary><dl className="sharing-facts">
          <div><dt>{t('shareDisclosure')}</dt><dd>{share.selectors.map(name => selectorLabel(name, t)).join(', ')}</dd></div>
          <div><dt>{t('shareInvitationExpiry')}</dt><dd>{shareDate(share.invitation.expires_at, language)}</dd></div>
          <div><dt>{t('shareAcceptedAt')}</dt><dd>{shareDate(share.invitation.accepted_at, language)}</dd></div>
          <div><dt>{t('shareCreatedAt')}</dt><dd>{shareDate(share.created_at, language)}{share.created_by ? ` · ${formatReadableUserIdentity(share.created_by)}` : ''}</dd></div>
          <div><dt>{t('shareRevokedAt')}</dt><dd>{shareDate(share.revoked_at, language)}</dd></div>
          <div><dt>{t('shareRevision')}</dt><dd>{share.policy_revision}</dd></div>
          <div><dt>{t('shareDeliveryUpdated')}</dt><dd>{shareDate(share.invitation.delivery.updated_at, language)}</dd></div>
          {share.capture ? <><div><dt>{t('shareCapturedAt')}</dt><dd>{shareDate(share.capture.captured_at, language)}</dd></div><div><dt>{t('shareCaptureHash')}</dt><dd className="sharing-hash">{share.capture.content_sha256}</dd></div></> : null}
        </dl></details>
        <div className="sharing-actions">
          <button className="secondary-button" type="button" disabled={busy || editor !== null || share.state === 'revoked'} onClick={() => setEditor(share)}>{t('shareEdit')}</button>
          <button className="secondary-button" type="button" disabled={busy || editor !== null || !options.data?.lifetime_policy.configured || share.invitation.accepted_at !== null || share.state === 'revoked' || share.state === 'expired'}
            onClick={() => { setReissue(share.id); setInvitationExpiry(''); }} aria-expanded={reissue === share.id}>{t('shareReissue')}</button>
          <button className="secondary-button sharing-danger" type="button" disabled={busy || share.state === 'revoked' || editor !== null} onClick={() => void mutate(share, 'revoke')}>{t('shareRevoke')}</button>
        </div>
        {reissue === share.id ? <form className="sharing-reissue" onSubmit={event => { event.preventDefault(); void mutate(share, 'reissue'); }}>
          <p>{t('shareReissueHelp')}</p><ExpiryField kind="invitation" value={invitationExpiry} onChange={setInvitationExpiry} maximum={options.data!.lifetime_policy.invitation_max_lifetime_seconds} t={t} />
          <div className="sharing-actions"><button className="primary-button" type="submit" disabled={busy}>{t('shareReissue')}</button><button className="secondary-button" type="button" disabled={busy} onClick={() => setReissue(null)}>{t('cancel')}</button></div>
        </form> : null}
      </li>;
    })}</ul>
    <nav className="sharing-actions" aria-label={t('shareOutgoingPages')}>
      <button className="secondary-button" type="button" disabled={busy || editor !== null || offset === 0 || page.loading} onClick={() => { setUpdates({}); setOffset(value => Math.max(0, value - 25)); }}>{t('sharePrevious')}</button>
      <span>{t('sharePage').replace('{page}', String(offset / 25 + 1))}</span>
      <button className="secondary-button" type="button" disabled={busy || editor !== null || !page.data?.has_more} onClick={() => { setUpdates({}); setOffset(value => value + 25); }}>{t('shareNext')}</button>
    </nav>
    {confirmActionModal}
  </>;
}
