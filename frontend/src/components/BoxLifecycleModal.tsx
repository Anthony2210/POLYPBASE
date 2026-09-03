import { type FormEvent, useEffect, useMemo, useState } from 'react';

import { AlertTriangle, X } from 'lucide-react';

import type { Translator } from '../i18n';
import type {
  BoxActivatePayload,
  BoxDeactivatePayload,
  BoxInitialLocationPayload,
  BoxItem,
  BoxLocation,
  BoxQualifyPayload,
  ThermalZone,
} from '../types';
import ModalPortal from './ModalPortal';

export type BoxLifecycleAction = 'qualify' | 'deactivate' | 'reactivate' | 'assign';

export type BoxLifecycleSubmission =
  | { action: 'qualify'; payload: BoxQualifyPayload }
  | { action: 'deactivate'; payload: BoxDeactivatePayload }
  | { action: 'reactivate'; payload: BoxActivatePayload }
  | { action: 'assign'; payload: BoxInitialLocationPayload };

type LifecycleBox = Pick<
  BoxItem,
  'id' | 'global_code' | 'local_code' | 'status' | 'species' | 'thermal_zone'
> & {
  last_location?: BoxLocation | null;
  locations?: BoxLocation[];
};

export default function BoxLifecycleModal({
  action,
  box,
  error,
  initialTargetStatus = 'active',
  isSaving,
  onClose,
  onSubmit,
  t,
  zones,
}: {
  action: BoxLifecycleAction;
  box: LifecycleBox;
  error: string | null;
  initialTargetStatus?: 'active' | 'inactive';
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (submission: BoxLifecycleSubmission) => Promise<void>;
  t: Translator;
  zones: ThermalZone[];
}) {
  const [targetStatus, setTargetStatus] = useState<'active' | 'inactive'>(initialTargetStatus);
  const [zoneId, setZoneId] = useState<number | null>(null);
  const [reason, setReason] = useState('');
  const [reasonMissing, setReasonMissing] = useState(false);
  const [notes, setNotes] = useState('');
  const availableZones = useMemo(
    () => zones.filter((zone) => zone.is_active).sort((first, second) => (
      first.name.localeCompare(second.name, 'fr', { numeric: true, sensitivity: 'base' })
    )),
    [zones],
  );
  const lastLocation = useMemo(() => (
    box.last_location
    ?? [...(box.locations ?? [])].sort((first, second) => (
      second.starts_at.localeCompare(first.starts_at)
    ))[0]
    ?? null
  ), [box.last_location, box.locations]);
  const reusableLastZone = action === 'reactivate' && lastLocation
    ? availableZones.find((zone) => zone.id === lastLocation.thermal_zone.id) ?? null
    : null;
  const isQualification = action === 'qualify';
  const qualifyingAsActive = isQualification && targetStatus === 'active';
  const needsRequiredZone = action === 'reactivate' || action === 'assign';
  const showsZoneChoice = needsRequiredZone || (qualifyingAsActive && box.thermal_zone == null);
  const showsReason = action === 'deactivate' || (isQualification && targetStatus === 'inactive');
  const activeWithoutLocation = qualifyingAsActive && box.thermal_zone == null && zoneId == null;
  const canSubmit = !isSaving
    && (!needsRequiredZone || zoneId != null)
    && (action !== 'deactivate' || Boolean(reason.trim()))
    && (!isQualification || targetStatus === 'active' || Boolean(reason.trim()) || reasonMissing);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !isSaving) onClose();
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isSaving, onClose]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    if (action === 'qualify') {
      await onSubmit({
        action,
        payload: targetStatus === 'active'
          ? {
              target_status: 'active',
              thermal_zone_id: box.thermal_zone == null ? zoneId : undefined,
            }
          : {
              target_status: 'inactive',
              reason: reasonMissing ? '' : reason.trim(),
              reason_missing_from_history: reasonMissing,
            },
      });
      return;
    }

    if (action === 'deactivate') {
      await onSubmit({ action, payload: { reason: reason.trim() } });
      return;
    }

    if (zoneId == null) return;
    await onSubmit({
      action,
      payload: {
        thermal_zone_id: zoneId,
        notes: notes.trim(),
      },
    });
  }

  const titleKey = {
    qualify: 'boxLifecycleQualifyTitle',
    deactivate: 'boxLifecycleDeactivateTitle',
    reactivate: 'boxLifecycleReactivateTitle',
    assign: 'boxLifecycleAssignTitle',
  }[action] as Parameters<Translator>[0];
  const submitKey = {
    qualify: 'boxLifecycleQualifySubmit',
    deactivate: 'boxLifecycleDeactivateSubmit',
    reactivate: 'boxLifecycleReactivateSubmit',
    assign: 'boxLifecycleAssignSubmit',
  }[action] as Parameters<Translator>[0];

  return (
    <ModalPortal>
      <div className="modal-backdrop box-lifecycle-backdrop" role="presentation" onMouseDown={isSaving ? undefined : onClose}>
        <section
          className="box-lifecycle-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="box-lifecycle-title"
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="box-lifecycle-heading">
            <div>
              <span>{t('boxLifecycleKicker')}</span>
              <h2 id="box-lifecycle-title">{t(titleKey)}</h2>
            </div>
            <button type="button" aria-label={t('close')} title={t('close')} disabled={isSaving} onClick={onClose}>
              <X aria-hidden="true" size={19} />
            </button>
          </header>

          <form className="box-lifecycle-form" onSubmit={handleSubmit}>
            <div className="box-lifecycle-identity">
              <span>
                <strong>{box.global_code}</strong>
                <small>{box.species.scientific_name}</small>
              </span>
              {box.thermal_zone ? <em>{box.thermal_zone.name}</em> : null}
            </div>

            {isQualification ? (
              <fieldset className="box-lifecycle-status-choice">
                <legend>{t('boxLifecycleTargetStatus')}</legend>
                <label className={targetStatus === 'active' ? 'is-selected' : ''}>
                  <input
                    type="radio"
                    name="target-status"
                    value="active"
                    checked={targetStatus === 'active'}
                    onChange={() => {
                      setTargetStatus('active');
                      setReason('');
                      setReasonMissing(false);
                    }}
                  />
                  <span>{t('boxLifecycleStatusActive')}</span>
                </label>
                <label className={targetStatus === 'inactive' ? 'is-selected' : ''}>
                  <input
                    type="radio"
                    name="target-status"
                    value="inactive"
                    checked={targetStatus === 'inactive'}
                    onChange={() => {
                      setTargetStatus('inactive');
                      setZoneId(null);
                    }}
                  />
                  <span>{t('boxLifecycleStatusInactive')}</span>
                </label>
              </fieldset>
            ) : null}

            {showsZoneChoice ? (
              <>
                {action === 'reactivate' && lastLocation ? (
                  <div className="box-lifecycle-last-location">
                    <span>
                      <small>{t('boxLifecycleLastKnownLocation')}</small>
                      <strong>{lastLocation.thermal_zone.name}</strong>
                    </span>
                    {reusableLastZone ? (
                      <button type="button" onClick={() => setZoneId(reusableLastZone.id)}>
                        {t('boxLifecycleReuseLastLocation')}
                      </button>
                    ) : (
                      <small>{t('boxLifecycleLastLocationUnavailable')}</small>
                    )}
                  </div>
                ) : null}
                <label className="box-lifecycle-field">
                  <span>
                    {t(needsRequiredZone ? 'boxLifecycleNewLocationRequired' : 'boxLifecycleLocationOptional')}
                  </span>
                  <select
                    value={zoneId ?? ''}
                    required={needsRequiredZone}
                    onChange={(event) => setZoneId(event.target.value ? Number(event.target.value) : null)}
                  >
                    <option value="">
                      {t(needsRequiredZone ? 'boxLifecycleChooseLocation' : 'boxLifecycleKeepWithoutLocation')}
                    </option>
                    {availableZones.map((zone) => (
                      <option key={zone.id} value={zone.id}>{zone.name}</option>
                    ))}
                  </select>
                </label>
              </>
            ) : null}

            {activeWithoutLocation ? (
              <div className="box-lifecycle-warning" role="status">
                <AlertTriangle aria-hidden="true" size={20} />
                <p>{t('boxLifecycleActiveWithoutLocationWarning')}</p>
              </div>
            ) : null}

            {showsReason ? (
              <label className="box-lifecycle-field">
                <span>{t('boxLifecycleReason')}</span>
                <textarea
                  rows={3}
                  required={action === 'deactivate' || !reasonMissing}
                  disabled={reasonMissing}
                  value={reason}
                  placeholder={t('boxLifecycleReasonPlaceholder')}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
            ) : null}

            {isQualification && targetStatus === 'inactive' ? (
              <label className="box-lifecycle-history-check">
                <input
                  type="checkbox"
                  checked={reasonMissing}
                  onChange={(event) => {
                    setReasonMissing(event.target.checked);
                    if (event.target.checked) setReason('');
                  }}
                />
                <span>
                  <strong>{t('boxLifecycleHistoricalReasonMissing')}</strong>
                  <small>{t('boxLifecycleHistoricalReasonMissingHelp')}</small>
                </span>
              </label>
            ) : null}

            {(action === 'reactivate' || action === 'assign') ? (
              <label className="box-lifecycle-field">
                <span>{t('boxLifecycleNotes')}</span>
                <textarea
                  rows={2}
                  value={notes}
                  placeholder={t('boxLifecycleNotesPlaceholder')}
                  onChange={(event) => setNotes(event.target.value)}
                />
              </label>
            ) : null}

            {error ? <p className="inline-error box-lifecycle-error">{error}</p> : null}

            <footer className="box-lifecycle-actions">
              <button className="secondary-button" type="button" disabled={isSaving} onClick={onClose}>
                {t('confirmCancel')}
              </button>
              <button
                className={action === 'deactivate' ? 'primary-button is-danger' : 'primary-button'}
                type="submit"
                disabled={!canSubmit}
              >
                {isSaving ? t('saving') : t(submitKey)}
              </button>
            </footer>
          </form>
        </section>
      </div>
    </ModalPortal>
  );
}
