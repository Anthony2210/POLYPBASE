import { type FormEvent, useMemo, useState } from 'react';

import type { BoxDetail, BoxItem, BoxLocation, BoxMovePayload, ThermalZone } from '../types';
import { createTranslator } from '../i18n';
import useMutationDialog from '../hooks/useMutationDialog';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import './box-utility-dialogs.css';

type Language = 'fr' | 'en';

type Props = {
  box: BoxItem | BoxDetail;
  zones: ThermalZone[];
  language: Language;
  isSaving: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: BoxMovePayload) => Promise<void>;
};


export default function MoveBoxModal({
  box,
  zones,
  language,
  isSaving,
  error,
  onClose,
  onSubmit,
}: Props) {
  const t = createTranslator(language);
  const { dialogRef, initialFocusRef, isBusy, close, submit } = useMutationDialog<HTMLSelectElement>(isSaving, onClose);
  const availableZones = useMemo(
    () => zones.filter(
      (zone) => (
        zone.organization.id === box.organization.id
        && zone.is_active
        && zone.id !== box.thermal_zone?.id
      ),
    ),
    [box.organization.id, box.thermal_zone?.id, zones],
  );
  const [targetZoneId, setTargetZoneId] = useState('');

  const [notes, setNotes] = useState('');
  const selectedZoneId = availableZones.some((zone) => String(zone.id) === targetZoneId)
    ? targetZoneId
    : String(availableZones[0]?.id ?? '');
  const locations = getBoxLocations(box);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isBusy || !selectedZoneId) return;

    await submit(() => onSubmit({
      expected_thermal_zone_id: box.thermal_zone?.id ?? null,
      thermal_zone_id: Number(selectedZoneId),

      notes: notes.trim(),
    }));
  }

  return (
    <ModalPortal>
      <div className="modal-backdrop box-dialog-backdrop" role="presentation" onClick={close}>
        <section
          ref={dialogRef}
          tabIndex={-1}
          aria-busy={isBusy}
          className="move-modal box-dialog box-dialog--move"
          role="dialog"
          aria-modal="true"
          aria-labelledby="move-title"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="subculture-heading box-dialog-heading">
            <div>
              <h2 id="move-title">{t('moveDialogTitle')}</h2>

            </div>
            <button className="icon-button box-dialog-close" type="button" aria-label={t('cancel')} title={t('cancel')} disabled={isBusy} onClick={close}>
              <PolypbaseIcon name="close" size={19} aria-hidden="true" />
            </button>
          </header>

          <form className="move-form box-dialog-form" onSubmit={handleSubmit}>
            <div className="box-dialog-body">
              <div className="move-subject-panel">
                <p className="utility-dialog-identity">
                  <strong>{box.global_code}</strong>
                  <small>{box.species.scientific_name}</small>
                </p>
                <div className="move-current-zone">
                  <span>{t('moveDialogCurrentZone')}</span>
                  <strong>{box.thermal_zone?.name ?? t('noZone')}</strong>

                </div>
              </div>
              <div className="box-dialog-location-flow">
                <label className="move-destination">
                  {t('moveDialogNewZone')}
                  <select
                    ref={initialFocusRef}
                    disabled={isBusy}
                    required
                    name="thermal_zone_id"
                    value={selectedZoneId}
                    onChange={(event) => setTargetZoneId(event.target.value)}
                  >
                    {!availableZones.length ? <option value="">{t('noZone')}</option> : null}
                    {availableZones.map((zone) => (
                      <option key={zone.id} value={zone.id}>
                        {zone.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="move-fields">

                <label className="move-notes">
                  {t('moveDialogNote')}
                  <textarea
                    name="notes"
                    rows={2}
                    disabled={isBusy}
                    placeholder={t('moveDialogNotePlaceholder')}
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                  />
                </label>
              </div>

              {error ? <p className="inline-error subculture-error" role="alert">{error}</p> : null}

              <details className="move-location-history">
                <summary tabIndex={isBusy ? -1 : 0} aria-disabled={isBusy} onClick={(event) => { if (isBusy) event.preventDefault(); }}>
                  {t('movementHistoryTitle')}
                  <span className="move-history-count">{locations.length}</span>
                </summary>
                {!locations.length ? <p className="muted compact-text">{t('moveDialogNoHistory')}</p> : null}
                {locations.map((location) => (
                  <article key={location.id} className="move-location-row">
                    <strong>{location.thermal_zone.name}</strong>
                    <dl className="move-location-dates">
                      <div>
                        <dt>{t('moveHistoryArrival')}</dt>
                        <dd><time dateTime={location.starts_at}>{formatDateTime(location.starts_at, language)}</time></dd>
                      </div>
                      {location.ends_at || location.end_date_unknown ? <div>
                        <dt>{t('moveHistoryDeparture')}</dt>
                        <dd>{location.end_date_unknown
                          ? t('moveDialogUnknownEnd')
                          : location.ends_at
                            ? <time dateTime={location.ends_at}>{formatDateTime(location.ends_at, language)}</time>
                            : t('moveDialogCurrent')}</dd>
                      </div> : null}
                    </dl>
                    {location.notes ? <p>{location.notes}</p> : null}
                  </article>
                ))}
              </details>
            </div>

            <footer className="subculture-actions box-dialog-actions">
              <button className="secondary-button" type="button" disabled={isBusy} onClick={close}>{t('cancel')}</button>
              <button className="primary-button" type="submit" disabled={isBusy || !availableZones.length}>
                {isSaving ? t('saving') : t('moveAction')}
              </button>
            </footer>
          </form>
        </section>
      </div>
    </ModalPortal>
  );
}

function getBoxLocations(box: BoxItem | BoxDetail): BoxLocation[] {
  if ('locations' in box) {
    return box.locations;
  }
  return [];
}


function formatDateTime(value: string, language: Language) {
  return new Intl.DateTimeFormat(language === 'fr' ? 'fr-FR' : 'en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}
