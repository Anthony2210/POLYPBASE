import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { ApiError, ApiResourceCancelledError } from '../api/client';
import useMutationDialog from '../hooks/useMutationDialog';
import type { TranslationKey, Translator } from '../i18n';
import type { ThermalZone } from '../types';
import type {
  ThermalZoneDeletionEligibility,
  ThermalZoneUpdatePayload,
  ThermalZonePayload,
} from '../types/admin';
import { formatTemperature } from '../utils/environmentFormat';
import { getErrorMessage } from '../utils/errors';
import {
  buildLabLocationDraft,
  compareLabLocations,
  formatLabSalinity,
  getChangedLabLocationValues,
  getLabLocationApiFieldErrors,
  getLabLocationDeletionRefusal,
  parseLabLocationDraft,
  type LabLocationDeletionReason,
  type LabLocationDraft,
  type LabLocationField,
  type LabLocationValidationError,
  type LabLocationValues,
} from '../utils/labLocations';
import { getMemberRowClassName, MEMBER_FEEDBACK_MS } from '../utils/memberFeedback';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';

const VALIDATION_MESSAGES: Record<Exclude<LabLocationValidationError, 'required'>, TranslationKey> = {
  capacity: 'adminLabCapacityInvalid',
  temperature: 'adminLabTemperatureInvalid',
  salinity: 'adminLabSalinityInvalid',
};

export default function LabConfigurationAdminSection({
  organizationId,
  zones,
  onCreateZone,
  onUpdateZone,
  onLoadZoneDeletion,
  onDeleteZone,
  onOpenZone,
  t,
}: {
  organizationId: number;
  zones: ThermalZone[];
  onCreateZone: (payload: ThermalZonePayload) => Promise<{ id: number }>;
  onUpdateZone: (zoneId: number, payload: ThermalZoneUpdatePayload) => Promise<void>;
  onLoadZoneDeletion: (zoneId: number) => Promise<ThermalZoneDeletionEligibility>;
  onDeleteZone: (zoneId: number) => Promise<void>;
  onOpenZone: (zoneId: number) => void;
  t: Translator;
}) {
  const [isCreating, setIsCreating] = useState(false);
  const [editingZoneId, setEditingZoneId] = useState<number | null>(null);
  const [deletingZoneId, setDeletingZoneId] = useState<number | null>(null);
  const [savedZoneId, setSavedZoneId] = useState<number | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const feedbackTimerRef = useRef<number | null>(null);
  const addButtonRef = useRef<HTMLButtonElement>(null);
  const editButtonsRef = useRef(new Map<number, HTMLButtonElement>());
  // Row whose pencil gets focus back once its inline editor closes.
  const returnFocusZoneIdRef = useRef<number | null>(null);

  // The list is already scoped to the active institution by the API; the
  // filter only guards against a stale collection during a switch.
  const locations = useMemo(
    () => zones.filter((zone) => zone.organization.id === organizationId).sort(compareLabLocations),
    [organizationId, zones],
  );
  // A row that left the collection no longer holds the single edit slot.
  const editingZone = locations.find((zone) => zone.id === editingZoneId) ?? null;
  const deletingZone = locations.find((zone) => zone.id === deletingZoneId) ?? null;

  useEffect(() => () => {
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
  }, []);

  useEffect(() => {
    if (editingZoneId !== null || returnFocusZoneIdRef.current === null) return;
    const button = editButtonsRef.current.get(returnFocusZoneIdRef.current);
    returnFocusZoneIdRef.current = null;
    if (button?.isConnected) button.focus();
  }, [editingZoneId]);

  function showSaved(zoneId: number) {
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
    setSavedZoneId(zoneId);
    setAnnouncement(t('adminLabSaved'));
    feedbackTimerRef.current = window.setTimeout(() => setSavedZoneId(null), MEMBER_FEEDBACK_MS);
  }

  async function deleteZone(zoneId: number) {
    // Resolves once the server deleted the location and the list was reloaded.
    await onDeleteZone(zoneId);
    setDeletingZoneId(null);
    setAnnouncement(t('adminLabDeleted'));
    // The deleted row and its trash button are gone: keep focus in the section.
    addButtonRef.current?.focus();
  }

  function stopEditing(zoneId: number) {
    returnFocusZoneIdRef.current = zoneId;
    setEditingZoneId(null);
  }

  return (
    <section className="admin-section lab-configuration" id="admin-environment">
      <div className="admin-section-heading account-management-heading section-title">
        <div>
          <h2>{t('adminLabLocationsTitle')}</h2>
        </div>
        <button ref={addButtonRef} className="admin-primary-action" type="button" onClick={() => setIsCreating(true)}>
          <PolypbaseIcon name="plus" size={18} />
          {t('adminLabAddLocation')}
        </button>
      </div>

      {isCreating ? (
        <LabLocationCreateDialog
          organizationId={organizationId}
          onCancel={() => setIsCreating(false)}
          onCreate={onCreateZone}
          onSaved={(zoneId) => {
            setIsCreating(false);
            showSaved(zoneId);
          }}
          t={t}
        />
      ) : null}

      {deletingZone ? (
        <LabLocationDeleteDialog
          zone={deletingZone}
          onClose={() => setDeletingZoneId(null)}
          onLoadDeletion={onLoadZoneDeletion}
          onDelete={deleteZone}
          t={t}
        />
      ) : null}

      <p className="sr-only" role="status">{announcement}</p>

      {locations.length ? (
        <div className="member-table-shell">
          <table className="member-table lab-location-table">
            <thead>
              <tr>
                <th scope="col">{t('zoneLabel')}</th>
                <th scope="col">{t('adminLabActiveBoxes')}</th>
                <th scope="col">{t('adminLabMaxCapacity')}</th>
                <th scope="col">{t('adminTargetTemperature')}</th>
                <th scope="col">{t('adminLabReferenceSalinity')}</th>
                <th scope="col" className="member-action-heading" />
              </tr>
            </thead>
            <tbody>
              {locations.map((zone) => (zone.id === editingZone?.id ? (
                <LabLocationEditRow
                  key={zone.id}
                  zone={zone}
                  onCancel={() => stopEditing(zone.id)}
                  onUpdate={onUpdateZone}
                  onSaved={(zoneId) => {
                    stopEditing(zoneId);
                    showSaved(zoneId);
                  }}
                  t={t}
                />
              ) : (
                <tr
                  key={zone.id}
                  className={getMemberRowClassName(zone.is_active, savedZoneId === zone.id ? 'positive' : null)}
                >
                  <td>
                    <span className="member-identity">
                      <a
                        href={`/zones/${zone.id}`}
                        onClick={(event) => {
                          if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                          event.preventDefault();
                          onOpenZone(zone.id);
                        }}
                      >
                        <strong>{zone.name}</strong>
                      </a>
                      {zone.is_active ? null : <small>{t('adminLabInactive')}</small>}
                    </span>
                  </td>
                  <td className="lab-location-number">{zone.box_count}</td>
                  <td className="lab-location-number">{zone.capacity ?? '-'}</td>
                  <td className="lab-location-number">{formatTemperature(zone.target_temperature_c)}</td>
                  <td className="lab-location-number">{formatLabSalinity(zone.salinity_psu)}</td>
                  <td className="member-action-cell">
                    <span className="lab-location-actions">
                      {/* One row is edited at a time: other pencils wait for it to close. */}
                      <button
                        ref={(node) => {
                          if (node) editButtonsRef.current.set(zone.id, node);
                          else editButtonsRef.current.delete(zone.id);
                        }}
                        className="icon-button lab-location-action"
                        type="button"
                        aria-label={t('adminLabEditTitle').replace('{zone}', zone.name)}
                        title={t('adminLabEditLocation')}
                        disabled={editingZone !== null}
                        onClick={() => setEditingZoneId(zone.id)}
                      >
                        <PolypbaseIcon name="edit" size={16} />
                      </button>
                      <button
                        className="icon-button lab-location-action is-danger"
                        type="button"
                        aria-label={t('adminLabDeleteTitle').replace('{zone}', zone.name)}
                        title={t('adminLabDeleteLocation')}
                        aria-haspopup="dialog"
                        onClick={() => setDeletingZoneId(zone.id)}
                      >
                        <PolypbaseIcon name="delete" size={16} />
                      </button>
                    </span>
                  </td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted compact-text">{t('adminLabNoLocations')}</p>
      )}
    </section>
  );
}

/**
 * Draft, validation and submission shared by the creation dialog and the
 * inline row editor. `save` returns the saved zone id, or null when the
 * draft changes nothing.
 */
function useLabLocationForm({
  zone,
  save,
  onCancel,
  onSaved,
  t,
}: {
  zone: ThermalZone | null;
  save: (values: LabLocationValues) => Promise<number | null>;
  onCancel: () => void;
  onSaved: (zoneId: number) => void;
  t: Translator;
}) {
  const [draft, setDraft] = useState<LabLocationDraft>(() => buildLabLocationDraft(zone));
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<LabLocationField, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const isMountedRef = useRef(true);
  // Control focused at submission: disabled while saving, focused again on failure.
  const focusAfterSaveRef = useRef<HTMLElement | null>(null);
  const idPrefix = useId();
  const fieldId = (field: LabLocationField) => `${idPrefix}-${field}`;

  // Set on every mount: StrictMode runs the cleanup once before remounting.
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (isSaving || !focusAfterSaveRef.current) return;
    const control = focusAfterSaveRef.current;
    focusAfterSaveRef.current = null;
    if (control.isConnected) control.focus();
  }, [isSaving]);

  const parsed = parseLabLocationDraft(draft);
  const changes = zone && parsed.ok ? getChangedLabLocationValues(zone, parsed.values) : null;
  const hasChanges = zone === null || changes === null || Object.keys(changes).length > 0;

  function updateField(field: LabLocationField, value: string) {
    setDraft((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
    setFormError(null);
  }

  function closeUnlessSaving() {
    if (!isSaving) onCancel();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) return;

    const result = parseLabLocationDraft(draft);
    if (!result.ok) {
      setFieldErrors(Object.fromEntries(
        Object.entries(result.errors)
          .filter(([, code]) => code !== 'required')
          .map(([field, code]) => [field, t(VALIDATION_MESSAGES[code as Exclude<LabLocationValidationError, 'required'>])]),
      ));
      return;
    }

    focusAfterSaveRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setIsSaving(true);
    setFieldErrors({});
    setFormError(null);
    try {
      const savedId = await save(result.values);
      focusAfterSaveRef.current = null;
      if (savedId === null) {
        onCancel();
        return;
      }
      if (isMountedRef.current) onSaved(savedId);
    } catch (requestError) {
      // An institution switch discards the pending form: nothing to report.
      if (requestError instanceof ApiResourceCancelledError || !isMountedRef.current) return;
      // The draft is kept as submitted so the value can be corrected.
      const apiFieldErrors = requestError instanceof ApiError ? getLabLocationApiFieldErrors(requestError.data) : {};
      if (Object.keys(apiFieldErrors).length) {
        setFieldErrors(apiFieldErrors);
      } else {
        setFormError(getErrorMessage(requestError));
      }
    } finally {
      if (isMountedRef.current) setIsSaving(false);
    }
  }

  function fieldError(field: LabLocationField) {
    const message = fieldErrors[field];
    return message ? <small className="member-field-error" id={`${fieldId(field)}-error`}>{message}</small> : null;
  }

  function inputProps(field: LabLocationField) {
    return {
      id: fieldId(field),
      ...(fieldErrors[field] ? { 'aria-invalid': true, 'aria-describedby': `${fieldId(field)}-error` } : {}),
    };
  }

  return {
    draft,
    formError,
    isSaving,
    hasChanges,
    idPrefix,
    updateField,
    closeUnlessSaving,
    handleSubmit,
    fieldError,
    inputProps,
  };
}

/** Centered dialog creating a location; the server keeps its default type. */
export function LabLocationCreateDialog({
  organizationId,
  onCancel,
  onCreate,
  onSaved,
  t,
}: {
  organizationId: number;
  onCancel: () => void;
  onCreate: (payload: ThermalZonePayload) => Promise<{ id: number }>;
  onSaved: (zoneId: number) => void;
  t: Translator;
}) {
  const form = useLabLocationForm({
    zone: null,
    save: async (values) => (await onCreate({ organization: organizationId, ...values })).id,
    onCancel,
    onSaved,
    t,
  });
  const { draft, isSaving, updateField, fieldError, inputProps } = form;
  const { dialogRef, initialFocusRef, close } = useMutationDialog<HTMLInputElement>(isSaving, onCancel);
  const titleId = `${form.idPrefix}-title`;
  const labelFor = (field: LabLocationField) => inputProps(field).id;

  return (
    <ModalPortal>
      <div className="modal-backdrop" role="presentation" onMouseDown={close}>
        <section
          ref={dialogRef}
          tabIndex={-1}
          className="lab-location-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-busy={isSaving || undefined}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="lab-location-modal-heading">
            <h2 id={titleId}>{t('adminLabAddLocation')}</h2>
            <button
              className="modal-close-button"
              type="button"
              aria-label={t('close')}
              title={t('close')}
              disabled={isSaving}
              onClick={close}
            >
              <PolypbaseIcon name="close" size={18} />
            </button>
          </header>
          <form className="lab-location-form" onSubmit={form.handleSubmit} noValidate>
            {/* A disabled fieldset freezes every field while the request runs,
                so the submitted draft is exactly what the server receives. */}
            <fieldset className="lab-location-fields" disabled={isSaving}>
              <div className="lab-location-field lab-location-field-wide">
                <label htmlFor={labelFor('name')}>{t('adminZoneName')}</label>
                <input
                  ref={initialFocusRef}
                  required
                  type="text"
                  maxLength={120}
                  autoComplete="off"
                  value={draft.name}
                  onChange={(event) => updateField('name', event.target.value)}
                  {...inputProps('name')}
                />
                {fieldError('name')}
              </div>
              <div className="lab-location-field">
                <label htmlFor={labelFor('capacity')}>{t('adminLabMaxCapacity')}</label>
                <input
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  value={draft.capacity}
                  onChange={(event) => updateField('capacity', event.target.value)}
                  {...inputProps('capacity')}
                />
                {fieldError('capacity')}
              </div>
              <div className="lab-location-field">
                <label htmlFor={labelFor('targetTemperature')}>{t('adminLabTargetTemperatureField')}</label>
                <input
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={draft.targetTemperature}
                  onChange={(event) => updateField('targetTemperature', event.target.value)}
                  {...inputProps('targetTemperature')}
                />
                {fieldError('targetTemperature')}
              </div>
              <div className="lab-location-field">
                <label htmlFor={labelFor('salinity')}>{t('adminLabReferenceSalinity')}</label>
                <input
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={draft.salinity}
                  onChange={(event) => updateField('salinity', event.target.value)}
                  {...inputProps('salinity')}
                />
                {fieldError('salinity')}
              </div>
            </fieldset>

            {form.formError ? <p className="inline-error" role="alert">{form.formError}</p> : null}

            <footer className="lab-location-form-actions">
              <button className="secondary-button" type="button" disabled={isSaving} onClick={onCancel}>
                {t('cancel')}
              </button>
              <button className="primary-button" type="submit" disabled={isSaving || !draft.name.trim()}>
                {isSaving ? t('saving') : t('adminSaveZone')}
              </button>
            </footer>
          </form>
        </section>
      </div>
    </ModalPortal>
  );
}

/**
 * Table row switched to edit mode. The inputs sit in their own cells and are
 * bound to the form of the action cell, so Enter saves and Escape cancels.
 */
export function LabLocationEditRow({
  zone,
  onCancel,
  onUpdate,
  onSaved,
  t,
}: {
  key?: string | number;
  zone: ThermalZone;
  onCancel: () => void;
  onUpdate: (zoneId: number, payload: ThermalZoneUpdatePayload) => Promise<void>;
  onSaved: (zoneId: number) => void;
  t: Translator;
}) {
  const form = useLabLocationForm({
    zone,
    save: async (values) => {
      // Only the fields whose value really changes, so an edit never rewrites the others.
      const payload = getChangedLabLocationValues(zone, values);
      if (!Object.keys(payload).length) return null;
      await onUpdate(zone.id, payload);
      return zone.id;
    },
    onCancel,
    onSaved,
    t,
  });
  const { draft, isSaving, updateField, fieldError, inputProps } = form;
  const formId = `${form.idPrefix}-form`;
  const nameInputRef = useRef<HTMLInputElement>(null);

  useLayoutEffect(() => {
    nameInputRef.current?.focus();
  }, []);

  function handleKeyDown(event: KeyboardEvent<HTMLTableRowElement>) {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    form.closeUnlessSaving();
  }

  function cellInput(
    field: Exclude<LabLocationField, 'name'>,
    label: TranslationKey,
    inputMode: 'numeric' | 'decimal',
  ) {
    return (
      <td className="lab-location-edit-cell">
        <input
          form={formId}
          type="text"
          inputMode={inputMode}
          autoComplete="off"
          aria-label={t(label)}
          disabled={isSaving}
          value={draft[field]}
          onChange={(event) => updateField(field, event.target.value)}
          {...inputProps(field)}
        />
        {fieldError(field)}
      </td>
    );
  }

  return (
    <>
      <tr className="lab-location-edit-row" aria-busy={isSaving || undefined} onKeyDown={handleKeyDown}>
        <td className="lab-location-edit-cell">
          <input
            ref={nameInputRef}
            form={formId}
            required
            type="text"
            maxLength={120}
            autoComplete="off"
            aria-label={t('adminZoneName')}
            disabled={isSaving}
            value={draft.name}
            onChange={(event) => updateField('name', event.target.value)}
            {...inputProps('name')}
          />
          {fieldError('name')}
        </td>
        <td className="lab-location-number">{zone.box_count}</td>
        {cellInput('capacity', 'adminLabMaxCapacity', 'numeric')}
        {cellInput('targetTemperature', 'adminLabTargetTemperatureField', 'decimal')}
        {cellInput('salinity', 'adminLabReferenceSalinity', 'decimal')}
        <td className="member-action-cell">
          <form id={formId} className="lab-location-row-actions" onSubmit={form.handleSubmit} noValidate>
            <button className="secondary-button" type="button" disabled={isSaving} onClick={onCancel}>
              {t('cancel')}
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={isSaving || !draft.name.trim() || !form.hasChanges}
            >
              {isSaving ? t('saving') : t('adminSaveZone')}
            </button>
          </form>
        </td>
      </tr>
      {form.formError ? (
        <tr className="lab-location-edit-feedback">
          <td colSpan={6}>
            <p className="inline-error" role="alert">{form.formError}</p>
          </td>
        </tr>
      ) : null}
    </>
  );
}

type DeletionCheck =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; deletable: boolean; reasons: LabLocationDeletionReason[]; boxCount: number };

const DELETION_REASON_MESSAGES: Record<LabLocationDeletionReason, TranslationKey> = {
  occupied: 'adminLabDeleteOccupied',
  history: 'adminLabDeleteHistory',
};

/**
 * Deletion confirmation. The server deletes only a location that was never
 * used; the dialog reads that eligibility first and offers the destructive
 * action only when it is available. The DELETE request re-checks everything.
 */
export function LabLocationDeleteDialog({
  zone,
  onClose,
  onLoadDeletion,
  onDelete,
  t,
}: {
  zone: ThermalZone;
  onClose: () => void;
  onLoadDeletion: (zoneId: number) => Promise<ThermalZoneDeletionEligibility>;
  onDelete: (zoneId: number) => Promise<void>;
  t: Translator;
}) {
  const [check, setCheck] = useState<DeletionCheck>({ status: 'loading' });
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [checkRequest, setCheckRequest] = useState(0);
  const { dialogRef, initialFocusRef, close } = useMutationDialog<HTMLButtonElement>(isDeleting, onClose);
  const loadRef = useRef(onLoadDeletion);
  loadRef.current = onLoadDeletion;
  const isMountedRef = useRef(true);
  const idPrefix = useId();
  const titleId = `${idPrefix}-title`;
  const countId = `${idPrefix}-count`;
  const messageId = `${idPrefix}-message`;

  // Set on every mount: StrictMode runs the cleanup once before remounting.
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let isCurrent = true;
    setCheck({ status: 'loading' });
    loadRef.current(zone.id).then(
      (eligibility) => {
        if (!isCurrent) return;
        setCheck({
          status: 'ready',
          deletable: eligibility.deletable && eligibility.reasons.length === 0,
          reasons: eligibility.reasons,
          boxCount: eligibility.box_count,
        });
      },
      (error: unknown) => {
        if (!isCurrent || error instanceof ApiResourceCancelledError) return;
        setCheck({ status: 'error', message: getErrorMessage(error) });
      },
    );
    return () => {
      isCurrent = false;
    };
  }, [zone.id, checkRequest]);

  async function confirmDeletion() {
    if (isDeleting || check.status !== 'ready' || !check.deletable) return;
    const boxCountAtConfirmation = check.boxCount;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      await onDelete(zone.id);
    } catch (error) {
      if (error instanceof ApiResourceCancelledError || !isMountedRef.current) return;
      const refusal = error instanceof ApiError ? getLabLocationDeletionRefusal(error.data) : null;
      if (refusal?.code === 'in_use') {
        // The location was used in the meantime: it is kept, say why.
        setCheck({ status: 'ready', deletable: false, reasons: refusal.reasons, boxCount: boxCountAtConfirmation });
      } else if (refusal?.code === 'changed') {
        setDeleteError(t('adminLabDeleteChanged'));
        setCheckRequest((current) => current + 1);
      } else {
        setDeleteError(getErrorMessage(error));
      }
    } finally {
      if (isMountedRef.current) setIsDeleting(false);
    }
  }

  // The count read with the eligibility is fresher than the table row.
  const boxCount = check.status === 'ready' ? check.boxCount : zone.box_count;
  const countMessage = boxCount === 0
    ? t('adminLabDeleteNoBoxes')
    : boxCount === 1
      ? t('adminLabDeleteOneBox')
      : t('adminLabDeleteBoxCount').replace('{count}', String(boxCount));
  const canDelete = check.status === 'ready' && check.deletable;

  return (
    <ModalPortal>
      <div className="modal-backdrop confirm-action-backdrop" role="presentation" onMouseDown={close}>
        <section
          ref={dialogRef}
          tabIndex={-1}
          className={`confirm-action-modal ${canDelete ? 'is-danger' : 'is-default'} lab-location-delete-modal`}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-describedby={`${countId} ${messageId}`}
          aria-busy={check.status === 'loading' || isDeleting || undefined}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="confirm-action-heading">
            <span className="confirm-action-mark" aria-hidden="true">!</span>
            <h2 id={titleId}>{t('adminLabDeleteTitle').replace('{zone}', zone.name)}</h2>
            <button
              className="modal-close-button"
              type="button"
              aria-label={t('close')}
              title={t('close')}
              disabled={isDeleting}
              onClick={close}
            >
              <PolypbaseIcon name="close" size={18} />
            </button>
          </header>
          <p
            className={boxCount > 0 ? 'confirm-action-message lab-location-delete-count is-occupied' : 'confirm-action-message lab-location-delete-count'}
            id={countId}
          >
            {countMessage}
          </p>
          <div className="lab-location-delete-status" id={messageId}>
            {check.status === 'loading' ? (
              <p className="confirm-action-message" role="status">{t('adminLabDeleteChecking')}</p>
            ) : null}
            {check.status === 'error' ? (
              <p className="confirm-action-message lab-location-delete-error" role="alert">
                {t('adminLabDeleteCheckError')} {check.message}
              </p>
            ) : null}
            {check.status === 'ready' && check.deletable ? (
              <p className="confirm-action-message">{t('adminLabDeleteNeverUsed')}</p>
            ) : null}
            {check.status === 'ready' && !check.deletable
              ? check.reasons.map((reason) => (
                <p key={reason} className="confirm-action-message">{t(DELETION_REASON_MESSAGES[reason])}</p>
              ))
              : null}
          </div>
          {deleteError ? (
            <p className="confirm-action-message lab-location-delete-error" role="alert">{deleteError}</p>
          ) : null}
          <footer className="confirm-action-footer">
            <button ref={initialFocusRef} className="confirm-action-cancel" type="button" disabled={isDeleting} onClick={close}>
              {canDelete ? t('cancel') : t('close')}
            </button>
            {check.status === 'error' ? (
              <button className="secondary-button" type="button" onClick={() => setCheckRequest((current) => current + 1)}>
                {t('taxonomyRetry')}
              </button>
            ) : null}
            {/* The destructive action exists only when the server allows it. */}
            {canDelete ? (
              <button
                className="confirm-action-submit is-danger"
                type="button"
                disabled={isDeleting}
                onClick={() => void confirmDeletion()}
              >
                {isDeleting ? t('adminLabDeleting') : t('adminLabDeleteLocation')}
              </button>
            ) : null}
          </footer>
        </section>
      </div>
    </ModalPortal>
  );
}
