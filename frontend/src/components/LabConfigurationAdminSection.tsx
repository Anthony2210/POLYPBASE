import { type FormEvent, useEffect, useId, useMemo, useRef, useState } from 'react';

import { ApiError, ApiResourceCancelledError } from '../api/client';
import type { TranslationKey, Translator } from '../i18n';
import type { ThermalZone } from '../types';
import type { ThermalZoneUpdatePayload, ThermalZonePayload } from '../types/admin';
import { formatTemperature } from '../utils/environmentFormat';
import { getErrorMessage } from '../utils/errors';
import {
  buildLabLocationDraft,
  compareLabLocations,
  formatLabSalinity,
  getChangedLabLocationValues,
  getLabLocationApiFieldErrors,
  parseLabLocationDraft,
  stepLabCapacity,
  stepLabSalinity,
  type LabLocationDraft,
  type LabLocationField,
  type LabLocationValidationError,
} from '../utils/labLocations';
import { getMemberRowClassName, MEMBER_FEEDBACK_MS } from '../utils/memberFeedback';
import AdminActionPanel from './AdminActionPanel';
import PolypbaseIcon from './PolypbaseIcon';

// Capacity is added a rack at a time, and salinity is read off a refractometer
// that lands on round values, so the -/+ buttons move in the steps technicians
// work in. Typing stays free.
const CAPACITY_STEP = 10;
const SALINITY_STEP = 5;

const VALIDATION_MESSAGES: Record<Exclude<LabLocationValidationError, 'required'>, TranslationKey> = {
  capacity: 'adminLabCapacityInvalid',
  temperature: 'adminLabTemperatureInvalid',
  salinity: 'adminLabSalinityInvalid',
};

type EditorState = { mode: 'create' } | { mode: 'edit'; zone: ThermalZone };

export default function LabConfigurationAdminSection({
  organizationId,
  zones,
  onCreateZone,
  onUpdateZone,
  onOpenZone,
  t,
}: {
  organizationId: number;
  zones: ThermalZone[];
  onCreateZone: (payload: ThermalZonePayload) => Promise<{ id: number }>;
  onUpdateZone: (zoneId: number, payload: ThermalZoneUpdatePayload) => Promise<void>;
  onOpenZone: (zoneId: number) => void;
  t: Translator;
}) {
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [savedZoneId, setSavedZoneId] = useState<number | null>(null);
  const feedbackTimerRef = useRef<number | null>(null);

  // The list is already scoped to the active institution by the API; the
  // filter only guards against a stale collection during a switch.
  const locations = useMemo(
    () => zones.filter((zone) => zone.organization.id === organizationId).sort(compareLabLocations),
    [organizationId, zones],
  );

  useEffect(() => () => {
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
  }, []);

  function showSaved(zoneId: number) {
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
    setSavedZoneId(zoneId);
    feedbackTimerRef.current = window.setTimeout(() => setSavedZoneId(null), MEMBER_FEEDBACK_MS);
  }

  const editedZone = editor?.mode === 'edit'
    ? locations.find((zone) => zone.id === editor.zone.id) ?? editor.zone
    : null;

  return (
    <section className="admin-section lab-configuration" id="admin-environment">
      <div className="admin-section-heading account-management-heading section-title">
        <div>
          <h2>{t('adminLabLocationsTitle')}</h2>
        </div>
        <button className="admin-primary-action" type="button" onClick={() => setEditor({ mode: 'create' })}>
          <PolypbaseIcon name="plus" size={18} />
          {t('adminLabAddLocation')}
        </button>
      </div>

      {editor ? (
        <LabLocationEditor
          key={editor.mode === 'edit' ? editor.zone.id : 'create'}
          zone={editedZone}
          organizationId={organizationId}
          onCancel={() => setEditor(null)}
          onCreate={onCreateZone}
          onUpdate={onUpdateZone}
          onSaved={(zoneId) => {
            setEditor(null);
            showSaved(zoneId);
          }}
          t={t}
        />
      ) : null}

      <p className="sr-only" role="status">{savedZoneId !== null ? t('adminLabSaved') : ''}</p>

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
                <th scope="col" className="member-action-heading">
                  <span className="sr-only">{t('adminLabEditLocation')}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {locations.map((zone) => (
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
                    <button
                      className="secondary-button lab-location-edit"
                      type="button"
                      aria-label={t('adminLabEditTitle').replace('{zone}', zone.name)}
                      onClick={() => setEditor({ mode: 'edit', zone })}
                    >
                      {t('adminLabEditLocation')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted compact-text">{t('adminLabNoLocations')}</p>
      )}
    </section>
  );
}

export function LabLocationEditor({
  zone,
  organizationId,
  onCancel,
  onCreate,
  onUpdate,
  onSaved,
  t,
}: {
  zone: ThermalZone | null;
  organizationId: number;
  onCancel: () => void;
  onCreate: (payload: ThermalZonePayload) => Promise<{ id: number }>;
  onUpdate: (zoneId: number, payload: ThermalZoneUpdatePayload) => Promise<void>;
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
  const title = zone ? t('adminLabEditTitle').replace('{zone}', zone.name) : t('adminLabAddLocation');

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
      let savedId: number;
      if (zone) {
        const payload = getChangedLabLocationValues(zone, result.values);
        if (!Object.keys(payload).length) {
          onCancel();
          return;
        }
        await onUpdate(zone.id, payload);
        savedId = zone.id;
      } else {
        const created = await onCreate({ organization: organizationId, ...result.values });
        savedId = created.id;
      }
      focusAfterSaveRef.current = null;
      if (isMountedRef.current) onSaved(savedId);
    } catch (requestError) {
      // An institution switch discards the pending form: nothing to report.
      if (requestError instanceof ApiResourceCancelledError || !isMountedRef.current) return;
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

  return (
    <AdminActionPanel title={title} closeLabel={t('close')} onClose={closeUnlessSaving}>
      <form className="lab-location-form" onSubmit={handleSubmit} noValidate>
        {/* A disabled fieldset freezes every field and stepper while the request runs,
            so the submitted draft is exactly what the server receives. */}
        <fieldset className="lab-location-fields member-add-grid" disabled={isSaving}>
          <div className="lab-location-field admin-wide-field">
            <label htmlFor={fieldId('name')}>{t('adminZoneName')}</label>
            <input
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
            <label htmlFor={fieldId('capacity')}>{t('adminLabMaxCapacity')}</label>
            <LabStepField
              ariaPrefix={t('adminLabMaxCapacity')}
              inputMode="numeric"
              step={CAPACITY_STEP}
              stepValue={stepLabCapacity}
              value={draft.capacity}
              onChange={(value) => updateField('capacity', value)}
              inputProps={inputProps('capacity')}
            />
            {fieldError('capacity')}
          </div>
          <div className="lab-location-field">
            <label htmlFor={fieldId('targetTemperature')}>{t('adminLabTargetTemperatureField')}</label>
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
            <label htmlFor={fieldId('salinity')}>{t('adminLabReferenceSalinity')}</label>
            <LabStepField
              ariaPrefix={t('adminLabReferenceSalinity')}
              inputMode="decimal"
              step={SALINITY_STEP}
              stepValue={stepLabSalinity}
              value={draft.salinity}
              onChange={(value) => updateField('salinity', value)}
              inputProps={inputProps('salinity')}
            />
            {fieldError('salinity')}
          </div>
        </fieldset>

        {formError ? <p className="inline-error" role="alert">{formError}</p> : null}

        <footer className="lab-location-form-actions">
          <button className="secondary-button" type="button" disabled={isSaving} onClick={onCancel}>
            {t('cancel')}
          </button>
          <button
            className="primary-button"
            type="submit"
            disabled={isSaving || !draft.name.trim() || !hasChanges}
          >
            {isSaving ? t('saving') : t('adminSaveZone')}
          </button>
        </footer>
      </form>
    </AdminActionPanel>
  );
}

/**
 * Text field with -/+ buttons moving by `step`. A text input (not a number
 * input) keeps an unreadable entry visible so it is reported, instead of being
 * silently read as empty, which would clear the configured value.
 */
function LabStepField({
  ariaPrefix,
  inputMode,
  step,
  stepValue,
  value,
  onChange,
  inputProps,
}: {
  ariaPrefix: string;
  inputMode: 'numeric' | 'decimal';
  step: number;
  stepValue: (value: string, increment: number) => string;
  value: string;
  onChange: (next: string) => void;
  inputProps: Record<string, unknown>;
}) {
  return (
    <div className="zone-step-field">
      <button type="button" aria-label={`${ariaPrefix} -${step}`} onClick={() => onChange(stepValue(value, -step))}>
        -
      </button>
      <input
        type="text"
        inputMode={inputMode}
        autoComplete="off"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        {...inputProps}
      />
      <button type="button" aria-label={`${ariaPrefix} +${step}`} onClick={() => onChange(stepValue(value, step))}>
        +
      </button>
    </div>
  );
}
