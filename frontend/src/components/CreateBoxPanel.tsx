import { type FormEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { ApiError, ApiResourceCancelledError, apiGet } from '../api/client';
import type { Translator } from '../i18n';
import type { BoxItem, BoxCreatePayload, BoxDetail, ExportOptions, UserProfile } from '../types';
import type { TaxonomyReferences } from '../types/admin';
import { buildNextBoxCode, boxCodeMatchesBoxNumber } from '../utils/boxCode';
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
  boxes,
  isBoxCollectionReady,
  onRequestBoxCollection,
  exportOptions,
  isOpen: controlledIsOpen,
  isOptionsLoading,
  presentation = 'inline',
  profile,
  confirmAction,
  onCreateBox,
  isOperationCurrent,
  onManageSpeciesCodes,
  onOpenChange,
  onRequestOptions,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  isBoxCollectionReady: boolean;
  onRequestBoxCollection: () => void;
  exportOptions: ExportOptions | null;
  isOpen?: boolean;
  isOptionsLoading: boolean;
  presentation?: 'inline' | 'modal';
  profile: UserProfile | null;
  confirmAction: ConfirmAction;
  onCreateBox: (payload: BoxCreatePayload) => Promise<BoxDetail>;
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
  const [strainId, setStrainId] = useState<number | null>(null);
  const [zoneId, setZoneId] = useState<number | null>(null);
  const [globalCode, setGlobalCode] = useState('');
  const [boxNumber, setBoxNumber] = useState('');
  const [enteredOn, setEnteredOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState('');
  const [strainSearch, setStrainSearch] = useState('');
  const [createdStrains, setCreatedStrains] = useState<QuickCreatedStrain[]>([]);
  const [references, setReferences] = useState<TaxonomyReferences | null>(null);
  const [isReferencesLoading, setIsReferencesLoading] = useState(false);
  const [referencesError, setReferencesError] = useState<string | null>(null);
  const [isQuickStrainOpen, setIsQuickStrainOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
  }, [isOpen, organizationId, t]);

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
    selectedStrain != null && zoneId != null && globalCode.trim() && boxNumber.trim();

  useEffect(() => {
    if (strainId != null && strains.some((strain) => strain.id === strainId)) return;
    setStrainId(strains[0]?.id ?? null);
  }, [strainId, strains]);

  // The next code is derived from every existing box, so it waits for the full list.
  useEffect(() => {
    if (isOpen) onRequestBoxCollection();
  }, [isOpen, onRequestBoxCollection]);

  useEffect(() => {
    if (organizationId == null || !selectedStrain || !isBoxCollectionReady) return;
    const suggestion = buildNextBoxCode(boxes, selectedStrain, organizationId);
    setGlobalCode((current) => current.trim() ? current : suggestion.globalCode);
    setBoxNumber((current) => current.trim() ? current : suggestion.boxNumber);
  }, [boxes, isBoxCollectionReady, organizationId, selectedStrain]);

  useEffect(() => {
    if (zoneId == null || availableZones.some((zone) => zone.id === zoneId)) return;
    setZoneId(null);
  }, [availableZones, zoneId]);

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
    if (isSaving || !canSubmit || organizationId == null || strainId == null || zoneId == null) return;

    if (!boxCodeMatchesBoxNumber(globalCode, boxNumber)) {
      setError(t('createBoxNumberMismatch'));
      return;
    }

    let confirmed = false;
    isConfirmationOpenRef.current = true;
    try {
      confirmed = await confirmAction({
        title: t('confirmCreateBoxTitle'),
        message: t('confirmCreateBoxMessage'),
        confirmLabel: t('confirmCreateBoxAction'),
        cancelLabel: t('confirmCancel'),
        details: [
          { label: t('confirmDetailBox'), value: globalCode.trim() },
          { label: t('confirmDetailSpecies'), value: selectedStrain?.species_name },
          { label: t('confirmDetailStrain'), value: selectedStrain?.code },
          { label: t('confirmDetailOrganization'), value: activeOrganization?.name },
          { label: t('confirmDetailLocation'), value: selectedZone?.name ?? t('createBoxNoZone') },
        ],
      });
    } finally {
      isConfirmationOpenRef.current = false;
    }
    if (!confirmed || !operationLifetimeRef.current || !isOperationCurrent()) return;

    setIsSaving(true);
    setError(null);

    try {
      const created = await onCreateBox({
        strain: strainId,
        thermal_zone: zoneId,
        global_code: globalCode.trim(),
        local_code: '',
        box_number: boxNumber.trim(),
        entered_on: enteredOn,
        volume_liters: null,
        notes: notes.trim(),
      });
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setGlobalCode('');
      setBoxNumber('');
      setNotes('');
      onSelectBox(created.id);
      if (presentation === 'modal') {
        window.requestAnimationFrame(() => {
          document.querySelector<HTMLElement>('[data-box-page-focus-target]')?.focus({ preventScroll: true });
        });
      }
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return;
      if (requestError instanceof ApiError && requestError.status === 403) {
        setError(t('createBoxForbidden'));
      } else {
        setError(getErrorMessage(requestError));
      }
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSaving(false);
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

      {isOpen ? (
        <form className="create-box-form" onSubmit={handleSubmit}>
          <div className="section-title">
            <h2>{t('createBoxTitle')}</h2>
          </div>

          {isOptionsLoading || isReferencesLoading ? <p className="muted compact-text">{t('loading')}</p> : null}
          {referencesError ? <p className="inline-error">{referencesError}</p> : null}
          {!isReferencesLoading && !referencesError && references && !strains.length ? <p className="muted compact-text">{t('createBoxNoOptions')}</p> : null}

          <div className="create-box-strain-field">
            <div className="create-box-field-heading">
              <span>{t('createBoxStrain')}</span>
              {userHasAdminRole(profile) ? (
                <button
                  className="create-box-reference-action"
                  type="button"
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
                value={strainSearch}
                placeholder={t('quickStrainSearch')}
                onChange={(event) => setStrainSearch(event.target.value)}
              />
            </label>
            <select
              aria-label={t('createBoxStrain')}
              value={strainId ?? ''}
              onChange={(event) => {
                setStrainId(Number(event.target.value));
                setGlobalCode('');
                setBoxNumber('');
              }}
            >
              {filteredStrains.map((strain) => (
                <option key={strain.id} value={strain.id}>
                  {strain.species_name} - {strain.code}
                </option>
              ))}
            </select>
          </div>

          <label className="create-box-location-field">
            <span>{t('createBoxZone')}</span>
            <select required value={zoneId ?? ''} onChange={(event) => setZoneId(event.target.value ? Number(event.target.value) : null)}>
              <option value="" disabled>{t('createBoxNoZone')}</option>
              {availableZones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </select>
          </label>

          <label className="create-box-code-field">
            <span>{t('createBoxGlobalCode')}</span>
            <input required value={globalCode} onChange={(event) => setGlobalCode(event.target.value)} />
          </label>

          <label className="create-box-number-field">
            <span>{t('createBoxNumber')}</span>
            <input required value={boxNumber} onChange={(event) => setBoxNumber(event.target.value)} />
          </label>

          <label className="create-box-date-field">
            <span>{t('createBoxEnteredOn')}</span>
            <input required type="date" value={enteredOn} onChange={(event) => setEnteredOn(event.target.value)} />
          </label>

          <label className="create-box-wide">
            <span>{t('createBoxNotes')}</span>
            <textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>

          <button type="submit" disabled={isSaving || !canSubmit}>
            {isSaving ? t('saving') : t('createBoxSubmit')}
          </button>
          {error ? <p className="inline-error">{error}</p> : null}
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
            setGlobalCode('');
            setBoxNumber('');
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
