import { ArrowRight, Pencil } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Language, TranslationKey, Translator } from '../i18n';
import type { BoxDetail, BoxItem, BoxMovement, BoxMovementCorrectionPayload, ThermalZone } from '../types';
import {
  canCorrectMovement,
  correctableLifecycleMovement,
  formatMovementTimestamp,
  isLifecycleMovement,
  latestPersistedMovement,
  movementCorrectionCapabilities,
  movementCorrectionErrorCode,
  movementCorrectionErrorKey,
  movementKindKey,
  parisMovementWeekBounds,
  resolveParisMovementTime,
  toParisMovementInput,
} from '../utils/boxMovementCorrection';

import MeasurementSaveButton from './MeasurementSaveButton';

function MovementZoneMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric movement-zone">
      <small>{label}</small>
      <strong>{value}</strong>
    </div>
  );
}

export type LatestMovementBannerProps = {
  box: BoxItem | BoxDetail;
  zones: ThermalZone[];
  language: Language;
  canEdit: boolean;
  isDesktop: boolean;
  onCorrect: (boxId: number, movementId: number, payload: BoxMovementCorrectionPayload) => Promise<void>;
  onRefresh: (boxId: number) => Promise<BoxDetail>;
  isOperationCurrent: () => boolean;
  /** Opens the editor for a lifecycle movement chosen in the movement history. */
  correctionRequest?: { movementId: number } | null;
  onCorrectionRequestHandled?: () => void;
  t: Translator;
};

type MovementDraft = {
  movement: BoxMovement;
  capabilities: ReturnType<typeof movementCorrectionCapabilities>;
  time: string;
  destination: number;
  notes: string;
};

/** A box/organization switch discards drafts synchronously and invalidates old continuations. */
export default function LatestMovementBanner(props: LatestMovementBannerProps) {
  return <MovementBannerState key={`${props.box.organization.id}:${props.box.id}`} {...props} />;
}

function MovementBannerState({ box, zones, language, canEdit, isDesktop, onCorrect, onRefresh, isOperationCurrent, correctionRequest, onCorrectionRequestHandled, t }: LatestMovementBannerProps) {
  const [refreshed, setRefreshed] = useState<{ base: BoxItem | BoxDetail; detail: BoxDetail } | null>(null);
  const [draft, setDraft] = useState<MovementDraft | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<TranslationKey | null>(null);
  const [saved, setSaved] = useState(false);
  const [reviewRequired, setReviewRequired] = useState(false);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const mounted = useRef(false);
  const busy = useRef(false);
  const latestProps = useRef({ box, isOperationCurrent });
  latestProps.current = { box, isOperationCurrent };
  const editButton = useRef<HTMLButtonElement>(null);

  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const displayedBox = refreshed?.base === box ? refreshed.detail : box;
  // Lifecycle entries remain in the history, not in the last-movement summary.
  const movement = latestPersistedMovement(displayedBox, (entry) => !isLifecycleMovement(entry));
  const capabilities = movement ? movementCorrectionCapabilities(displayedBox, movement) : null;
  // A lifecycle draft is compared with the newest movement overall, an ordinary one with the banner movement.
  const draftReference = draft && isLifecycleMovement(draft.movement) ? latestPersistedMovement(displayedBox) : movement;
  const draftChanged = Boolean(draft && (draft.movement.id !== draftReference?.id
    || draft.movement.revision !== draftReference?.revision));
  const locked = pending || !canEdit || reviewRequired || refreshRequired || draftChanged;
  const availableZones = zones.filter((zone) => zone.is_active && zone.organization.id === box.organization.id
    && zone.id !== draft?.movement.from_thermal_zone?.id);
  const canOpen = canEdit && capabilities && canCorrectMovement(capabilities) && !pending && !refreshRequired;
  const draftTime = draft?.capabilities.date ? resolveParisMovementTime(draft.time, draft.movement.moved_at) : null;
  const weekBounds = draft?.capabilities.date ? parisMovementWeekBounds(draft.movement.moved_at) : null;
  const hasChanges = Boolean(draft && (
    draft.notes.trim() !== draft.movement.notes
    || (draftTime && (draftTime.error || draftTime.timestamp !== draft.movement.moved_at))
    || (draft.capabilities.destination && draft.destination !== draft.movement.to_thermal_zone.id)
  ));

  function openEditor(target = movement) {
    const targetCapabilities = target ? movementCorrectionCapabilities(displayedBox, target) : null;
    if (!target || !targetCapabilities || !canEdit || !canCorrectMovement(targetCapabilities) || pending || refreshRequired
      || !isOperationCurrent()) return;
    setDraft({
      movement: { ...target }, capabilities: targetCapabilities,
      time: toParisMovementInput(target.moved_at) ?? '',
      destination: target.to_thermal_zone.id, notes: target.notes,
    });
    setError(null);
    setSaved(false);
    setReviewRequired(false);
  }

  useEffect(() => {
    if (!correctionRequest) return;
    const target = correctableLifecycleMovement(displayedBox);
    if (target?.id === correctionRequest.movementId) {
      // After a saved correction whose refresh failed, reopening only recovers the data; it never writes again.
      if (refreshRequired) { if (!pending) void retryRefresh(); } else openEditor(target);
    }
    onCorrectionRequestHandled?.();
  }, [correctionRequest]);

  function closeEditor() {
    if (busy.current) return;
    setDraft(null);
    setReviewRequired(false);
    setError(refreshRequired ? 'movementErrorRefresh' : null);
    // The summary replaces the editor before restoring keyboard focus.
    requestAnimationFrame(() => { if (mounted.current) editButton.current?.focus(); });
  }

  function operationGuard() {
    const originalGuard = isOperationCurrent;
    return () => mounted.current && originalGuard() && latestProps.current.isOperationCurrent();
  }

  async function refreshSummary(current: () => boolean) {
    try {
      const detail = await onRefresh(box.id);
      if (!current()) return false;
      if (detail.id !== box.id || detail.organization.id !== box.organization.id) {
        throw new Error('Unexpected box refresh scope.');
      }
      setRefreshed({ base: latestProps.current.box, detail });
      setRefreshRequired(false);
      return true;
    } catch {
      if (current()) {
        setRefreshRequired(true);
        setError('movementErrorRefresh');
      }
      return false;
    }
  }

  async function retryRefresh() {
    if (busy.current || !isOperationCurrent()) return;
    const current = operationGuard();
    busy.current = true;
    setPending(true);
    setError(null);
    const refreshedSuccessfully = await refreshSummary(current);
    if (current()) {
      busy.current = false;
      setPending(false);
      if (saved && refreshedSuccessfully) {
        setDraft(null);
        setReviewRequired(false);
        requestAnimationFrame(() => { if (current()) editButton.current?.focus(); });
      }
    }
  }

  async function saveCorrection(): Promise<boolean> {
    if (!draft || !hasChanges || locked || busy.current || !isOperationCurrent()) return false;
    const payload: BoxMovementCorrectionPayload = { expected_revision: draft.movement.revision! };
    if (draft.notes.trim() !== draft.movement.notes) payload.notes = draft.notes.trim();
    if (draft.capabilities.date) {
      const result = resolveParisMovementTime(draft.time, draft.movement.moved_at);
      if (result.error) { setError(result.error); return false; }
      if (result.timestamp !== draft.movement.moved_at) payload.moved_at = result.timestamp;
    }
    if (draft.capabilities.destination && draft.destination !== draft.movement.to_thermal_zone.id) {
      if (!availableZones.some((zone) => zone.id === draft.destination)) {
        setError('movementErrorValidation');
        return false;
      }
      payload.to_thermal_zone_id = draft.destination;
    }
    if (Object.keys(payload).length === 1) return false;
    const current = operationGuard();
    busy.current = true;
    setPending(true);
    setError(null);
    try {
      await onCorrect(box.id, draft.movement.id, payload);
    } catch (failure: unknown) {
      if (!current()) return false;
      setError(movementCorrectionErrorKey(failure));
      if (movementCorrectionErrorCode(failure) === 'box_movement_changed') {
        // Keep the submitted snapshot frozen; refresh never rebases its revision.
        setReviewRequired(true);
        setRefreshRequired(true);
        await refreshSummary(current);
      }
      if (current()) {
        busy.current = false;
        setPending(false);
      }
      return false;
    }
    if (!current()) return false;
    setSaved(true);
    setReviewRequired(true);
    setRefreshRequired(true);
    const refreshedSuccessfully = await refreshSummary(current);
    if (current()) {
      busy.current = false;
      setPending(false);
      if (refreshedSuccessfully) {
        setDraft(null);
        setReviewRequired(false);
        requestAnimationFrame(() => { if (current()) editButton.current?.focus(); });
      }
    }
    return refreshedSuccessfully;
  }

  const feedback = <>
    {saved ? <p className="movement-banner-feedback" role="status">{t('movementSaved')}</p> : null}
    {error ? <p className="inline-error movement-banner-feedback" role="alert">{t(error)}</p> : null}
    {pending ? <p className="movement-banner-feedback" role="status">{t(saved || reviewRequired || refreshRequired ? 'loading' : 'saving')}</p> : null}
    {refreshRequired && !pending ? (
      <button type="button" className="secondary-button" onClick={() => void retryRefresh()}>{t('movementRefresh')}</button>
    ) : null}
  </>;

  if (draft) {
    const id = `movement-${box.organization.id}-${box.id}-${draft.movement.id}`;
    return (
      <section className="box-section measurement-form-section measurement-module is-expanded latest-movement-editor" aria-busy={pending}>
        <form className="fake-form" onSubmit={(event) => { event.preventDefault(); if (isDesktop) void saveCorrection(); }}>
          <div className="section-title">
            <div>
              <h2>{t('movementCorrect')}</h2>
              {draft.movement.kind !== 'ordinary' && draft.movement.kind !== 'unclassified' ? <p className="movement-kind-label">{t(movementKindKey(draft.movement))}</p> : null}
            </div>
          </div>
          <fieldset className="measurement-editor-fields" disabled={locked}>
            <div className="movement-correction-fields">
              <label htmlFor={`${id}-origin`}>
                {t('movementOrigin')}
                <input id={`${id}-origin`} readOnly value={draft.movement.from_thermal_zone?.name ?? t('movementNoOrigin')} />
              </label>
              <label htmlFor={`${id}-destination`}>
                {t('movementDestination')}
                {draft.capabilities.destination ? (
                  <select id={`${id}-destination`} value={draft.destination} required
                    onChange={(event) => setDraft({ ...draft, destination: Number(event.target.value) })}>
                    {!availableZones.some((zone) => zone.id === draft.movement.to_thermal_zone.id) ? (
                      <option value={draft.movement.to_thermal_zone.id}>{draft.movement.to_thermal_zone.name}</option>
                    ) : null}
                    {availableZones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
                  </select>
                ) : <input id={`${id}-destination`} readOnly value={draft.movement.to_thermal_zone.name} />}
              </label>
            </div>
            <div className="movement-correction-details">
              <label htmlFor={`${id}-date`}>
                {t('movementDate')}
                {draft.capabilities.date ? (
                  <input id={`${id}-date`} type="datetime-local" step="1" required value={draft.time}
                    min={weekBounds?.min} max={weekBounds?.max} autoFocus
                    onChange={(event) => setDraft({ ...draft, time: event.target.value })} />
                ) : <input id={`${id}-date`} readOnly value={formatMovementTimestamp(draft.movement.moved_at, language)} />}
              </label>
              <label className="notes-field" htmlFor={`${id}-notes`}>
                {t('movementNotes')}
                <textarea id={`${id}-notes`} value={draft.notes} autoFocus={!draft.capabilities.date} rows={3}
                  onChange={(event) => setDraft({ ...draft, notes: event.target.value })} />
              </label>
            </div>
          </fieldset>
          {feedback}
          {!pending && (reviewRequired || draftChanged) ? <p className="measurement-lock-note" role="status">{t('movementReviewReopen')}</p> : null}
          <div className="measurement-actions-row">
            {hasChanges ? <MeasurementSaveButton
              isDesktop={isDesktop}
              isDisabled={locked}
              isSaving={pending}
              labels={{ hold: t('holdToUpdate'), save: t('movementSave'), saving: t(saved ? 'loading' : 'saving') }}
              onSave={saveCorrection}
            /> : null}
            <button type="button" className="secondary-button measurement-cancel-button" disabled={pending} onClick={closeEditor}>
              {t(reviewRequired || draftChanged ? 'movementReview' : 'cancel')}
            </button>
          </div>
        </form>
      </section>
    );
  }

  // A lifecycle-only box has no banner, but a pending refresh recovery must stay reachable after the editor closes.
  if (!movement) return refreshRequired ? <div className="latest-movement-recovery">{feedback}</div> : null;

  return (
    <section className={`last-reading-card measurement-summary measurement-module latest-movement-banner${canOpen ? ' has-edit-capability' : ''}`} aria-busy={pending}>
      <div>
        <h2>{t('latestMovement')}</h2>
        <time dateTime={movement.moved_at}>{formatMovementTimestamp(movement.moved_at, language)}</time>
        {movement.kind !== 'ordinary' ? <p className="movement-kind-label">{t(movementKindKey(movement))}</p> : null}
      </div>
      <>
        <div className="movement-route">
          <MovementZoneMetric label={t('movementOrigin')} value={movement.from_thermal_zone?.name ?? t('movementNoOrigin')} />
          <ArrowRight className="movement-route-arrow" aria-hidden="true" size={20} />
          <MovementZoneMetric label={t('movementDestination')} value={movement.to_thermal_zone.name} />
        </div>
        <div className="last-reading-comment">
          <div className="last-reading-comment-header"><small>{t('movementNotes')}</small></div>
          <p>{movement.notes || t('movementNoNotes')}</p>
        </div>
        {canOpen ? <button ref={editButton} className="icon-button measurement-summary-edit-button" type="button"
          aria-label={t('movementCorrect')} title={t('movementCorrect')} onClick={() => openEditor()}>
          <Pencil aria-hidden="true" size={18} />
        </button> : null}
      </>
      {feedback}
    </section>
  );
}
