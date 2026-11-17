import { Pencil } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FocusEvent } from 'react';
import { createPortal } from 'react-dom';

import { ApiError } from '../api/client';
import { useAnchoredPopover } from '../hooks/useAnchoredPopover';
import type { Translator } from '../i18n';
import type { DnaValidation, DnaValidationMutation } from '../types';
import { formatDisplayDateTime } from '../utils/dateFormat';
import type { ConfirmActionOptions } from './ConfirmActionModal';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';

import { DnaEvidenceLink, isDnaEvidenceUrl } from './DnaEvidenceLink';

export default function DnaValidationBadge({ validation, canEdit, onMutate, onReload, isOperationCurrent, confirmAction, t }: {
  validation: DnaValidation | null;
  canEdit: boolean;
  onMutate: (mutation: DnaValidationMutation) => Promise<void>;
  onReload: () => Promise<unknown>;
  isOperationCurrent: () => boolean;
  confirmAction: (options: ConfirmActionOptions) => Promise<boolean>;
  t: Translator;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [url, setUrl] = useState('');
  const [dirty, setDirty] = useState(false);
  const [editValidation, setEditValidation] = useState<DnaValidation | null>(null);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);

  const [completed, setCompleted] = useState<number | null | undefined>(undefined);
  const busyRef = useRef(false);
  const alive = useRef(true);
  const dialogRef = useRef<HTMLElement>(null);
  const revision = validation?.revision ?? null;
  // A committed write must not be repeated if its detail refresh failed.
  const blocked = conflict || completed === revision;
  const close = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) anchorRef.current?.focus({ preventScroll: true });
  }, []);
  const { anchorRef, panelRef, position, id } = useAnchoredPopover<HTMLButtonElement>(open, close);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const current = () => alive.current && isOperationCurrent();


  useLayoutEffect(() => {
    if (!open) return;
    // Wait for the anchored panel's measured position to reach the DOM before
    // focusing: focusing a still-hidden link is ignored by the browser.
    const frame = requestAnimationFrame(() => {
      panelRef.current?.querySelector<HTMLElement>('a, button')?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [open, position.visibility, panelRef]);

  useLayoutEffect(() => {
    if (!editing) return;
    const dialog = dialogRef.current;
    const backdrop = dialog?.closest('.modal-backdrop');
    const background = Array.from(document.body.children)
      .filter((node): node is HTMLElement => node instanceof HTMLElement && node !== backdrop);
    const previousInert = background.map((node) => node.inert);
    background.forEach((node) => { node.inert = true; });
    dialog?.querySelector('input')?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!busyRef.current) setEditing(false);
      }
      if (event.key !== 'Tab' || !dialog) return;
      const nodes = Array.from(dialog.querySelectorAll<HTMLElement>('input:not(:disabled), button:not(:disabled)'));
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (!first) { event.preventDefault(); dialog.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }
    window.addEventListener('keydown', onKey);
    const trigger = anchorRef.current;
    return () => {
      window.removeEventListener('keydown', onKey);
      background.forEach((node, index) => { node.inert = previousInert[index]; });
      trigger?.focus({ preventScroll: true });
    };
  }, [editing, anchorRef]);

  function startEdit() {
    close();
    setEditValidation(validation);
    setUrl(validation?.evidence_url ?? '');
    setError('');
    setDirty(false);
    setEditing(true);
  }

  async function mutate(mutation: DnaValidationMutation) {
    if (!canEdit || busyRef.current || blocked || !current()) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      await onMutate(mutation);
      if (!current()) return;
      setCompleted(revision);
      setEditing(false);
      close(true);
    } catch (requestError) {
      if (!current()) return;
      if (!editing) setOpen(true);
      if (requestError instanceof ApiError && requestError.status === 409) {
        setConflict(true);
        setError(t('dnaConflict'));
      } else {
        setError(t(requestError instanceof ApiError && requestError.status === 403 ? 'dnaForbidden' : 'dnaSaveError'));
      }
    } finally {
      busyRef.current = false;
      if (current()) setBusy(false);
    }
  }

  async function reload() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await onReload();
      if (!current()) return;
      setConflict(false);
      setCompleted(undefined);
      setError('');
      setEditing(false);
      close(true);
    } catch {
      if (current()) setError(t('dnaReloadError'));
    } finally {
      busyRef.current = false;
      if (current()) setBusy(false);
    }
  }

  async function revoke() {
    if (!validation || !canEdit || blocked || busyRef.current) return;
    close(true);
    busyRef.current = true;
    setBusy(true);
    const confirmed = await confirmAction({
      title: t('dnaRevoke'), message: t('dnaRevokeMessage'),
      confirmLabel: t('dnaRevoke'), cancelLabel: t('cancel'), variant: 'danger',
    });
    busyRef.current = false;
    if (!current()) return;
    setBusy(false);
    anchorRef.current?.focus({ preventScroll: true });
    if (confirmed) await mutate({ operation: 'revoke', expected_revision: validation.revision });
  }

  const label = t(validation ? 'dnaValidated' : 'dnaMissing');
  const icon = <svg width="26" height="26" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M14 2.5 24 7v7c0 5.5-5 9-10 11.5C9 23 4 19.5 4 14V7Z" />
    <path d="M10 8c0 6 8 6 8 12M18 8c0 6-8 6-8 12M11 10h6m-6 8h6" />
    <circle cx="22" cy="6" r="5.5" fill="var(--color-surface)" stroke="none" />
    {validation ? <path d="m19 6 2 2 4-4" /> : <path d="M20 4a2 2 0 0 1 4 0c0 1-2 1-2 2m0 2h.01" />}
  </svg>;
  const reloadButton = blocked
    ? <button type="button" className="dna-reload" disabled={busy} onClick={() => void reload()}>{t('dnaReload')}</button>
    : null;
  // The URL error only appears after a save attempt or after leaving a modified field.
  function validateOnBlur(event: FocusEvent<HTMLInputElement>) {
    // Let submit validate when Save receives focus: inserting an error on blur
    // would move the button between pointer down and up, cancelling the click.
    if (event.relatedTarget instanceof HTMLButtonElement && event.relatedTarget.type === 'submit') return;
    if (dirty && !isDnaEvidenceUrl(url)) setError(t('dnaInvalidUrl'));
  }

  return <>
    {validation || canEdit ? <button ref={anchorRef} className={`dna-validation-badge${validation ? ' is-validated' : ''}`} type="button"
      aria-label={label} title={label} aria-haspopup="dialog" aria-expanded={open || editing}
      aria-controls={open ? id : editing ? `${id}-editor` : undefined}
      onClick={() => validation || blocked ? setOpen(!open) : startEdit()}>{icon}</button>
      : <span className="dna-validation-badge" role="img" aria-label={label} title={label}>{icon}</span>}
    {open ? createPortal(<div ref={panelRef} id={id} role="dialog" aria-labelledby={`${id}-title`} className="anchored-popover dna-validation-popover" style={position}>
      <header className="dna-validation-heading">
        <h3 id={`${id}-title`}>{t('dnaTitle')}</h3>
      </header>
      {validation ? <dl className="dna-validation-facts">
        <div>
          <dt>{t('dnaEvidenceLabel')}</dt>
          <dd>
            <DnaEvidenceLink url={validation.evidence_url} />
            {canEdit ? <button type="button" className="dna-evidence-edit" aria-label={t('dnaEdit')} title={t('dnaEdit')} disabled={busy || blocked} onClick={startEdit}><Pencil aria-hidden="true" size={16} /></button> : null}
          </dd>
        </div>
        <div>
          <dt>{t('dnaAddedByLabel')}</dt>
          <dd>{validation.created_by}</dd>
        </div>
        <div>
          <dt>{t('dnaDateLabel')}</dt>
          <dd><time dateTime={validation.created_at}>{formatDisplayDateTime(validation.created_at)}</time></dd>
        </div>
      </dl> : null}
      {error ? <p role="alert" className="dna-error">{error}</p> : null}
      {reloadButton}
      {validation && canEdit ? <div className="dna-validation-actions">
        <button type="button" className="is-danger" disabled={busy || blocked} onClick={() => void revoke()}>{t('dnaRevoke')}</button>
      </div> : null}
    </div>, document.body) : null}
    {editing ? <ModalPortal><div className="modal-backdrop box-dialog-backdrop" role="presentation" onMouseDown={() => { if (!busyRef.current) setEditing(false); }}>
      <section ref={dialogRef} tabIndex={-1} id={`${id}-editor`} aria-busy={busy} className="box-dialog" role="dialog" aria-modal="true" aria-labelledby={`${id}-editor-title`} onMouseDown={(event) => event.stopPropagation()}>
        <header className="subculture-heading box-dialog-heading">
          <div>
            <p>{t('dnaTitle')}</p>
            <h2 id={`${id}-editor-title`}>{t(editValidation ? 'dnaEdit' : 'dnaAdd')}</h2>
          </div>
          <button className="icon-button box-dialog-close" type="button" aria-label={t('cancel')} title={t('cancel')} disabled={busy} onClick={() => setEditing(false)}>
            <PolypbaseIcon name="close" size={19} aria-hidden="true" />
          </button>
        </header>
        <form className="box-dialog-form" noValidate onSubmit={(event) => {
          event.preventDefault();
          if (!isDnaEvidenceUrl(url)) {
            setError(t('dnaInvalidUrl'));
            dialogRef.current?.querySelector<HTMLInputElement>('input')?.focus();
            return;
          }
          void mutate(editValidation ? { operation: 'update', evidence_url: url.trim(), expected_revision: editValidation.revision } : { operation: 'add', evidence_url: url.trim() });
        }}>
          <div className="box-dialog-body">
            <label htmlFor={`${id}-url`}>{t('dnaEvidenceUrl')}</label>
            <input id={`${id}-url`} type="url" name="evidence_url" autoComplete="url" required maxLength={2000} value={url} disabled={busy || blocked} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} onChange={(event) => { setUrl(event.target.value); setError(''); setDirty(true); }} onBlur={validateOnBlur} />
            {error ? <p id={`${id}-error`} className="inline-error" role="alert">{error}</p> : null}
            {reloadButton}
          </div>
          <footer className="subculture-actions box-dialog-actions">
            <button className="secondary-button" type="button" disabled={busy} onClick={() => setEditing(false)}>{t('cancel')}</button>
            <button className="primary-button" type="submit" disabled={busy || blocked || Boolean(editValidation && url.trim() === editValidation.evidence_url)}>{t(busy ? 'saving' : 'dnaSave')}</button>
          </footer>
        </form>
      </section>
    </div></ModalPortal> : null}
  </>;
}
