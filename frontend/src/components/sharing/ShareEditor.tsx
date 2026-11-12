import { type FormEvent, useEffect, useRef, useState } from 'react';
import { sharingApi, type AdminShare, type SharingOptions, type ScientificSnapshot, type ShareMode } from '../../api/boxSharing';
import { useSharingResource } from '../../hooks/useSharingResource';
import { readForSourceAdmin } from '../../api/sourceSharingSession';
import type { UserProfile } from '../../types';
import type { Language, Translator } from '../../i18n';
import { selectorLabel, shareDate, sharingError, sharingErrorCode, snapshotSupported, utcInput, validExpiry, validSelection } from '../../utils/boxSharing';
import { useSharingConfirmation } from '../../hooks/useSharingConfirmation';
import ScientificSnapshotView from './ScientificSnapshotView';
import { DisclosureControls, ExpiryField, ModeControls } from './SharingControls';
import SourceBoxLookup, { type SourceBox } from './SourceBoxLookup';

export default function ShareEditor({ profile, organizationId, options, share, language, onSaved, onCancel, onReload, t }: {
  profile: UserProfile; organizationId: number; options: SharingOptions; share?: AdminShare; language: Language;
  onSaved: (share: AdminShare) => void; onCancel: () => void; onReload?: () => Promise<void>; t: Translator;
}) {
  const [box, setBox] = useState<SourceBox | null>(share ? { ...share.box, organization: { id: organizationId, name: '', slug: null } } : null);
  const [email, setEmail] = useState(share?.recipient_email ?? '');
  const [selectors, setSelectors] = useState<string[]>(share?.selectors ?? []);
  const [mode, setMode] = useState<ShareMode | ''>(share?.mode ?? '');
  const [invitationExpiry, setInvitationExpiry] = useState('');
  const [accessExpiry, setAccessExpiry] = useState('');
  const [previewRequest, setPreviewRequest] = useState<{ signature: string; nonce: number }>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const locked = useRef(false);
  const lifecycle = useRef(new AbortController());
  useEffect(() => {
    alive.current = true; lifecycle.current = new AbortController();
    return () => { alive.current = false; lifecycle.current.abort(); };
  }, []);
  const { confirmAction, confirmActionModal } = useSharingConfirmation();
  const signature = JSON.stringify([box?.id, [...selectors].sort()]);
  const preview = useSharingResource<ScientificSnapshot | null>(`${signature}:${previewRequest?.signature}:${previewRequest?.nonce}`, async signal => {
    if (!box || previewRequest?.signature !== signature || !validSelection(options, selectors)) return null;
    return (await readForSourceAdmin(profile, organizationId, signal, () => sharingApi.preview(organizationId, box.id, selectors, signal))).snapshot;
  });
  const policy = options.lifetime_policy;
  const selectionChanged = share ? JSON.stringify([...selectors].sort()) !== JSON.stringify([...share.selectors].sort()) : true;
  const previewReady = preview.data != null && snapshotSupported(preview.data) && previewRequest?.signature === signature
    && JSON.stringify([...preview.data.disclosed].sort()) === JSON.stringify([...selectors].sort());
  const frozenUnavailable = mode === 'frozen' && !options.frozen_capture_available && selectionChanged;
  const canSave = validSelection(options, selectors) && !frozenUnavailable && (Boolean(share) || policy.configured)
    && sharingErrorCode(error) !== 'policy_revision_conflict';

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked.current) return;
    setError(undefined);
    if (!box || !mode || !canSave || (selectionChanged && !previewReady)) { setError({ local: true }); return; }
    if ((!share && (!validExpiry(invitationExpiry, policy.invitation_max_lifetime_seconds) || !validExpiry(accessExpiry, policy.access_max_lifetime_seconds)
      || Date.parse(utcInput(invitationExpiry)!) > Date.parse(utcInput(accessExpiry)!)))
      || (share && accessExpiry !== '' && !validExpiry(accessExpiry, policy.access_max_lifetime_seconds))) {
      setError({ dates: true }); return;
    }
    locked.current = true;
    setBusy(true);
    try {
      const added = share ? selectors.filter(name => !share.selectors.includes(name)) : [];
      const recapture = Boolean(share && mode === 'frozen' && selectionChanged);
      const confirmed = await confirmAction({
        title: t(share ? 'shareSave' : 'shareCreate'), confirmLabel: t(share ? 'shareSave' : 'shareCreateConfirm'), cancelLabel: t('cancel'),
        message: [t('shareConfirmDisclosure'), added.length ? t('shareConfirmExpansion') : '', recapture ? t('shareConfirmRecapture') : '', selectors.includes('observation.time') ? t('shareHistoryScope') : ''].filter(Boolean).join(' '),
        details: [
          { label: t('shareSelectedBox'), value: box.global_code }, { label: t('shareRecipient'), value: email },
          { label: t('shareMode'), value: t(mode === 'live' ? 'shareLive' : 'shareFrozen') },
          { label: t('shareDisclosure'), value: selectors.map(name => selectorLabel(name, t)).join(', ') },
          { label: t('shareInvitationExpiry'), value: shareDate(share?.invitation.expires_at ?? utcInput(invitationExpiry), language) },
          { label: t('shareAccessExpiry'), value: shareDate(accessExpiry ? utcInput(accessExpiry) : share?.access_expires_at ?? null, language) },
          { label: t('shareAddedFields'), value: added.length ? added.map(name => selectorLabel(name, t)).join(', ') : null },
        ],
      });
      if (!confirmed || !alive.current) return;
      const response = await readForSourceAdmin(profile, organizationId, lifecycle.current.signal, () => share ? sharingApi.update(organizationId, share.id, {
        expected_policy_revision: share.policy_revision,
        ...(selectionChanged ? { selectors, confirm_expansion: added.length > 0, confirm_recapture: recapture } : {}),
        ...(accessExpiry ? { access_expires_at: utcInput(accessExpiry)! } : {}),
      }) : sharingApi.create(organizationId, { box_id: box.id, recipient_email: email.trim(), mode, selectors,
        invitation_expires_at: utcInput(invitationExpiry)!, access_expires_at: utcInput(accessExpiry)! }));
      if (alive.current) onSaved(response.share);
    } catch (requestError) { if (alive.current) setError(requestError); }
    finally { locked.current = false; if (alive.current) setBusy(false); }
  }
  return <section className="sharing-editor" aria-labelledby="sharing-editor-title">
    <div className="section-title"><h3 id="sharing-editor-title">{t(share ? 'shareEdit' : 'shareCreate')}</h3>
      <button className="secondary-button" type="button" disabled={busy} onClick={onCancel}>{t('cancel')}</button></div>
    {!share ? <SourceBoxLookup profile={profile} organizationId={organizationId} selected={box} onSelect={setBox} t={t} /> : <p><strong>{share.box.global_code}</strong> · {share.recipient_email}</p>}
    <form onSubmit={save}>
      <fieldset className="sharing-form-body" disabled={busy}>
        {!share ? <label className="sharing-field">{t('shareRecipient')}<input type="email" autoComplete="off" required maxLength={254} value={email} onChange={event => setEmail(event.target.value)} /></label> : null}
        <DisclosureControls options={options} selected={selectors} onChange={setSelectors} t={t} />
        {!share ? <ModeControls options={options} mode={mode} onChange={setMode} t={t} /> : <p>{t(mode === 'live' ? 'shareLiveHelp' : 'shareFrozenHelp')}</p>}
        {!policy.configured ? <p className="sharing-notice" role="status">{t('shareErrorConfiguration')}</p> : <div className="sharing-expiries">
          {!share ? <ExpiryField kind="invitation" value={invitationExpiry} onChange={setInvitationExpiry} maximum={policy.invitation_max_lifetime_seconds} t={t} /> : null}
          <div><ExpiryField kind="access" value={accessExpiry} onChange={setAccessExpiry} maximum={policy.access_max_lifetime_seconds} required={!share} t={t} />
            {share ? <p className="sharing-help">{t('shareKeepExpiry').replace('{date}', shareDate(share.access_expires_at, language))}</p> : null}</div>
        </div>}
        {frozenUnavailable ? <p className="sharing-notice">{t('shareErrorFrozen')}</p> : null}
        <div className="sharing-actions">
          <button className="secondary-button" type="button" disabled={!box || !validSelection(options, selectors) || preview.loading}
            onClick={() => setPreviewRequest({ signature, nonce: Date.now() })}>{t('sharePreview')}</button>
          <button className="primary-button" type="submit" disabled={!canSave || (selectionChanged && !previewReady)}>{busy ? t('saving') : t(share ? 'shareSave' : 'shareCreateConfirm')}</button>
        </div>
      </fieldset>
      {error ? <div className="inline-error" role="alert"><p>{typeof error === 'object' && error !== null && 'dates' in error ? t('shareErrorDates')
        : typeof error === 'object' && error !== null && 'local' in error ? t('shareErrorValidation') : sharingError(error, t)}</p>
        {sharingErrorCode(error) === 'policy_revision_conflict' && onReload ? <button className="secondary-button" type="button" onClick={() => void onReload().catch(setError)}>{t('shareReloadPolicy')}</button> : null}
      </div> : null}
    </form>
    {previewRequest?.signature === signature && preview.loading ? <p role="status">{t('loading')}</p> : null}
    {preview.error ? <p className="inline-error" role="alert">{sharingError(preview.error, t)}</p> : null}
    {preview.data && !previewReady ? <p className="inline-error" role="alert">{t('shareUnsupported')}</p> : null}
    {previewReady ? <section className="sharing-preview"><h3>{t('sharePreviewTitle')}</h3><p className="sharing-help">{t('sharePreviewHelp')}</p><ScientificSnapshotView key={signature} snapshot={preview.data!} t={t} /></section> : null}
    {confirmActionModal}
  </section>;
}
