import { type FormEvent, useMemo, useState } from 'react';

import type {
  BoxItem,
  SubcultureChildPayload,
  SubculturePayload,
  ThermalZone,
} from '../types';

type Language = 'fr' | 'en';

type ChildDraft = SubcultureChildPayload & {
  key: number;
};

type Props = {
  box: BoxItem;
  existingBoxes: BoxItem[];
  zones: ThermalZone[];
  language: Language;
  isSaving: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: SubculturePayload) => Promise<void>;
};

const labels = {
  fr: {
    title: 'Repiquer la boîte',
    parent: 'Boîte parent',
    date: 'Date du repiquage',
    reason: 'Motif',
    reasonPlaceholder: 'Ex. densité élevée de polypes',
    eventNotes: 'Note générale',
    eventNotesPlaceholder: 'Informations utiles sur le repiquage',
    children: 'Boîtes enfants',
    addChild: 'Ajouter une boîte',
    removeChild: 'Supprimer cette boîte',
    globalCode: 'Code global',
    boxNumber: 'Numéro',
    zone: 'Emplacement thermique',
    initialPolyps: 'Nombre de polypes initial',
    childNotes: 'Note',
    childNotesPlaceholder: 'Optionnel',
    copyOrigin: "Reprendre l'origine",
    cancel: 'Annuler',
    save: 'Créer le repiquage',
    saving: 'Création...',
  },
  en: {
    title: 'Create a subculture',
    parent: 'Parent box',
    date: 'Subculture date',
    reason: 'Reason',
    reasonPlaceholder: 'For example, high polyp density',
    eventNotes: 'General note',
    eventNotesPlaceholder: 'Useful information about the subculture',
    children: 'Child boxes',
    addChild: 'Add a box',
    removeChild: 'Remove this box',
    globalCode: 'Global code',
    boxNumber: 'Number',
    zone: 'Thermal zone',
    initialPolyps: 'Initial polyp count',
    childNotes: 'Note',
    childNotesPlaceholder: 'Optional',
    copyOrigin: 'Copy origin',
    cancel: 'Cancel',
    save: 'Create subculture',
    saving: 'Creating...',
  },
};

export default function SubcultureModal({
  box,
  existingBoxes,
  zones,
  language,
  isSaving,
  error,
  onClose,
  onSubmit,
}: Props) {
  const text = labels[language];
  const availableZones = useMemo(
    () => zones.filter((zone) => zone.organization.id === box.organization.id && zone.is_active),
    [box.organization.id, zones],
  );
  const [eventDate, setEventDate] = useState(getTodayDateValue);
  const [reason, setReason] = useState('');
  const [notes, setNotes] = useState('');
  const [nextKey, setNextKey] = useState(2);
  const [children, setChildren] = useState<ChildDraft[]>(() => [
    createChildDraft(1, box, existingBoxes, [], availableZones),
  ]);

  function addChild() {
    setChildren((current) => [
      ...current,
      createChildDraft(nextKey, box, existingBoxes, current, availableZones),
    ]);
    setNextKey((current) => current + 1);
  }

  function updateChild(key: number, values: Partial<ChildDraft>) {
    setChildren((current) => (
      current.map((child) => (child.key === key ? { ...child, ...values } : child))
    ));
  }

  function removeChild(key: number) {
    setChildren((current) => current.filter((child) => child.key !== key));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || children.length === 0) return;

    await onSubmit({
      event_date: eventDate,
      reason: reason.trim(),
      notes: notes.trim(),
      children: children.map(({ key: _key, ...child }) => ({
        ...child,
        global_code: child.global_code.trim(),
        local_code: child.local_code.trim(),
        box_number: child.box_number.trim(),
        initial_polyp_count: child.initial_polyp_count,
        notes: child.notes.trim(),
      })),
    });
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="subculture-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="subculture-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="subculture-heading">
          <div>
            <p>{text.parent} · {box.global_code}</p>
            <h2 id="subculture-title">{text.title}</h2>
          </div>
          <button type="button" aria-label={text.cancel} title={text.cancel} onClick={onClose}>
            ×
          </button>
        </header>

        <form className="subculture-form" onSubmit={handleSubmit}>
          <div className="subculture-event-fields">
            <label>
              {text.date}
              <input
                required
                type="date"
                value={eventDate}
                onChange={(event) => setEventDate(event.target.value)}
              />
            </label>
            <label>
              {text.reason}
              <input
                maxLength={180}
                placeholder={text.reasonPlaceholder}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            <label>
              {text.eventNotes}
              <input
                placeholder={text.eventNotesPlaceholder}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </label>
          </div>

          <div className="subculture-children-heading">
            <h3>{text.children}</h3>
            <button type="button" onClick={addChild}>
              <span aria-hidden="true">+</span>
              {text.addChild}
            </button>
          </div>

          <div className="subculture-children">
            {children.map((child, index) => (
              <section className="subculture-child" key={child.key}>
                <div className="subculture-child-title">
                  <strong>{index + 1}</strong>
                  {children.length > 1 ? (
                    <button
                      type="button"
                      aria-label={text.removeChild}
                      title={text.removeChild}
                      onClick={() => removeChild(child.key)}
                    >
                      ×
                    </button>
                  ) : null}
                </div>

                <label className="subculture-global-code">
                  {text.globalCode}
                  <input
                    required
                    readOnly
                    value={child.global_code}
                  />
                </label>
                <label>
                  {text.boxNumber}
                  <input
                    required
                    value={child.box_number}
                    onChange={(event) => updateChild(child.key, { box_number: event.target.value })}
                  />
                </label>
                <label>
                  {text.zone}
                  <select
                    required
                    value={child.thermal_zone_id || ''}
                    onChange={(event) => updateChild(child.key, {
                      thermal_zone_id: Number(event.target.value),
                    })}
                  >
                    <option value="" disabled>—</option>
                    {availableZones.map((zone) => (
                      <option key={zone.id} value={zone.id}>{zone.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  {text.initialPolyps}
                  <input
                    min="0"
                    step="1"
                    type="number"
                    value={child.initial_polyp_count ?? ''}
                    onChange={(event) => updateChild(child.key, {
                      initial_polyp_count: event.target.value === '' ? null : Number(event.target.value),
                    })}
                  />
                </label>
                <label className="subculture-child-notes">
                  {text.childNotes}
                  <input
                    placeholder={text.childNotesPlaceholder}
                    value={child.notes}
                    onChange={(event) => updateChild(child.key, { notes: event.target.value })}
                  />
                </label>

                <div className="subculture-copy-options">
                  <label>
                    <input
                      type="checkbox"
                      checked={child.copy_origin}
                      onChange={(event) => updateChild(child.key, {
                        copy_origin: event.target.checked,
                      })}
                    />
                    {text.copyOrigin}
                  </label>
                </div>
              </section>
            ))}
          </div>

          {error ? <p className="inline-error subculture-error">{error}</p> : null}

          <footer className="subculture-actions">
            <button type="button" onClick={onClose}>{text.cancel}</button>
            <button className="is-primary" type="submit" disabled={isSaving || !availableZones.length}>
              {isSaving ? text.saving : text.save}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function createChildDraft(
  key: number,
  parentBox: BoxItem,
  existingBoxes: BoxItem[],
  currentChildren: ChildDraft[],
  zones: ThermalZone[],
): ChildDraft {
  const suggestion = suggestChildIdentity(parentBox, existingBoxes, currentChildren);
  const parentZoneId = parentBox.thermal_zone?.id;
  const defaultZone = zones.find((zone) => zone.id === parentZoneId) ?? zones[0];

  return {
    key,
    global_code: suggestion.globalCode,
    local_code: '',
    box_number: suggestion.boxNumber,
    thermal_zone_id: defaultZone?.id ?? 0,
    copy_origin: true,
    initial_polyp_count: null,
    notes: '',
  };
}

function suggestChildIdentity(
  parentBox: BoxItem,
  existingBoxes: BoxItem[],
  currentChildren: ChildDraft[],
) {
  const match = parentBox.global_code.match(/^(.*\.)(\d+)(.*)$/);
  if (!match) {
    return { globalCode: '', boxNumber: '' };
  }

  const [, prefix, parentNumber, suffix] = match;
  const width = parentNumber.length;
  const existingCodes = [
    ...existingBoxes.map((box) => box.global_code),
    ...currentChildren.map((child) => child.global_code),
  ];
  const matchingNumbers = existingCodes
    .map((code) => code.match(new RegExp(`^${escapeRegExp(prefix)}(\\d+)${escapeRegExp(suffix)}$`)))
    .filter((value): value is RegExpMatchArray => Boolean(value))
    .map((value) => Number(value[1]));
  const nextNumber = Math.max(Number(parentNumber), ...matchingNumbers) + 1;
  const formattedNumber = String(nextNumber).padStart(width, '0');

  return {
    globalCode: `${prefix}${formattedNumber}${suffix}`,
    boxNumber: formattedNumber,
  };
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function getTodayDateValue() {
  const today = new Date();
  today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
  return today.toISOString().slice(0, 10);
}
