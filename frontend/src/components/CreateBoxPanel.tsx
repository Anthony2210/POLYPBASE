import { type FormEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { ApiError, apiGet } from '../api/client';
import type { Translator } from '../i18n';
import type { BoxCreatePayload, BoxCreationResolution, BoxDetail, ExportOptions, UserProfile } from '../types';
import type { TaxonomyReferences } from '../types/admin';
import { getErrorMessage } from '../utils/errors';
import { userHasAdminRole } from '../utils/profilePermissions';
import type { ConfirmActionOptions } from './ConfirmActionModal';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import QuickStrainCreator, { type QuickCreatedStrain } from './QuickStrainCreator';

type TFunction = Translator;
type ConfirmAction = (options: ConfirmActionOptions) => Promise<boolean>;
const DIALOG_FOCUSABLE_SELECTOR =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export default function CreateBoxPanel({
  exportOptions,
  isOpen: controlledIsOpen,
  isOptionsLoading,
  presentation = 'inline',
  profile,
  confirmAction,
  onCreateBox,
  onResolveBoxCreation,
  isOperationCurrent,
  onManageSpeciesCodes,
  onOpenChange,
  onRequestOptions,
  onSelectBox,
  t,
}: {
  exportOptions: ExportOptions | null;
  isOpen?: boolean;
  isOptionsLoading: boolean;
  presentation?: 'inline' | 'modal';
  profile: UserProfile | null;
  confirmAction: ConfirmAction;
  onCreateBox: (payload: BoxCreatePayload) => Promise<BoxDetail>;
  onResolveBoxCreation: (requestId: string) => Promise<BoxCreationResolution>;
  isOperationCurrent: () => boolean;
  onManageSpeciesCodes?: () => void;
  onOpenChange?: (isOpen: boolean) => void;
  onRequestOptions: () => void;
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  const [isInlineOpen, setIsInlineOpen] = useState(false);
  const isOpen = presentation === 'modal' ? Boolean(controlledIsOpen) : isInlineOpen;
  const dialogRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const isQuickStrainOpenRef = useRef(false);
  const operationLifetimeRef = useRef(true);
  useLayoutEffect(() => {
    operationLifetimeRef.current = true;
    return () => { operationLifetimeRef.current = false; };
  }, []);
  const isConfirmationOpenRef = useRef(false);
  const activeOrganization = profile?.active_organization ?? null;
  const organizationId = activeOrganization?.id ?? null;
  const storageKey = `polypbase.automaticBox.${profile?.id}.${organizationId}`;
  const [initialIntent] = useState<BoxCreatePayload | null>(() => {
    try {
      const stored = window.sessionStorage.getItem(storageKey);
      return stored ? JSON.parse(stored) : null;
    } catch { return null; }
  });
  const [strainId, setStrainId] = useState<number | null>(initialIntent?.strain ?? null);
  const [zoneId, setZoneId] = useState<number | null>(initialIntent?.thermal_zone ?? null);
  const pendingIntentRef = useRef<BoxCreatePayload | null>(initialIntent);
  const autoSelectStrainRef = useRef(!initialIntent);
  const submissionRef = useRef(false);
  const [hasPendingIntent, setHasPendingIntent] = useState(Boolean(initialIntent));
  const [createdBox, setCreatedBox] = useState<BoxDetail | null>(null);
  const [enteredOn, setEnteredOn] = useState(initialIntent?.entered_on ?? new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState(initialIntent?.notes ?? '');
  const [strainSearch, setStrainSearch] = useState('');
  const [createdStrains, setCreatedStrains] = useState<QuickCreatedStrain[]>([]);
  const [references, setReferences] = useState<TaxonomyReferences | null>(null);
  const [isReferencesLoading, setIsReferencesLoading] = useState(false);
  const [referencesError, setReferencesError] = useState<string | null>(null);
  const [isQuickStrainOpen, setIsQuickStrainOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isResolving, setIsResolving] = useState(false);
  const [isRequestCancelled, setIsRequestCancelled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [referenceAttempt, setReferenceAttempt] = useState(0);
  const isDraftLocked = hasPendingIntent || isSaving;

  function setOpen(nextIsOpen: boolean) {
    if (presentation === 'modal') onOpenChange?.(nextIsOpen);
    else setIsInlineOpen(nextIsOpen);
  }

  useEffect(() => {
    if (!isOpen || organizationId == null) return;
    let isCurrent = true;
    setIsReferencesLoading(true);
    setReferencesError(null);
    apiGet<TaxonomyReferences>('/api/taxonomy/references/')
      .then((result) => {
        if (isCurrent) setReferences(result);
      })
      .catch((requestError) => {
        if (isCurrent) setReferencesError(getErrorMessage(requestError, t('taxonomyLoadError')));
      })
      .finally(() => {
        if (isCurrent) setIsReferencesLoading(false);
      });
    return () => { isCurrent = false; };
  }, [isOpen, organizationId, t, referenceAttempt]);

  const strains = useMemo(() => {
    const options = (references?.strains ?? []).map((strain) => ({
      id: strain.id,
      code: strain.code,
      species_id: strain.species,
      species_name: strain.species_scientific_name,
    }));
    return [...options, ...createdStrains.filter((created) =>
      !options.some((option) => option.id === created.id))];
  }, [createdStrains, references?.strains]);
  const normalizedStrainSearch = strainSearch.trim().toLocaleLowerCase('fr-FR');
  const filteredStrains = strains.filter((strain) => {
    if (!normalizedStrainSearch || strain.id === strainId) return true;
    return `${strain.species_name} ${strain.code}`.toLocaleLowerCase('fr-FR').includes(normalizedStrainSearch);
  });
  const selectedStrain = strains.find((strain) => strain.id === strainId) ?? null;
  const availableZones = (exportOptions?.zones ?? []).filter((zone) => zone.organization_id === organizationId);
  const selectedZone = availableZones.find((zone) => zone.id === zoneId) ?? null;
  const canSubmit = organizationId != null && references != null && !isReferencesLoading && !referencesError &&
    selectedStrain != null && selectedZone != null && enteredOn !== '' && !isOptionsLoading;

  useEffect(() => {
    if (hasPendingIntent || (strainId != null && strains.some((strain) => strain.id === strainId))) return;
    setStrainId(autoSelectStrainRef.current ? strains[0]?.id ?? null : null);
  }, [hasPendingIntent, strainId, strains]);

  useEffect(() => {
    if (hasPendingIntent || zoneId == null || availableZones.some((zone) => zone.id === zoneId)) return;
    setZoneId(null);
  }, [availableZones, hasPendingIntent, zoneId]);

  useEffect(() => {
    isQuickStrainOpenRef.current = isQuickStrainOpen;
  }, [isQuickStrainOpen]);

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
      if (isQuickStrainOpenRef.current || isConfirmationOpenRef.current) return;
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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submissionRef.current || createdBox || !isOperationCurrent() || organizationId == null ||
      (!pendingIntentRef.current && (!canSubmit || strainId == null || zoneId == null))) return;
    // Acquire before awaiting confirmation: two synchronous submits share one intent.
    submissionRef.current = true;
    setIsSaving(true);
    setIsRequestCancelled(false);
    let intent = pendingIntentRef.current;
    const draft = { strain: strainId!, thermal_zone: zoneId!, entered_on: enteredOn, volume_liters: null, notes: notes.trim() };
    let confirmed = Boolean(intent);
    isConfirmationOpenRef.current = !intent;
    try {
      if (!intent) confirmed = await confirmAction({
        title: t('confirmCreateBoxTitle'),
        message: t('confirmCreateBoxMessage'),
        confirmLabel: t('confirmCreateBoxAction'),
        cancelLabel: t('confirmCancel'),
        details: [
          { label: t('createBoxEnteredOn'), value: draft.entered_on },
          { label: t('confirmDetailSpecies'), value: selectedStrain?.species_name },
          { label: t('confirmDetailStrain'), value: selectedStrain?.code },
          { label: t('confirmDetailOrganization'), value: activeOrganization?.name },
          { label: t('confirmDetailLocation'), value: selectedZone?.name ?? t('createBoxNoZone') },
        ],
      });
    } catch {
      submissionRef.current = false;
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSaving(false);
      return;
    } finally {
      isConfirmationOpenRef.current = false;
    }
    if (!confirmed || !operationLifetimeRef.current || !isOperationCurrent()) {
      submissionRef.current = false;
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSaving(false);
      return;
    }

    setIsSaving(true);
    setError(null);

    try {
      if (!intent) {
        intent = { ...draft, request_id: window.crypto.randomUUID() };
        // Persist before any write; if storage is unavailable no request is sent.
        window.sessionStorage.setItem(storageKey, JSON.stringify(intent));
        pendingIntentRef.current = intent;
        setHasPendingIntent(true);
      }
      const created = await onCreateBox({ ...intent });
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setCreatedBox(created);
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      if (requestError instanceof ApiError && requestError.status === 403) {
        setError(t('createBoxForbidden'));
      } else if (requestError instanceof ApiError && requestError.status === 409) {
        setError(t('createBoxRequestConflict'));
      } else {
        setError(getErrorMessage(requestError, t('createBoxRequestError')));
      }
    } finally {
      submissionRef.current = false;
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSaving(false);
    }
  }

  async function handleResolveRequest() {
    const intent = pendingIntentRef.current;
    if (submissionRef.current || !intent || createdBox || !operationLifetimeRef.current || !isOperationCurrent()) return;
    submissionRef.current = true;
    setIsSaving(true);
    setIsResolving(true);
    setError(null);
    try {
      const result = await onResolveBoxCreation(intent.request_id);
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      if (result.status === 'created') {
        setCreatedBox(result.box);
      } else if (result.status === 'cancelled') {
        // Only the durable server tombstone makes discarding this key safe.
        window.sessionStorage.removeItem(storageKey);
        pendingIntentRef.current = null;
        autoSelectStrainRef.current = false;
        setHasPendingIntent(false);
        setIsRequestCancelled(true);
      }
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setError(getErrorMessage(requestError, t('createBoxResolveError')));
    } finally {
      submissionRef.current = false;
      if (operationLifetimeRef.current && isOperationCurrent()) {
        setIsSaving(false);
        setIsResolving(false);
      }
    }
  }

  useLayoutEffect(() => {
    if (isRequestCancelled && isOpen && presentation === 'modal') {
      dialogRef.current?.querySelector<HTMLSelectElement>('.create-box-strain-field select')?.focus();
    }
  }, [isRequestCancelled, isOpen, presentation]);

  useLayoutEffect(() => {
    if (createdBox && isOpen && presentation === 'modal') {
      dialogRef.current?.querySelector<HTMLButtonElement>('.create-box-open-created')?.focus();
    }
  }, [createdBox, isOpen, presentation]);

  function openCreatedBox() {
    if (!createdBox || !isOperationCurrent()) return;
    window.sessionStorage.removeItem(storageKey);
    onSelectBox(createdBox.id);
    if (presentation === 'modal') {
      window.requestAnimationFrame(() => {
        document.querySelector<HTMLElement>('[data-box-page-focus-target]')?.focus({ preventScroll: true });
      });
    }
  }

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
        <div className="create-box-form">
          <p className="inline-success" role="status">{t('createBoxSuccess')} <strong>{createdBox.global_code}</strong></p>
          <button type="button" className="primary-button create-box-wide create-box-open-created" onClick={openCreatedBox}>{t('createBoxOpenCreated')}</button>
        </div>
      ) : isOpen ? (
        <form className="create-box-form" onSubmit={handleSubmit}>
          <div className="section-title">
            <h2>{t('createBoxTitle')}</h2>
          </div>

          {isOptionsLoading || isReferencesLoading ? <p className="muted compact-text">{t('loading')}</p> : null}
          {referencesError ? <p className="inline-error" role="alert">{referencesError} <button type="button" onClick={() => setReferenceAttempt((value) => value + 1)}>{t('createBoxRetry')}</button></p> : null}
          {!isReferencesLoading && !referencesError && references && !strains.length ? <p className="muted compact-text">{t('createBoxNoOptions')}</p> : null}

          <p className="muted compact-text">{t('createBoxAutomaticCode')}</p>
          {hasPendingIntent ? (
            <div className="create-box-wide">
              <p className="compact-text" role="status">{t('createBoxPendingIntent')}</p>
              <p className="compact-text" id="create-box-resolve-help">{t('createBoxResolveHelp')}</p>
              <button className="secondary-button create-box-resolve" type="button" disabled={isSaving} aria-describedby="create-box-resolve-help" onClick={handleResolveRequest}>
                {isResolving ? t('createBoxResolving') : t('createBoxResolve')}
              </button>
            </div>
          ) : null}
          {isRequestCancelled ? <p className="compact-text" role="status">{t('createBoxRequestCancelled')}</p> : null}
          {!isOptionsLoading && exportOptions && !availableZones.length ? <p className="compact-text">{t('createBoxNoLocations')}</p> : null}
          <div className="create-box-strain-field">
            <div className="create-box-field-heading">
              <span>{t('createBoxStrain')}</span>
              {userHasAdminRole(profile) ? (
                <button
                  className="create-box-reference-action"
                  type="button"
                  disabled={isDraftLocked}
                  title={t('quickStrainTitle')}
                  onClick={() => setIsQuickStrainOpen(true)}
                >
                  <PolypbaseIcon name="plus" size={17} />
                  {t('quickStrainAdd')}
                </button>
              ) : null}
            </div>
            <label className="create-box-strain-search">
              <PolypbaseIcon name="search" size={16} />
              <span className="sr-only">{t('quickStrainSearch')}</span>
              <input
                type="search"
                disabled={isDraftLocked}
                value={strainSearch}
                placeholder={t('quickStrainSearch')}
                onChange={(event) => setStrainSearch(event.target.value)}
              />
            </label>
            <select
              disabled={isDraftLocked}
              required
              aria-label={t('createBoxStrain')}
              value={strainId ?? ''}
              onChange={(event) => setStrainId(event.target.value ? Number(event.target.value) : null)}
            >
              <option value="" disabled>{t('createBoxSelectStrain')}</option>
              {filteredStrains.map((strain) => (
                <option key={strain.id} value={strain.id}>
                  {strain.species_name} - {strain.code}
                </option>
              ))}
            </select>
          </div>

          <label className="create-box-location-field">
            <span>{t('createBoxZone')}</span>
            <select disabled={isDraftLocked} required value={zoneId ?? ''} onChange={(event) => setZoneId(event.target.value ? Number(event.target.value) : null)}>
              <option value="" disabled>{t('createBoxNoZone')}</option>
              {availableZones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </select>
          </label>

          <label className="create-box-date-field">
            <span>{t('createBoxEnteredOn')}</span>
            <input disabled={isDraftLocked} required type="date" value={enteredOn} onChange={(event) => setEnteredOn(event.target.value)} />
          </label>

          <label className="create-box-wide">
            <span>{t('createBoxNotes')}</span>
            <textarea disabled={isDraftLocked} rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>

          <button type="submit" disabled={isSaving || (!hasPendingIntent && !canSubmit)}>
            {isSaving ? t('saving') : hasPendingIntent ? t('createBoxRetry') : t('createBoxSubmit')}
          </button>
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
        </form>
      ) : null}

      {isQuickStrainOpen && userHasAdminRole(profile) ? (
        <QuickStrainCreator
          t={t}
          onClose={() => setIsQuickStrainOpen(false)}
          onManageCodes={onManageSpeciesCodes}
          onCreated={(strain) => {
            setCreatedStrains((current) => [...current.filter((item) => item.id !== strain.id), strain]);
            setStrainId(strain.id);
            setStrainSearch(`${strain.species_name} ${strain.code}`);
            setIsQuickStrainOpen(false);
          }}
        />
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
