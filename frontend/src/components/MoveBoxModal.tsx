import { type FormEvent, useMemo, useState } from 'react';

import type { BoxDetail, BoxItem, BoxLocation, BoxMovePayload, ThermalZone } from '../types';
import useMutationDialog from '../hooks/useMutationDialog';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';

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

const labels = {
  fr: {
    title: 'Déplacer la boîte',
    box: 'Boîte',
    currentZone: 'Emplacement actuel',
    newZone: 'Nouvel emplacement',
    movedAt: 'Date du déplacement',
    notes: 'Note',
    notesPlaceholder: 'Ex. changement de température, rangement, manipulation',
    history: 'Historique des emplacements',
    current: 'actuel',
    unknownEnd: 'date de fin inconnue',
    noHistory: 'Aucun historique d’emplacement pour cette boîte.',
    cancel: 'Annuler',
    save: 'Déplacer',
    saving: 'Enregistrement...',
    noZone: 'Sans emplacement',
  },
  en: {
    title: 'Move box',
    box: 'Box',
    currentZone: 'Current zone',
    newZone: 'New zone',
    movedAt: 'Movement date',
    notes: 'Note',
    notesPlaceholder: 'For example, temperature change, storage, handling',
    history: 'Location history',
    current: 'current',
    unknownEnd: 'end date unknown',
    noHistory: 'No location history for this box.',
    cancel: 'Cancel',
    save: 'Move',
    saving: 'Saving...',
    noZone: 'No zone',
  },
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
  const text = labels[language];
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
  const [movedAt, setMovedAt] = useState(getCurrentDateTimeValue);
  const [notes, setNotes] = useState('');
  const selectedZoneId = targetZoneId || String(availableZones[0]?.id ?? '');
  const locations = getBoxLocations(box);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isBusy || !selectedZoneId) return;

    await submit(() => onSubmit({
      expected_thermal_zone_id: box.thermal_zone?.id ?? null,
      thermal_zone_id: Number(selectedZoneId),
      moved_at: new Date(movedAt).toISOString(),
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
              <p className="box-dialog-context">{text.box}: {box.global_code}</p>
              <h2 id="move-title">{text.title}</h2>
            </div>
            <button className="icon-button box-dialog-close" type="button" aria-label={text.cancel} title={text.cancel} disabled={isBusy} onClick={close}>
              <PolypbaseIcon name="close" size={19} aria-hidden="true" />
            </button>
          </header>

          <form className="move-form box-dialog-form" onSubmit={handleSubmit}>
            <div className="box-dialog-body">
              <div className="box-dialog-location-flow">
                <div className="current-zone-card">
                  <span>{text.currentZone}</span>
                  <strong>{box.thermal_zone?.name ?? text.noZone}</strong>
                  <small>{box.organization.name}</small>
                </div>
                <label>
                  {text.newZone}
                  <select
                    ref={initialFocusRef}
                    disabled={isBusy}
                    required
                    value={selectedZoneId}
                    onChange={(event) => setTargetZoneId(event.target.value)}
                  >
                    {!availableZones.length ? <option value="">{text.noZone}</option> : null}
                    {availableZones.map((zone) => (
                      <option key={zone.id} value={zone.id}>
                        {zone.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="move-fields">
                <label>
                  {text.movedAt}
                  <input
                    required
                    type="datetime-local"
                    disabled={isBusy}
                    value={movedAt}
                    onChange={(event) => setMovedAt(event.target.value)}
                  />
                </label>
              </div>

              <label className="move-notes">
                {text.notes}
                <textarea
                  rows={3}
                  disabled={isBusy}
                  placeholder={text.notesPlaceholder}
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                />
              </label>

              <section className="location-history">
                <h3>{text.history}</h3>
                {!locations.length ? <p className="muted compact-text">{text.noHistory}</p> : null}
                {locations.slice(0, 6).map((location) => (
                  <article key={location.id} className="location-row">
                    <div>
                      <strong>{location.thermal_zone.name}</strong>
                      <small>
                        {formatDateTime(location.starts_at, language)}
                        {' -> '}
                        {location.end_date_unknown
                          ? text.unknownEnd
                          : location.ends_at
                            ? formatDateTime(location.ends_at, language)
                            : text.current}
                      </small>
                    </div>
                    {location.notes ? <p>{location.notes}</p> : null}
                  </article>
                ))}
              </section>

              {error ? <p className="inline-error subculture-error" role="alert">{error}</p> : null}
            </div>

            <footer className="subculture-actions box-dialog-actions">
              <button type="button" disabled={isBusy} onClick={close}>{text.cancel}</button>
              <button className="is-primary" type="submit" disabled={isBusy || !availableZones.length}>
                {isSaving ? text.saving : text.save}
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

function getCurrentDateTimeValue() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
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
