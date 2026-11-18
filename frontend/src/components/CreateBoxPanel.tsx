import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { LockKeyhole } from 'lucide-react';

import { ApiError, apiGet } from '../api/client';
import type { Translator } from '../i18n';
import type { BoxCreatePayload, BoxCreationIntent, BoxCreationInspection, BoxCreationResolution, BoxDetail, BoxStrainOption, ExportOptions, UserProfile } from '../types';
import {
  CREATION_RETRY_DELAYS_MS,
  creationStorageKey,
  creationIntentDigest,
  forgetStoredCreationIntent,
  isUncertainCreationError,
  readStoredCreationIntent,
  storeCreationIntent,
  todayLocalIsoDate,
} from '../utils/boxCreationRecovery';
import { getErrorMessage } from '../utils/errors';
import CreatedBoxIdentifier from './CreatedBoxIdentifier';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import StrainPicker from './StrainPicker';

type TFunction = Translator;
// draft: editable; sending: a request is in flight; waiting: outcome uncertain,
// retried automatically; blocked: the server refuses to settle the key for this account.
type CreationPhase = 'draft' | 'sending' | 'waiting' | 'blocked' | 'created';
type PendingIntent = BoxCreatePayload | BoxCreationIntent | { request_id: string };
const DIALOG_FOCUSABLE_SELECTOR =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

function hasFieldError(error: unknown) {
  const data = error instanceof ApiError ? error.data : null;
  return Boolean(data && typeof data === 'object' && ['strain', 'thermal_zone', 'entered_on', 'notes', 'initial_polyp_count']
    .some((field) => field in (data as Record<string, unknown>)));
}

// A whole, non-negative count: empty is missing, never 0.
const INITIAL_POLYPS_PATTERN = /^\d{1,10}$/;
const MAX_INITIAL_POLYPS = 2147483647;

export function parseInitialPolypCount(value: string) {
  const text = value.trim();
  if (!INITIAL_POLYPS_PATTERN.test(text)) return null;
  const count = Number(text);
  return count <= MAX_INITIAL_POLYPS ? count : null;
}

export default function CreateBoxPanel({
  exportOptions,
  initialStrainId = null,
  lockStrain = false,
  isOpen: controlledIsOpen,
  isOptionsLoading,
  presentation = 'inline',
  profile,
  onCreateBox,
  isOperationCurrent,
  onOpenChange,
  onRequestOptions,
  onSelectBox,
  t,
}: {
  exportOptions: ExportOptions | null;
  // Preselected Strain (and so its Species) when continuing from its creation.
  initialStrainId?: number | null;
  lockStrain?: boolean;
  isOpen?: boolean;
  isOptionsLoading: boolean;
  presentation?: 'inline' | 'modal';
  profile: UserProfile | null;
  onCreateBox: (payload: BoxCreationIntent) => Promise<BoxDetail>;
  onResolveBoxCreation: (requestId: string) => Promise<BoxCreationResolution>;
  isOperationCurrent: () => boolean;
  onOpenChange?: (isOpen: boolean) => void;
  onRequestOptions: () => void;
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  const [isInlineOpen, setIsInlineOpen] = useState(false);
  const isOpen = presentation === 'modal' ? Boolean(controlledIsOpen) : isInlineOpen;
  const dialogRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const operationLifetimeRef = useRef(true);
  useLayoutEffect(() => {
    operationLifetimeRef.current = true;
    return () => { operationLifetimeRef.current = false; };
  }, []);
  const organizationId = profile?.active_organization?.id ?? null;
  // Pending creations are scoped to this tab, actor and institution.
  const storageKey = creationStorageKey(profile?.id, organizationId);
  const [stored] = useState(() => readStoredCreationIntent(storageKey));
  const pending = stored?.kind === 'pending' ? stored : null;
  const initialDraft = pending?.canRestoreDraft ? pending.intent as BoxCreatePayload : null;
  // A contextual form cannot silently resume another (or unidentified) strain.
  // Keep its evidence intact so the general creation flow can recover it.
  const hasContextConflict = lockStrain && pending != null
    && (initialDraft == null || initialDraft.strain !== initialStrainId);
  const currentContextRef = useRef({ storageKey, initialStrainId, lockStrain, isOperationCurrent });
  currentContextRef.current = { storageKey, initialStrainId, lockStrain, isOperationCurrent };
  const pendingIntentRef = useRef<PendingIntent | null>(pending?.intent ?? null);
  // Only a complete, scoped token-bound intention can resume registration/execution.
  // Bare or divergent UUIDs never acquire write authority.
  const replayRef = useRef(pending?.canResume ?? false);
  const restoredDigestRef = useRef(pending?.digest ?? null);
  const committedEvidenceRef = useRef<BoxDetail[]>([]);
  // Records are cleared only once every candidate is terminal.
  const requestIdsRef = useRef<string[]>(pending?.requestIds ?? []);
  // Destroyed identities cannot be reconciled safely; do not offer a replacement.
  const [hasUnverifiedPrevious, setHasUnverifiedPrevious] = useState(stored?.kind === 'unidentified' || hasContextConflict);
  const refusalRef = useRef<unknown>(null);
  const busyRef = useRef(false);
  const attemptRef = useRef(0);
  const retryTimerRef = useRef<number | null>(null);
  const reconcileRef = useRef<() => Promise<void>>(async () => {});
  const [phase, setPhase] = useState<CreationPhase>(hasContextConflict || stored?.kind === 'unidentified' ? 'blocked' : pending ? 'sending' : 'draft');
  const [isRetryExhausted, setIsRetryExhausted] = useState(false);
  const [isRecoveryUnavailable, setIsRecoveryUnavailable] = useState(false);
  const [createdBox, setCreatedBox] = useState<BoxDetail | null>(null);
  // Divergent stored keys can reveal two committed Boxes; both are shown, none is hidden.
  const [otherCreatedBox, setOtherCreatedBox] = useState<BoxDetail | null>(null);
  const [strainId, setStrainId] = useState<number | null>(initialDraft?.strain ?? initialStrainId);
  const [zoneId, setZoneId] = useState<number | null>(initialDraft?.thermal_zone ?? null);
  const [enteredOn, setEnteredOn] = useState(initialDraft?.entered_on ?? todayLocalIsoDate());
  const [notes, setNotes] = useState(initialDraft?.notes ?? '');
  const [initialPolyps, setInitialPolyps] = useState(
    typeof initialDraft?.initial_polyp_count === 'number' ? String(initialDraft.initial_polyp_count) : '',
  );
  const [isInitialPolypsTouched, setIsInitialPolypsTouched] = useState(false);
  const initialPolypCount = parseInitialPolypCount(initialPolyps);
  const [strainOptions, setStrainOptions] = useState<BoxStrainOption[] | null>(null);
  const [isStrainsLoading, setIsStrainsLoading] = useState(false);
  const [strainsError, setStrainsError] = useState<string | null>(null);
  const [strainsAttempt, setStrainsAttempt] = useState(0);

  const [error, setError] = useState<string | null>(null);
  const isLocked = phase !== 'draft' || hasContextConflict;
  const effectiveStrainId = lockStrain ? initialStrainId : strainId;
  const strainsContext = JSON.stringify([storageKey, isOpen, strainsAttempt, lockStrain, initialStrainId]);
  const [loadedStrainsContext, setLoadedStrainsContext] = useState<string | null>(null);
  const hasCurrentStrains = loadedStrainsContext === strainsContext;

  function setOpen(nextIsOpen: boolean) {
    if (presentation === 'modal') onOpenChange?.(nextIsOpen);
    else setIsInlineOpen(nextIsOpen);
  }

  function isAlive() {
    const current = currentContextRef.current;
    return operationLifetimeRef.current && current.storageKey === storageKey
      && current.initialStrainId === initialStrainId && current.lockStrain === lockStrain
      && isOperationCurrent() && current.isOperationCurrent();
  }

  function clearRetryTimer() {
    if (retryTimerRef.current != null) window.clearTimeout(retryTimerRef.current);
    retryTimerRef.current = null;
  }

  function settle() {
    forgetStoredCreationIntent(storageKey);
    pendingIntentRef.current = null;
    requestIdsRef.current = [];
    replayRef.current = false;
    attemptRef.current = 0;
    clearRetryTimer();
    setIsRetryExhausted(false);
  }

  function finishCreated(box: BoxDetail, otherBox: BoxDetail | null = null) {
    settle();
    refusalRef.current = null;
    setError(null);
    setCreatedBox(box);
    setOtherCreatedBox(otherBox);
    setPhase('created');
  }

  function finishCancelled() {
    // Only a durable terminal receipt makes it safe to edit and send again.
    const refusal = refusalRef.current;
    settle();
    refusalRef.current = null;
    setPhase('draft');
    if (refusal instanceof ApiError && refusal.status === 403) setError(t('createBoxForbidden'));
    else if (hasFieldError(refusal)) setError(getErrorMessage(refusal, t('createBoxNotCreated')));
    else setError(t('createBoxNotCreated'));
  }

  function scheduleRetry() {
    setPhase('waiting');
    const delay = CREATION_RETRY_DELAYS_MS[attemptRef.current];
    attemptRef.current += 1;
    if (delay === undefined) {
      setIsRetryExhausted(true);
      return;
    }
    retryTimerRef.current = window.setTimeout(() => {
      retryTimerRef.current = null;
      void reconcileRef.current();
    }, delay);
  }

  // Re-register the same immutable token-bound intention on every retry. This is
  // safe even if registration never reached Django or execution already committed.
  async function reconcile() {
    const intent = pendingIntentRef.current;
    if (!intent || hasContextConflict || busyRef.current || !isAlive()) return;
    busyRef.current = true;
    clearRetryTimer();
    setPhase('sending');
    try {
      if (replayRef.current && restoredDigestRef.current != null) {
        let digest: string;
        try {
          digest = await creationIntentDigest(storageKey!, intent as BoxCreationIntent);
        } catch {
          if (isAlive()) {
            setPhase('blocked');
            setError(t('createBoxPreviousUnverified'));
          }
          return;
        }
        if (!isAlive()) return;
        // This checksum detects damaged local evidence; server token/intent matching
        // remains the authority. A checksum is never cancellation permission.
        if (digest !== restoredDigestRef.current) replayRef.current = false;
        restoredDigestRef.current = null;
      }
      if (replayRef.current) {
        try {
          const box = await onCreateBox({ ...(intent as BoxCreationIntent) });
          if (isAlive()) finishCreated(box);
          return;
        } catch (requestError) {
          if (!isAlive()) return;
          if (isUncertainCreationError(requestError)) {
            scheduleRetry();
            return;
          }
          const data = requestError instanceof ApiError ? requestError.data : null;
          if (data && typeof data === 'object' && 'code' in data && data.code === 'box_creation_intent_settled') {
            refusalRef.current = requestError;
            finishCancelled();
          } else {
            // A permission refusal or binding conflict is not proof of termination.
            setPhase('blocked');
            setError(requestError instanceof ApiError && requestError.status === 403
              ? t('createBoxForbidden') : t('createBoxPreviousUnverified'));
          }
          return;
        }
      }
      try {
        const committed = committedEvidenceRef.current;
        let hasUnknown = false;
        for (const requestId of requestIdsRef.current.length ? requestIdsRef.current : [intent.request_id]) {
          const result = await apiGet<BoxCreationInspection>(
            `/api/boxes/automatic/resolve/?request_id=${encodeURIComponent(requestId)}`, { cache: 'no-store' },
          );
          if (!isAlive()) return;
          if (result.status === 'unknown' || result.status === 'pending') hasUnknown = true;
          if (result.status === 'created' && !committed.some((box) => box.id === result.box.id)) {
            committed.push(result.box);
            // Keep evidence visible even if inspection of another candidate fails.
            setCreatedBox(committed[0]);
            setOtherCreatedBox(committed[1] ?? null);
          }
        }
        // Absence does not fence a delayed create. Never clear storage or unlock here.
        if (hasUnknown) {
          setIsRecoveryUnavailable(true);
          scheduleRetry();
        } else if (committed.length) finishCreated(committed[0], committed[1] ?? null);
        else finishCancelled();
      } catch (requestError) {
        if (!isAlive()) return;
        if (isUncertainCreationError(requestError)) {
          scheduleRetry();
          return;
        }
        // The key stays stored: a later session with the right access can settle it.
        clearRetryTimer();
        setPhase('blocked');
        setError(requestError instanceof ApiError && requestError.status === 403
          ? t('createBoxForbidden') : getErrorMessage(requestError, t('createBoxNotCreated')));
      }
    } finally {
      busyRef.current = false;
    }
  }
  reconcileRef.current = reconcile;

  function retryNow() {
    if (!pendingIntentRef.current || busyRef.current || phase === 'blocked') return;
    attemptRef.current = 0;
    setIsRetryExhausted(false);
    void reconcileRef.current();
  }
  const retryNowRef = useRef(retryNow);
  retryNowRef.current = retryNow;

  useEffect(() => {
    // A creation left unsettled by a reload or lost response resumes on its own.
    if (pendingIntentRef.current) void reconcileRef.current();
    const handleOnline = () => retryNowRef.current();
    window.addEventListener('online', handleOnline);
    return () => {
      window.removeEventListener('online', handleOnline);
      clearRetryTimer();
    };
  }, []);

  useEffect(() => {
    if (isOpen && phase === 'waiting') retryNowRef.current();
    // Only reopening restarts a wait; phase changes are handled by reconcile itself.
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || organizationId == null || !isAlive()) return;
    let isCurrent = true;
    setIsStrainsLoading(true);
    setStrainOptions(null);
    setStrainsError(null);
    apiGet<BoxStrainOption[]>('/api/boxes/automatic/strains/', { cache: 'no-store' })
      .then((result) => {
        if (isCurrent && isAlive()) {
          setStrainOptions(result);
          setLoadedStrainsContext(strainsContext);
        }
      })
      .catch((requestError) => {
        if (isCurrent && isAlive()) setStrainsError(getErrorMessage(requestError, t('taxonomyLoadError')));
      })
      .finally(() => {
        if (isCurrent && isAlive()) setIsStrainsLoading(false);
      });
    return () => { isCurrent = false; };
  }, [isOpen, organizationId, profile?.id, t, strainsAttempt, lockStrain, initialStrainId]);

  const selectedStrain = hasCurrentStrains
    ? strainOptions?.find((strain) => strain.id === effectiveStrainId) ?? null : null;

  const availableZones = (exportOptions?.zones ?? []).filter((zone) => zone.organization_id === organizationId);
  const selectedZone = availableZones.find((zone) => zone.id === zoneId) ?? null;
  const canSubmit = organizationId != null && storageKey != null && !isLocked && !createdBox
    && isOpen && isAlive() && hasCurrentStrains && strainOptions != null && !isStrainsLoading && !strainsError
    && selectedStrain?.identity.status === 'usable'
    && selectedZone != null && enteredOn !== '' && initialPolypCount !== null && !isOptionsLoading;

  useEffect(() => {
    // An unlocked draft never keeps a choice the server no longer offers.
    if (lockStrain || isLocked || !hasCurrentStrains || strainOptions == null || strainId == null || strainOptions.some((strain) => strain.id === strainId)) return;
    setStrainId(null);
  }, [lockStrain, isLocked, hasCurrentStrains, strainId, strainOptions]);

  useEffect(() => {
    if (isLocked || !exportOptions || zoneId == null || availableZones.some((zone) => zone.id === zoneId)) return;
    setZoneId(null);
  }, [availableZones, exportOptions, isLocked, zoneId]);

  useEffect(() => {
    if (presentation === 'modal' && isOpen && !exportOptions) onRequestOptions();
  }, [exportOptions, isOpen, onRequestOptions, presentation]);

  useLayoutEffect(() => {
    if (presentation !== 'modal' || !isOpen) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    dialogRef.current?.querySelector<HTMLButtonElement>('.modal-close-button')?.focus();
  }, [isOpen, presentation]);

  useEffect(() => {
    if (presentation !== 'modal' || !isOpen) return;

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== 'Tab') return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE_SELECTOR));
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onOpenChange, presentation]);

  useEffect(() => {
    if (presentation !== 'modal' || !isOpen) return;
    const returnFocus = returnFocusRef.current;
    return () => {
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, [isOpen, presentation]);

  useLayoutEffect(() => {
    if (createdBox && isOpen && presentation === 'modal') {
      dialogRef.current?.querySelector<HTMLButtonElement>('.create-box-open-created')?.focus();
    }
  }, [createdBox, isOpen, presentation]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsInitialPolypsTouched(true);
    if (busyRef.current || pendingIntentRef.current || !canSubmit || effectiveStrainId == null || zoneId == null
      || initialPolypCount === null || !isAlive()) return;
    const intent: BoxCreationIntent = {
      strain: effectiveStrainId, thermal_zone: zoneId, entered_on: enteredOn, volume_liters: null, notes: notes.trim(),
      initial_polyp_count: initialPolypCount,
      request_id: window.crypto.randomUUID(),
      resume_token: window.crypto.randomUUID(),
    };
    // Reserve the submit synchronously while computing integrity and persisting.
    busyRef.current = true;
    setPhase('sending');
    const storedSuccessfully = await storeCreationIntent(storageKey!, intent);
    busyRef.current = false;
    if (!isAlive()) return;
    if (!storedSuccessfully) {
      setPhase('draft');
      setError(t('createBoxStorageUnavailable'));
      return;
    }
    setHasUnverifiedPrevious(false);
    pendingIntentRef.current = intent;
    requestIdsRef.current = [intent.request_id];
    replayRef.current = true;
    restoredDigestRef.current = null;
    committedEvidenceRef.current = [];
    refusalRef.current = null;
    attemptRef.current = 0;
    setError(null);
    await reconcile();
  }

  function openCreatedBox(box: BoxDetail | null = createdBox) {
    if (!box || !isAlive()) return;
    onSelectBox(box.id);
    if (presentation === 'modal') {
      window.requestAnimationFrame(() => {
        document.querySelector<HTMLElement>('[data-box-page-focus-target]')?.focus({ preventScroll: true });
      });
    }
  }

  function startAnotherBox() {
    // Same strain, location and date are common for a series; the note is per box.
    if (pendingIntentRef.current) return;
    committedEvidenceRef.current = [];
    setCreatedBox(null);
    setOtherCreatedBox(null);
    setNotes('');
    setInitialPolyps('');
    setIsInitialPolypsTouched(false);
    setPhase('draft');
  }


  const statusNotice = phase === 'waiting' ? (
    <div className="create-box-notice" role="status">
      <PolypbaseIcon name="info" size={16} />
      <span>{t(isRecoveryUnavailable ? 'createBoxRecoveryUnavailable' : 'createBoxConnectionPending')}</span>
      {isRetryExhausted ? (
        <button className="create-box-retry-now" type="button" onClick={retryNow}>{t('createBoxConnectionRetry')}</button>
      ) : null}
    </div>
  ) : null;

  const panel = (
    <section className={presentation === 'modal' ? 'create-box-panel is-modal' : 'create-box-panel'}>
      {presentation === 'inline' ? (
        <button
          className="create-box-toggle"
          type="button"
          onClick={() => {
            if (!isOpen && !exportOptions) onRequestOptions();
            setOpen(!isOpen);
          }}
        >
          <span aria-hidden="true">
            <PolypbaseIcon name={isOpen ? 'close' : 'plus'} size={18} />
          </span>
          <strong>{isOpen ? t('createBoxClose') : t('createBoxOpen')}</strong>
        </button>
      ) : null}

      {isOpen && createdBox ? (
        <div className="create-box-form create-box-result">
          <CreatedBoxIdentifier createdBox={createdBox} t={t} />
          <div className="create-box-actions">

            {otherCreatedBox ? (
              <p className="create-box-notice create-box-also-created" role="status">
                {t('createBoxAlsoCreated')} <strong>{otherCreatedBox.global_code}</strong>
                <button className="create-box-retry-now" type="button" onClick={() => openCreatedBox(otherCreatedBox)}>
                  {t('createBoxOpenCreated')}
                </button>
              </p>
            ) : null}
            {statusNotice}
            {error ? <p className="inline-error" role="alert">{error}</p> : null}
            <button type="button" className="secondary-button create-box-another" disabled={phase !== 'created'} onClick={startAnotherBox}>{t('createBoxAnother')}</button>
            <button type="button" className="primary-button create-box-open-created" onClick={() => openCreatedBox()}>{t('createBoxOpenCreated')}</button>
          </div>
        </div>
      ) : isOpen ? (
        <form className="create-box-form" onSubmit={handleSubmit} aria-busy={phase === 'sending' || undefined}>
          <div className="section-title">
            <h2>{t('createBoxTitle')}</h2>
          </div>

          <fieldset className="create-box-strain-field" disabled={isLocked}>
            <legend className={lockStrain ? 'create-box-locked-strain-legend' : undefined}>
              {lockStrain ? (
                <span>
                  {t('createBoxSelectedStrain')}
                  <LockKeyhole size={14} strokeWidth={1.5} aria-hidden="true" />
                </span>
              ) : t('createBoxStrain')}
            </legend>
            {isStrainsLoading ? <p className="muted compact-text">{t('loading')}</p> : null}
            {strainsError ? (
              <p className="inline-error" role="alert">
                {strainsError} <button type="button" onClick={() => setStrainsAttempt((value) => value + 1)}>{t('createBoxRetry')}</button>
              </p>
            ) : null}
            {!isStrainsLoading && !strainsError && hasCurrentStrains && strainOptions
              && (lockStrain ? !selectedStrain || selectedStrain.identity.status !== 'usable' : !strainOptions.length)
              ? <p className="muted compact-text">{t('createBoxNoOptions')}</p> : null}
            {lockStrain && selectedStrain?.identity.status === 'usable' && !isStrainsLoading && !strainsError ? (
              <div className="create-box-selected-strain">
                <div className="create-box-selected-strain-identity">
                  <span><em>{selectedStrain.species.scientific_name}</em></span>
                  <span>
                    <strong>{selectedStrain.name.trim() || `${t('createBoxStrainLabel')} ${selectedStrain.identity.strain_number}`}</strong>
                    {' '}({selectedStrain.code})
                  </span>
                </div>
                <span className="sr-only" role="status">{t('createBoxStrainFixed')}</span>
              </div>
            ) : null}
            {!lockStrain && hasCurrentStrains && strainOptions?.length && !strainsError ? (
              <StrainPicker
                options={strainOptions}
                selectedId={strainId}
                disabled={isLocked}
                onSelect={(id) => {
                  setStrainId(id);
                  setError(null);
                }}
                t={t}
              />
            ) : null}
          </fieldset>



          <label className="create-box-location-field">
            <span>{t('createBoxZone')}</span>
            <select disabled={isLocked} required value={zoneId ?? ''} onChange={(event) => setZoneId(event.target.value ? Number(event.target.value) : null)}>
              <option value="" disabled>{isOptionsLoading ? t('loading') : t('createBoxSelectZone')}</option>
              {availableZones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </select>
            {!isOptionsLoading && exportOptions && !availableZones.length
              ? <small className="compact-text">{t('createBoxNoLocations')}</small> : null}
          </label>

          <label className="create-box-date-field">
            <span>{t('createBoxEnteredOn')}</span>
            <input disabled={isLocked} required type="date" value={enteredOn} onChange={(event) => setEnteredOn(event.target.value)} />
          </label>

          <label className="create-box-initial-polyps-field">
            <span>{t('createBoxInitialPolyps')}</span>
            <input
              disabled={isLocked} required type="number" min={0} step={1} inputMode="numeric"
              value={initialPolyps}
              aria-invalid={isInitialPolypsTouched && initialPolypCount === null}
              aria-describedby={isInitialPolypsTouched && initialPolypCount === null
                ? 'create-box-initial-polyps-error' : undefined}
              onBlur={() => setIsInitialPolypsTouched(true)}
              onChange={(event) => setInitialPolyps(event.target.value)}
            />
            {isInitialPolypsTouched && initialPolypCount === null ? (
              <small id="create-box-initial-polyps-error" className="inline-error compact-text">{t('createBoxInitialPolypsRequired')}</small>
            ) : null}
          </label>

          <label className="create-box-notes-field">
            <span>{t('createBoxNotes')}</span>
            <textarea disabled={isLocked} rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>

          <div className="create-box-actions">
            <div className="create-box-feedback">
              {hasUnverifiedPrevious ? (
                <p className="create-box-notice create-box-unverified" role="status">{t('createBoxPreviousUnverified')}</p>
              ) : null}
              {statusNotice}
              {error ? <p className="inline-error create-box-error" role="alert">{error}</p> : null}
            </div>
            <button className="primary-button create-box-submit" type="submit" disabled={!canSubmit}>
              {phase === 'sending' || phase === 'waiting' ? t('createBoxCreating') : t('createBoxSubmit')}
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );

  if (presentation === 'modal') {
    if (!isOpen) return null;
    return (
      <ModalPortal>
        <div className="modal-backdrop create-box-backdrop" role="presentation" onMouseDown={() => setOpen(false)}>
          <section
            ref={dialogRef}
            className="create-box-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-box-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="modal-heading create-box-modal-heading">
              <h2 id="create-box-modal-title">{t('createBoxTitle')}</h2>
              <button className="modal-close-button" type="button" aria-label={t('close')} onClick={() => setOpen(false)}>
                <PolypbaseIcon name="close" size={18} />
              </button>
            </header>
            <div className="create-box-modal-content">{panel}</div>
          </section>
        </div>
      </ModalPortal>
    );
  }

  return panel;
}
