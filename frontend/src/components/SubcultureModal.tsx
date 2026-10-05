import { type FormEvent, useLayoutEffect, useMemo, useRef, useState } from 'react';

import type { BoxItem, SubculturePayload, ThermalZone } from '../types';
import { createTranslator, type Language } from '../i18n';
import {
  MAX_ALLOCATED_POLYPS,
  MAX_SUBCULTURE_CHILDREN,
  parseAllocatedPolyps,
  summarizeSubcultureAllocation,
} from '../utils/subculture';
import useMutationDialog from '../hooks/useMutationDialog';
import useSubcultureCodePreview from '../hooks/useSubcultureCodePreview';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import './quantitative-subculture.css';

type ChildDraft = {
  key: number;
  thermal_zone_id: number;
  allocatedPolyps: string;
};

type Props = {
  box: BoxItem;
  existingBoxes?: BoxItem[];
  zones: ThermalZone[];
  language: Language;
  isSaving: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: SubculturePayload) => Promise<void>;
};


export default function SubcultureModal({
  box,
  zones,
  language,
  isSaving,
  error,
  onClose,
  onSubmit,
}: Props) {
  const t = createTranslator(language);
  const text = {
    title: t('subcultureTitle'), note: t('subcultureNote'), notePlaceholder: t('subcultureNotePlaceholder'),
    child: t('subcultureChild'),
    addChild: t('subcultureAddChild'), removeChild: t('subcultureRemoveChild'), allocatedPolyps: t('subculturePolypsToAllocate'),
    available: t('subcultureAvailable'), allocated: t('subcultureAllocated'), remaining: t('subcultureRemaining'),
    unknown: t('subcultureUnknown'), unknownCount: t('subcultureUnknownCount'),
    invalidCount: t('subcultureInvalidCount'), overAllocated: t('subcultureOverAllocated'),
    noZones: t('subcultureNoZones'), stateChanged: t('subcultureStateChanged'), childLimit: t('subcultureChildLimit'),
    save: t('subcultureAction'), saving: t('subcultureCreating'),
  };
  const { dialogRef, initialFocusRef, isBusy, close, submit } = useMutationDialog<HTMLInputElement>(isSaving, onClose);
  const availableZones = useMemo(
    () => zones.filter((zone) => zone.organization.id === box.organization.id && zone.is_active),
    [box.organization.id, zones],
  );
  const [notes, setNotes] = useState('');
  // Keep the revision observed when preparing this intent; a refresh must not silently rebase it.
  const [expectedRevision, setExpectedRevision] = useState(() => box.current_polyp_state?.revision ?? '');
  const nextKey = useRef(2);
  const pendingFocusKey = useRef<number | null>(null);
  const [children, setChildren] = useState<ChildDraft[]>(() => [createChildDraft(1, box, availableZones)]);
  const previewCodes = useSubcultureCodePreview(box.id, box.organization.id, children.length);
  const allocation = summarizeSubcultureAllocation(
    box.current_polyp_state?.polyp_count ?? null,
    children.map((child) => child.allocatedPolyps),
  );
  const stateChanged = expectedRevision !== (box.current_polyp_state?.revision ?? '');
  const invalidCount = children.some((child) => child.allocatedPolyps !== '' && parseAllocatedPolyps(child.allocatedPolyps) === null);
  const validZones = children.every((child) => availableZones.some((zone) => zone.id === child.thermal_zone_id));
  const canSubmit = Boolean(expectedRevision) && !stateChanged && allocation.available !== null
    && !invalidCount && !allocation.overAllocated && validZones
    && children.length > 0 && children.length <= MAX_SUBCULTURE_CHILDREN;
  const validationMessage = stateChanged ? text.stateChanged
    : allocation.available === null ? text.unknownCount
      : !availableZones.length ? text.noZones
        : allocation.overAllocated ? text.overAllocated
          : invalidCount ? text.invalidCount : null;

  useLayoutEffect(() => {
    if (isBusy || pendingFocusKey.current === null) return;
    dialogRef.current?.querySelector<HTMLInputElement>(`[data-allocation-key="${pendingFocusKey.current}"]`)?.focus();
    pendingFocusKey.current = null;
  }, [children, isBusy, dialogRef]);

  function addChild() {
    if (isBusy || children.length >= MAX_SUBCULTURE_CHILDREN) return;
    const key = nextKey.current++;
    pendingFocusKey.current = key;
    setChildren((current) => [...current, createChildDraft(key, box, availableZones)]);
  }

  function updateChild(key: number, values: Partial<Omit<ChildDraft, 'key'>>) {
    if (isBusy) return;
    setChildren((current) => current.map((child) => child.key === key ? { ...child, ...values } : child));
  }

  function removeChild(key: number) {
    if (isBusy || children.length <= 1) return;
    const index = children.findIndex((child) => child.key === key);
    if (index < 0) return;
    pendingFocusKey.current = (children[index + 1] ?? children[index - 1]).key;
    setChildren((current) => current.filter((child) => child.key !== key));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isBusy || !canSubmit) return;
    const payloadChildren = children.map((child) => ({
      thermal_zone_id: child.thermal_zone_id,
      allocated_polyps: parseAllocatedPolyps(child.allocatedPolyps),
      copy_origin: true,
      notes: '',
    }));

    await submit(() => onSubmit({
      expected_current_state_revision: expectedRevision,
      reason: '',
      notes: notes.trim(),
      children: payloadChildren,
    }));
  }

  return (
    <ModalPortal>
      <div className="modal-backdrop box-dialog-backdrop quantitative-subculture-backdrop" role="presentation" onClick={close}>
        <section
          ref={dialogRef}
          tabIndex={-1}
          aria-busy={isBusy}
          className="subculture-modal box-dialog box-dialog--subculture quantitative-subculture"
          role="dialog"
          aria-modal="true"
          aria-labelledby="subculture-title"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="box-dialog-heading">
            <div>
              <h2 id="subculture-title">{text.title}</h2>

            </div>
            <button className="icon-button box-dialog-close" type="button" aria-label={t('cancel')} title={t('cancel')} disabled={isBusy} onClick={close}>
              <PolypbaseIcon name="close" size={19} aria-hidden="true" />
            </button>
          </header>

          <form className="box-dialog-form" onSubmit={handleSubmit}>
            <div className="box-dialog-body quantitative-subculture-body">
              <div className="quantitative-subculture-parent">
                <span>
                  <strong>{box.global_code}</strong>
                  <small>{box.species.scientific_name}</small>
                </span>
                <div className="quantitative-subculture-summary" role="status" aria-live="polite" aria-atomic="true">
                  <dl>
                    <div><dt>{text.available}</dt><dd>{allocation.available ?? text.unknown}{allocation.available !== null ? ` ${t('polyps')}` : ''}</dd></div>
                    {allocation.allocated !== null && allocation.remaining !== null ? <div><dt>{text.allocated}</dt><dd>{allocation.allocated}</dd></div> : null}
                    {allocation.remaining !== null ? <div><dt>{text.remaining}</dt><dd>{allocation.remaining}</dd></div> : null}
                  </dl>
                </div>
              </div>

              <div className="quantitative-subculture-children">

                <div className="quantitative-subculture-rows">
                  {children.map((child, index) => (
                    <section className="quantitative-subculture-row" key={child.key} aria-labelledby={`subculture-child-${child.key}`}>
                      <div className="quantitative-subculture-child-heading">
                        <div>
                          <h4 id={`subculture-child-${child.key}`}>
                            {text.child} {index + 1} : <span title={t('subcultureCodePreviewHint')}>{previewCodes[index] ?? '—'}</span>
                          </h4>
                        </div>
                        {children.length > 1 ? (
                          <button className="icon-button" type="button" aria-label={`${text.removeChild} ${index + 1}`} title={`${text.removeChild} ${index + 1}`} disabled={isBusy} onClick={() => removeChild(child.key)}>
                            <PolypbaseIcon name="close" size={17} aria-hidden="true" />
                          </button>
                        ) : null}
                      </div>
                      <label className="quantitative-subculture-zone">
                        {t('createBoxZone')}
                        <select
                          name={`children.${index}.thermal_zone_id`}
                          disabled={isBusy}
                          required
                          value={availableZones.some((zone) => zone.id === child.thermal_zone_id) ? child.thermal_zone_id : ''}
                          onChange={(event) => updateChild(child.key, { thermal_zone_id: Number(event.target.value) })}
                        >
                          <option value="" disabled>—</option>
                          {availableZones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
                        </select>
                      </label>
                      <label className="quantitative-subculture-count">
                        {text.allocatedPolyps}
                        <input
                          ref={index === 0 ? initialFocusRef : undefined}
                          data-allocation-key={child.key}
                          name={`children.${index}.allocated_polyps`}
                          type="number"
                          inputMode="numeric"
                          min="0"
                          max={MAX_ALLOCATED_POLYPS}
                          step="1"
                          disabled={isBusy}
                          value={child.allocatedPolyps}
                          aria-invalid={child.allocatedPolyps !== '' && parseAllocatedPolyps(child.allocatedPolyps) === null ? true : undefined}
                          aria-describedby={validationMessage ? 'subculture-validation' : undefined}
                          onChange={(event) => updateChild(child.key, { allocatedPolyps: event.target.value })}
                        />
                      </label>

                    </section>
                  ))}
                </div>
                <div className="quantitative-subculture-add">
                  <button className="secondary-button quantitative-subculture-add-button" type="button" disabled={isBusy || children.length >= MAX_SUBCULTURE_CHILDREN} onClick={addChild}>
                    <span className="quantitative-subculture-add-icon" aria-hidden="true">+</span>
                    <span>{text.addChild}</span>
                  </button>
                  {children.length >= MAX_SUBCULTURE_CHILDREN ? <small>{text.childLimit}</small> : null}
                </div>
              </div>

              <label className="quantitative-subculture-note">
                {text.note}
                <textarea name="notes" rows={2} disabled={isBusy} placeholder={text.notePlaceholder} value={notes} onChange={(event) => setNotes(event.target.value)} />
              </label>
              {validationMessage ? (
                <p id="subculture-validation" className={allocation.overAllocated || invalidCount || stateChanged ? 'inline-error' : 'quantitative-subculture-help'}>{validationMessage}</p>
              ) : null}
              {stateChanged && box.current_polyp_state?.revision ? (
                <button className="secondary-button" type="button" disabled={isBusy} onClick={() => setExpectedRevision(box.current_polyp_state.revision)}>
                  {t('subcultureReviewState')}
                </button>
              ) : null}
              {error && !stateChanged ? <p className="inline-error" role="alert">{error}</p> : null}
            </div>

            <footer className="box-dialog-actions">

              <button className="secondary-button" type="button" disabled={isBusy} onClick={close}>{t('cancel')}</button>
              <button className="primary-button" type="submit" disabled={isBusy || !canSubmit}>{isBusy ? text.saving : text.save}</button>
            </footer>
          </form>
        </section>
      </div>
    </ModalPortal>
  );
}

function createChildDraft(key: number, parentBox: BoxItem, zones: ThermalZone[]): ChildDraft {
  const defaultZone = zones.find((zone) => zone.id === parentBox.thermal_zone?.id) ?? zones[0];
  return { key, thermal_zone_id: defaultZone?.id ?? 0, allocatedPolyps: '' };
}
