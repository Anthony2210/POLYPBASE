import { type FormEvent, type MouseEvent, type ReactNode, useEffect, useMemo, useRef, useState } from 'react';

import { ExternalLink, RefreshCw, Search } from 'lucide-react';

import { ApiError, apiGet, apiPatch, apiPost } from '../api/client';
import useMutationDialog from '../hooks/useMutationDialog';
import type { Language, TranslationKey, Translator } from '../i18n';
import type { BoxItem } from '../types';
import type { BoxCollectionStatus } from '../utils/boxCollection';
import { fillTemplate } from '../utils/auditPresentation';
import { formatDisplayDate } from '../utils/dateFormat';
import {
  type AdminSpeciesTarget,
  acceptedRecordFrom,
  adminSpeciesPath,
  adminSpeciesTargetFor,
  buildEditPayload,
  buildRegistrationPayload,
  buildSpeciesEntries,
  ACQUISITION_TYPES,
  type AcquisitionType,
  buildStrainPayload,
  canReferenceWormsRecord,
  type CollectionLocation,
  type CollectionLocationsResponse,

  coordinateProblems,
  type CoordinateProblem,
  countryName,

  filterSpeciesEntries,
  type FormReference,
  getServiceFailure,
  getWormsQueryProblem,
  hasDistinctAcceptedName,
  type HistoricalSpeciesDetail,
  type InstitutionSpecies,
  type InstitutionSpeciesDetail,
  type InstitutionSpeciesList,
  isReferenceCompatible,
  isUsableSpeciesCode,
  isWormsAccepted,
  isWormsReferenceFailure,
  LOCATION_CODE_PATTERN,
  normalizeLocationCode,
  normalizeScientificName,
  normalizeWormsQuery,
  recordedStrainName,
  referenceAphiaId,
  sameSpeciesTarget,
  type SpeciesBoxSummary,
  summarizeBoxesBySpecies,
  type SpeciesListEntry,
  type SpeciesStrain,
  sortedCountries,
  splitTemplate,
  strainTitle,
  type StrainCreationResponse,
  type WormsQueryProblem,
  type WormsRecord,
  type WormsSearchResponse,
} from '../utils/speciesCatalog';
import { useConfirmAction } from './ConfirmActionModal';
import DetailBackButton from './DetailBackButton';
import ModalPortal from './ModalPortal';
import PageLoader from './PageLoader';
import { RowActionMenu } from './RowActionMenu';
import PolypbaseIcon from './PolypbaseIcon';

import SpeciesStrainsPanel from './SpeciesStrainsPanel';

const EMPTY_BOX_SUMMARY: SpeciesBoxSummary = {
  boxCount: 0, activeBoxCount: 0, firstMeasurementOn: null, lastMeasurementOn: null,
};
const LIST_PATH = '/api/admin/institution-species/';
const profilePath = (id: number) => `/api/admin/institution-species/${id}/`;
const historicalPath = (speciesId: number) => `/api/admin/institution-species/historical/${speciesId}/`;
const WORMS_SEARCH_PATH = '/api/admin/species-catalog/worms-search/';
const LOCATIONS_PATH = '/api/admin/collection-locations/';
const profileStrainsPath = (id: number) => `/api/admin/institution-species/${id}/strains/`;

type LoadState<T> =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'forbidden' }
  | { status: 'not_found' }
  | { status: 'ready'; data: T };

// offerStrain: the "add a strain?" question is still to be asked, once.
type CreatedNotice = { target: AdminSpeciesTarget; code: string | null; offerStrain?: boolean };

function failedState(error: unknown): LoadState<never> {
  if (error instanceof ApiError && error.status === 403) return { status: 'forbidden' };
  if (error instanceof ApiError && error.status === 404) return { status: 'not_found' };
  return { status: 'error' };
}

function renderTemplate(template: string, values: Record<string, ReactNode>) {
  return splitTemplate(template).map((part, index) => (
    'placeholder' in part ? <span key={index}>{values[part.placeholder ?? ''] ?? ''}</span> : part.text
  ));
}

// In-app navigation for a real link: modified clicks keep browser behavior.
function followInApp(event: MouseEvent<HTMLAnchorElement> | undefined, navigate: () => void) {
  if (event && (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) return;
  event?.preventDefault();
  navigate();
}

export default function SpeciesStrainsAdminSection({
  target,
  organizationId,
  organizationName,
  boxes,
  boxCollectionStatus,
  onRetryBoxCollection,
  language,
  onOpenSpecies,
  onOpenBox,
  onOpenZone,
  onBack,
  onCreateBox,
  t,
}: {
  target: AdminSpeciesTarget | null;
  // Known Boxes of the active institution; complete only when the status is 'ready'.
  boxes: readonly BoxItem[];
  boxCollectionStatus: BoxCollectionStatus;
  onRetryBoxCollection: () => void;
  // All catalogue and detail requests belong to this active institution.
  organizationId: number;
  organizationName: string;
  language: Language;
  onOpenSpecies: (target: AdminSpeciesTarget) => void;
  onOpenBox: (boxId: number, code: string) => void;
  onOpenZone: (zoneId: number) => void;
  onBack: () => void;
  // Opens the existing New Box form locked to this Strain (and its Species).
  onCreateBox?: (strainId: number) => void;
  t: Translator;
}) {
  const [list, setList] = useState<LoadState<InstitutionSpeciesList>>({ status: 'loading' });
  const [listVersion, setListVersion] = useState(0);
  const [search, setSearch] = useState('');

  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [created, setCreated] = useState<CreatedNotice | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState(false);
  const isListShown = target === null;

  // The list is reread whenever it is shown again; current rows stay visible meanwhile.
  useEffect(() => {
    if (!isListShown) return undefined;
    let isCurrent = true;
    const controller = new AbortController();
    setRefreshError(false);
    // Explicit reads must not join a pre-refresh request or the browser cache.
    apiGet<InstitutionSpeciesList>(LIST_PATH, { signal: controller.signal, cache: 'no-store' })
      .then((data) => { if (isCurrent) setList({ status: 'ready', data }); })
      .catch((error) => {
        if (!isCurrent) return;
        setRefreshError(true);
        setList((current) => error instanceof ApiError && error.status === 403
          ? failedState(error) : current.status === 'ready' ? current : failedState(error));
      })
      .finally(() => { if (isCurrent) setIsRefreshing(false); });
    return () => { isCurrent = false; controller.abort(); };
  }, [isListShown, listVersion, organizationId]);

  const entries = useMemo(() => list.status === 'ready' ? buildSpeciesEntries(list.data) : [], [list]);
  const matches = useMemo(() => filterSpeciesEntries(entries, search), [entries, search]);
  const codeIssues = useMemo(() => entries.filter((entry) => !entry.code), [entries]);
  // Box totals exist only once the complete institutional collection is loaded.
  const boxSummaries = useMemo(
    () => boxCollectionStatus === 'ready' ? summarizeBoxesBySpecies(boxes, organizationId) : null,
    [boxCollectionStatus, boxes, organizationId],
  );

  // The registration notice belongs to the newly opened profile only.
  function open(next: AdminSpeciesTarget, notice: CreatedNotice | null = null) {

    setCreated(notice);
    onOpenSpecies(next);
  }

  if (target) {
    return (
      <SpeciesDetail
        key={`${organizationId}-${target.kind}-${target.id}`}
        target={target}
        createdCode={created && sameSpeciesTarget(created.target, target) ? created.code : undefined}
        offerStrain={Boolean(created?.offerStrain && sameSpeciesTarget(created.target, target))}
        onStrainOffered={() => setCreated((current) => current && { ...current, offerStrain: false })}
        onCreateBox={onCreateBox}
        organizationId={organizationId}
        organizationName={organizationName}
        boxes={boxes}
        boxCollectionStatus={boxCollectionStatus}
        onRetryBoxCollection={onRetryBoxCollection}
        language={language}
        onOpenBox={onOpenBox}
        onOpenZone={onOpenZone}
        onBack={onBack}
        onOpenSpecies={open}
        t={t}
      />
    );
  }

  if (list.status === 'loading') return <PageLoader variant="admin" label={t('speciesCatalogTitle')} />;

  return (
    <section className="admin-section species-admin-section" id="admin-species" aria-labelledby="species-admin-title">
      <header className="box-inventory-heading species-admin-heading">
        <h2 id="species-admin-title">{t('speciesCatalogTitle')}</h2>
        {list.status === 'ready' ? (
          <div className="species-admin-actions">
            {/* Rereads POLYPBASE data only: it never queries WoRMS or changes an identification. */}
            <button
              className="icon-button" type="button" disabled={isRefreshing}
              aria-label={t(isRefreshing ? 'loading' : 'speciesCatalogRefresh')}
              aria-busy={isRefreshing || undefined}
              onClick={() => { setIsRefreshing(true); setListVersion((value) => value + 1); onRetryBoxCollection(); }}
            >
              <RefreshCw aria-hidden="true" size={16} />
            </button>
            <button className="primary-button" type="button" onClick={() => setIsCreateOpen(true)}>
              <PolypbaseIcon name="plus" size={17} />
              {t('speciesCatalogNewSpecies')}
            </button>
          </div>
        ) : null}
      </header>

      {refreshError && list.status === 'ready' ? <p className="inline-error" role="alert">{t('speciesCatalogLoadError')}</p> : null}
      {list.status === 'forbidden' ? <p className="inline-error" role="alert">{t('speciesCatalogForbidden')}</p> : null}
      {list.status === 'error' || list.status === 'not_found' ? (
        <div className="species-admin-notice is-error" role="alert">
          <p>{t('speciesCatalogLoadError')}</p>
          <button className="secondary-button" type="button" onClick={() => setListVersion((value) => value + 1)}>
            {t('taxonomyRetry')}
          </button>
        </div>
      ) : null}

      {list.status === 'ready' ? (
        <>
          {codeIssues.length ? (
            <div className="species-code-issues" role="alert">
              <p>{t('speciesCodeDataIssue')}</p>
              <ul>
                {codeIssues.map((entry) => (
                  <li key={`${entry.target.kind}-${entry.target.id}`}>
                    <a href={adminSpeciesPath(entry.target)} onClick={(event) => followInApp(event, () => open(entry.target))}>
                      <em>{entry.scientificName}</em>
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="box-inventory-filters species-admin-filters">
            <label className="box-inventory-search">
              <span>{t('speciesCatalogSearch')}</span>
              <span className="box-inventory-search-control">
                <Search aria-hidden="true" size={17} />
                <input
                  type="search"
                  value={search}
                  placeholder={t('speciesCatalogSearchPlaceholder')}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </span>
            </label>
          </div>

          {!entries.length ? (
            <div className="empty-state species-admin-empty"><strong>{t('speciesCatalogEmpty')}</strong></div>
          ) : null}
          {entries.length > 0 && !matches.length ? (
            <div className="empty-state species-admin-empty" role="status"><strong>{t('speciesCatalogNoResults')}</strong></div>
          ) : null}
          {boxCollectionStatus === 'error' ? (
            <div className="species-admin-notice is-error" role="alert">
              <p>{t('boxCollectionLoadError')}</p>
              <button className="secondary-button" type="button" onClick={onRetryBoxCollection}>{t('boxCollectionRetry')}</button>
            </div>
          ) : null}
          {matches.length > 0 ? (
            <div className="box-inventory-list species-catalog-list">
            <div className="species-list-header" aria-hidden="true">
              <span>{t('speciesLabel')}</span>
              <span className="is-numeric">{t('speciesDetailStrains')}</span>
              <span className="is-numeric">{t('speciesStrainActiveBoxes')}</span>
              <span>{t('boxInventoryDates')}</span>
              <span />
            </div>
            <ul className="species-list" aria-labelledby="species-admin-title">
              {matches.map((entry) => (
                <SpeciesRow
                  key={`${entry.target.kind}-${entry.target.id}`}
                  entry={entry}
                  summary={boxSummaries ? boxSummaries.get(entry.speciesId) ?? EMPTY_BOX_SUMMARY : null}
                  isBoxCountLoading={boxCollectionStatus !== 'ready' && boxCollectionStatus !== 'error'}
                  onOpen={() => open(entry.target)}
                  t={t}
                />
              ))}
            </ul>
            </div>
          ) : null}
        </>
      ) : null}

      {isCreateOpen ? (
        <SpeciesFormDialog
          mode="create"
          t={t}
          onClose={() => setIsCreateOpen(false)}
          onSaved={(profile) => {
            const next = { kind: 'profile' as const, id: profile.id };
            setIsCreateOpen(false);
            setListVersion((value) => value + 1);
            open(next, { target: next, code: profile.species_code, offerStrain: true });
          }}
          onOpenExisting={(existing) => {
            setIsCreateOpen(false);
            open(existing);
          }}
        />
      ) : null}
    </section>
  );
}

function SpeciesRow({ entry, summary, isBoxCountLoading, onOpen, t }: {
  key?: string;
  entry: SpeciesListEntry;
  // Null until the complete institutional collection is available.
  summary: SpeciesBoxSummary | null;
  isBoxCountLoading: boolean;
  onOpen: () => void;
  t: Translator;
}) {
  const unavailable = isBoxCountLoading ? <span aria-label={t('loading')}>…</span> : '';
  const date = (value: string | null | undefined) => summary
    ? value ? formatDisplayDate(value) : t('boxInventoryNoData')
    : unavailable;
  return (
    <li className="species-list-row">
      <div className="box-inventory-cell box-inventory-identity">
        <a
          className="species-list-name"
          href={adminSpeciesPath(entry.target)}
          onClick={(event) => followInApp(event, onOpen)}
        >
          <em>{`${entry.scientificName}${entry.code ? ` (${entry.code})` : ''}`}</em>
        </a>
      </div>
      <span className="species-list-count">
        {entry.strainCount}
        <span className="sr-only">{` ${t(entry.strainCount === 1 ? 'speciesCatalogStrain' : 'speciesCatalogStrains')}`}</span>
      </span>
      <span className="species-list-count species-list-active-count">
        {summary ? `${summary.activeBoxCount} / ${summary.boxCount}` : unavailable}
      </span>
      <div className="box-inventory-cell box-inventory-dates" data-label={t('boxInventoryDates')}>
        <span><small>{t('firstMeasurementOn')}</small><strong>{date(summary?.firstMeasurementOn)}</strong></span>
        <span><small>{t('boxInventoryLastMeasurement')}</small><strong>{date(summary?.lastMeasurementOn)}</strong></span>
      </div>
      <RowActionMenu
        ariaLabel={`${t('boxInventoryActions')}: ${entry.scientificName}`}
        actions={[{ action: 'open', label: t('speciesCreateShowExisting') }]}
        onAction={onOpen}
      />
    </li>
  );
}

type DetailData =
  | { kind: 'profile'; data: InstitutionSpeciesDetail }
  | { kind: 'historical'; data: HistoricalSpeciesDetail };

function SpeciesDetail({
  target, createdCode, offerStrain, onStrainOffered, onCreateBox, organizationId, boxes,
  boxCollectionStatus, onRetryBoxCollection, language, onOpenBox, onOpenZone, onBack, onOpenSpecies, t,
}: {
  key?: string;
  target: AdminSpeciesTarget;
  // Right after registration: ask once whether to add a Strain.
  offerStrain: boolean;
  onStrainOffered: () => void;
  onCreateBox?: (strainId: number) => void;
  boxes: readonly BoxItem[];
  boxCollectionStatus: BoxCollectionStatus;
  onRetryBoxCollection: () => void;
  // Present only right after this profile was registered.
  createdCode: string | null | undefined;
  organizationId: number;
  organizationName: string;
  language: Language;
  onOpenBox: (boxId: number, code: string) => void;
  onOpenZone: (zoneId: number) => void;
  onBack: () => void;
  onOpenSpecies: (target: AdminSpeciesTarget) => void;
  t: Translator;
}) {
  const [state, setState] = useState<LoadState<DetailData>>({ status: 'loading' });
  const [version, setVersion] = useState(0);
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [isUpdated, setIsUpdated] = useState(false);
  const [isStrainCreateOpen, setIsStrainCreateOpen] = useState(false);

  const { confirmAction, confirmActionModal } = useConfirmAction();
  // A Strain just created here, waiting for its detail reread before "create a box?".
  const [boxOffer, setBoxOffer] = useState<SpeciesStrain | null>(null);
  const strainOfferRef = useRef(false);


  useEffect(() => {
    let isCurrent = true;
    setState({ status: 'loading' });
    const request = target.kind === 'profile'
      ? apiGet<InstitutionSpeciesDetail>(profilePath(target.id), { cache: 'no-store' }).then((data): DetailData => ({ kind: 'profile', data }))
      : apiGet<HistoricalSpeciesDetail>(historicalPath(target.id), { cache: 'no-store' }).then(async (data): Promise<DetailData> => {
        // Old URLs remain valid after association; fetch the revision-protected local detail.
        if (isCurrent && data.institution_species_id != null) {
          const profile = await apiGet<InstitutionSpeciesDetail>(profilePath(data.institution_species_id), { cache: 'no-store' });
          return { kind: 'profile', data: profile };
        }
        return { kind: 'historical', data };
      });
    request
      .then((data) => { if (isCurrent) setState({ status: 'ready', data }); })
      .catch((error) => { if (isCurrent) setState(failedState(error)); });
    return () => { isCurrent = false; };
  }, [target.kind, target.id, version, organizationId]);

  const readyProfile = state.status === 'ready' && state.data.kind === 'profile' ? state.data.data : null;
  // Readiness comes from verified server evidence, never from a displayed AAA.
  const canCreateStrain = state.status === 'ready' && state.data.data.can_create_strain === true;
  // Opening a dialog never persists anything: only an established institutional
  // profile can be edited, and only a server-ready one can receive a Strain.
  // Historical Species stay read-only here (the former implicit "resolve" is retired).
  const isHistorical = state.status === 'ready' && state.data.kind === 'historical';

  function openSpeciesAction(action: 'edit' | 'strain') {
    if (!readyProfile || (action === 'strain' && !canCreateStrain)) return;
    if (action === 'strain') setIsStrainCreateOpen(true);
    else { setIsUpdated(false); setIsEditOpen(true); }
  }

  // Species -> Strain: asked once, on the new Species page. No creates nothing.
  useEffect(() => {
    if (!offerStrain || !canCreateStrain || strainOfferRef.current) return;
    strainOfferRef.current = true;
    onStrainOffered();
    void confirmAction({
      title: t('speciesOfferStrainTitle'),
      confirmLabel: t('creationContinueYes'),
      cancelLabel: t('creationContinueNo'),
    }).then((confirmed) => { if (confirmed) setIsStrainCreateOpen(true); });
  }, [offerStrain, canCreateStrain]);

  // Strain -> Box: asked once the new Strain is visible in the reread detail.
  useEffect(() => {
    if (!boxOffer || state.status !== 'ready') return;
    const created = state.data.data.strains.find((strain) => strain.id === boxOffer.id);
    if (!created) return;
    setBoxOffer(null);
    if (!onCreateBox || !created.is_box_usable) return;
    const title = strainTitle({ ...created, name: recordedStrainName(created, language) }, t('createBoxStrainLabel'));
    void confirmAction({
      title: t('strainOfferBoxTitle'),
      message: fillTemplate(t('strainOfferBoxMessage'), { strain: `${title} (${created.code})` }),
      confirmLabel: t('creationContinueYes'),
      cancelLabel: t('creationContinueNo'),
    }).then((confirmed) => { if (confirmed) onCreateBox(created.id); });
  }, [boxOffer, state, onCreateBox]);

  const back = <DetailBackButton label={t('back')} onBack={onBack} desktopClassName="species-back-button" />;

  if (state.status === 'loading') {
    return <section className="admin-section species-detail-page">{back}<PageLoader variant="admin" label={t('loading')} /></section>;
  }
  if (state.status !== 'ready') {
    const message: TranslationKey = state.status === 'forbidden'
      ? 'speciesCatalogForbidden'
      : state.status === 'not_found' ? 'speciesDetailNotFound' : 'speciesDetailLoadError';
    return (
      <section className="admin-section species-detail-page">
        {back}
        <div className={state.status === 'not_found' ? 'species-admin-notice' : 'species-admin-notice is-error'} role="alert">
          <p>{t(message)}</p>
          {state.status === 'error' ? (
            <button className="secondary-button" type="button" onClick={() => setVersion((value) => value + 1)}>
              {t('taxonomyRetry')}
            </button>
          ) : null}
        </div>
      </section>
    );
  }

  const detail = state.data;

  const name = detail.kind === 'profile' ? detail.data.scientific_name : detail.data.scientific_name ?? detail.data.shared_scientific_name;
  const profile = detail.kind === 'profile' ? detail.data : null;
  const code = detail.data.species_code;
  const strains = detail.data.strains;
  const wormsId = detail.data.worms_aphia_id;
  const strainIssue = detail.data.strain_creation_issue;
  const strainIssueKey: TranslationKey = strainIssue === 'species_code_missing' ? 'speciesStrainSpeciesCodeMissing'
    : strainIssue === 'species_code_unusable' ? 'speciesStrainSpeciesCodeUnusable' : 'speciesCodeDataIssue';

  // Total of the Boxes of the listed Strains, only from the complete collection.
  const strainIds = new Set(strains.map((strain) => strain.id));
  const boxCount = boxCollectionStatus === 'ready'
    ? boxes.filter((box) => box.organization.id === organizationId && strainIds.has(box.strain.id)).length
    : null;

  return (
    <section className="admin-section species-detail-page" aria-labelledby="species-detail-title">
      {back}

      <header className="entity-header entity-header--species species-sheet-hero">
        <div className="entity-header__identity species-sheet-identity">
          <p className="box-page-label">{t('speciesDetailLabel')}</p>
          {/* The institutional code sits beside the name: no separate identification card. */}
          <h2 id="species-detail-title">
            {name}
            {code ? <span className="species-sheet-code">{`(${code})`}</span> : null}
          </h2>
          <ul className="species-sheet-context">

            <li>{strains.length} {t(strains.length === 1 ? 'speciesCatalogStrain' : 'speciesCatalogStrains')}</li>
            {boxCount !== null ? <li>{boxCount} {t(boxCount === 1 ? 'speciesDetailBox' : 'speciesDetailBoxes')}</li> : null}

            {/* A recorded reference, without any reloaded taxonomic status. */}
            {wormsId != null ? (
              <li>
                <a href={detail.data.worms_url ?? undefined} target="_blank" rel="noreferrer">
                  {t('speciesCreateWormsReference')} {wormsId}
                  <ExternalLink aria-hidden="true" size={12} />
                </a>
              </li>
            ) : null}
          </ul>
        </div>
        <div className="entity-header__actions box-action-stack species-action-stack">
          <button className="primary-button" type="button" disabled={!canCreateStrain || isHistorical}
            aria-describedby={isHistorical ? 'species-historical-readonly' : !canCreateStrain ? 'species-code-issue' : undefined}
            onClick={() => { openSpeciesAction('strain'); }}>
            <PolypbaseIcon name="plus" size={16} />
            {t('speciesStrainNew')}
          </button>
          {/* Editing is independent of AAA readiness and of any WoRMS link. */}
          <button className="secondary-button" type="button" disabled={isHistorical}
            aria-describedby={isHistorical ? 'species-historical-readonly' : undefined}
            onClick={() => { openSpeciesAction('edit'); }}>
            {t('speciesDetailIdentification')}
          </button>
        </div>
      </header>


      {isHistorical ? (
        <p id="species-historical-readonly" className="species-admin-notice" role="status">{t('speciesDetailActionsUnavailable')}</p>
      ) : !canCreateStrain ? <p id="species-code-issue" className="species-code-issues" role="alert">{t(strainIssueKey)}</p> : null}
      {createdCode !== undefined && !isUpdated ? (
        <p className="species-detail-message" role="status">
          {createdCode
            ? renderTemplate(t('speciesDetailCreated'), { code: <strong>{createdCode}</strong> })
            : t('speciesDetailCreatedWithoutCode')}
        </p>
      ) : null}




      <SpeciesStrainsPanel
        strains={strains}
        boxes={boxes}
        boxCollectionStatus={boxCollectionStatus}
        onRetryBoxCollection={onRetryBoxCollection}
        organizationId={organizationId}

        speciesName={name}

        language={language}
        onOpenBox={onOpenBox}
        onOpenZone={onOpenZone}
        onCreateBox={onCreateBox}
        t={t}
      />

      {isStrainCreateOpen && profile && isUsableSpeciesCode(code) ? (
        <StrainCreateDialog
          profile={profile}
          speciesCode={code}
          language={language}
          t={t}
          onClose={() => setIsStrainCreateOpen(false)}
          onCreated={(created) => {
            setIsStrainCreateOpen(false);
            setIsUpdated(false);
            setBoxOffer(created.strain);

            // Creation returns a compact projection; reread the full recorded metadata.
            setVersion((value) => value + 1);
          }}
        />
      ) : null}

      {isEditOpen && profile ? (
        <SpeciesFormDialog
          mode="edit"
          profile={profile}
          t={t}
          onClose={() => setIsEditOpen(false)}
          onSaved={(saved) => {
            setIsEditOpen(false);
            setIsUpdated(true);
            setState({ status: 'ready', data: { kind: 'profile', data: { ...profile, ...saved, strains: profile.strains } } });
          }}
          onOpenExisting={(existing) => {
            setIsEditOpen(false);
            onOpenSpecies(existing);
          }}
          onReload={() => {
            setIsEditOpen(false);
            setVersion((value) => value + 1);
          }}
        />
      ) : null}
      {confirmActionModal}
    </section>
  );
}

type WormsState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'invalid'; problem: WormsQueryProblem }
  | { status: 'unavailable' }
  | { status: 'results'; query: string; generation: number; results: WormsRecord[]; truncated: boolean; limit: number };

// WoRMS rank names; an unlisted rank is shown as returned by WoRMS.
const WORMS_RANK_KEYS: Record<string, TranslationKey> = {
  species: 'speciesCreateRankSpecies',
  subspecies: 'speciesCreateRankSubspecies',
  variety: 'speciesCreateRankVariety',
  forma: 'speciesCreateRankForma',
  genus: 'speciesCreateRankGenus',
  subgenus: 'speciesCreateRankSubgenus',
  family: 'speciesCreateRankFamily',
};

const WORMS_QUERY_PROBLEM_KEYS: Record<WormsQueryProblem, TranslationKey> = {
  unsupported_characters: 'speciesCreateWormsUnsupportedQuery',
  too_short: 'speciesCreateWormsInvalidQuery',
  too_long: 'speciesCreateWormsQueryTooLong',
};

const CODE_FAILURE_KEYS: Record<string, TranslationKey> = {
  name_not_derivable: 'speciesCodeNameNotDerivable',
  code_exhausted: 'speciesCodeExhausted',
  historical_code_conflict: 'speciesCodeHistoricalConflict',
};

function WormsRank({ record, t }: { record: WormsRecord; t: Translator }) {
  const key = WORMS_RANK_KEYS[record.rank.toLowerCase()];
  const label = key ? t(key) : record.rank;
  if (!label) return null;
  return <span className={canReferenceWormsRecord(record) ? 'species-worms-rank' : 'species-worms-rank is-above-species'}>{label}</span>;
}

function WormsStatus({ record, t }: { record: WormsRecord; t: Translator }) {
  const accepted = isWormsAccepted(record);
  const label = accepted
    ? t('speciesCreateAccepted')
    : record.status === 'unaccepted' ? t('speciesCreateUnaccepted') : record.status;
  return label ? <span className={accepted ? 'species-worms-status is-accepted' : 'species-worms-status'}>{label}</span> : null;
}

// A refusal shown in the dialog, with at most one explicit follow-up.
type Feedback = {
  content: ReactNode;
  action?: { kind: 'open'; target: AdminSpeciesTarget } | { kind: 'without_reference' } | { kind: 'reload' };
};

function SpeciesFormDialog({
  mode,
  profile,
  t,
  onClose,
  onSaved,
  onOpenExisting,
  onReload,
}: {
  mode: 'create' | 'edit';
  profile?: InstitutionSpecies;
  t: Translator;
  onClose: () => void;
  onSaved: (profile: InstitutionSpecies) => void;
  onOpenExisting: (target: AdminSpeciesTarget) => void;
  onReload?: () => void;
}) {
  const isEdit = mode === 'edit' && profile !== undefined;
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);
  const { dialogRef, initialFocusRef, isBusy, close, submit } = useMutationDialog<HTMLInputElement>(false, onClose);
  const [name, setName] = useState(isEdit ? profile.scientific_name : '');
  const [reference, setReference] = useState<FormReference | null>(
    isEdit && profile.worms_aphia_id !== null
      ? { kind: 'saved', aphiaId: profile.worms_aphia_id, url: profile.worms_url, name: profile.scientific_name }
      : null,
  );
  const [isChoosing, setIsChoosing] = useState(!isEdit);
  useEffect(() => { initialFocusRef.current?.focus(); }, [isChoosing, initialFocusRef]);
  const [query, setQuery] = useState(isEdit ? profile.scientific_name : '');
  const [worms, setWorms] = useState<WormsState>({ status: 'idle' });
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const searchGeneration = useRef(0);
  const renderedSearchGeneration = searchGeneration.current;
  useEffect(() => () => { searchGeneration.current += 1; }, []);

  const normalizedName = normalizeScientificName(name);
  const isCompatible = isReferenceCompatible(reference, name);
  const aphiaId = referenceAphiaId(reference);
  const isUnchanged = isEdit && normalizedName === profile.scientific_name && aphiaId === profile.worms_aphia_id;
  const canSubmit = !isChoosing && normalizedName.length > 0 && isCompatible && !isUnchanged && !isBusy;
  const titleId = isEdit ? 'species-edit-title' : 'species-create-title';

  function invalidateSearch() {
    searchGeneration.current += 1;
    setWorms({ status: 'idle' });
  }

  async function searchWorms() {
    setFeedback(null);
    const searchValue = isChoosing ? query : name;
        const searchQuery = normalizeWormsQuery(searchValue);
    const generation = ++searchGeneration.current;
    if (!searchQuery) {
      setWorms({ status: 'invalid', problem: searchValue.trim() ? getWormsQueryProblem(searchValue) ?? 'unsupported_characters' : 'too_short' });
      return;
    }
    setWorms({ status: 'loading' });
    try {
      const response = await apiGet<WormsSearchResponse>(`${WORMS_SEARCH_PATH}?${new URLSearchParams({ q: searchQuery })}`);
      if (generation !== searchGeneration.current) return;
      setWorms({ status: 'results', query: searchQuery, generation, results: response.results, truncated: response.truncated === true, limit: response.limit });
    } catch (requestError) {
      if (generation !== searchGeneration.current) return;
      setWorms(requestError instanceof ApiError && requestError.status === 400
        ? { status: 'invalid', problem: 'unsupported_characters' }
        : { status: 'unavailable' });
    }
  }

  // Enter searches only in the search step or when verifying an existing species.
  // Creating from a final name always requires the explicit primary button.
  function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((!isChoosing && !isEdit) || isBusy || worms.status === 'loading') return;
    void searchWorms();
  }

  function existingFeedback(match: NonNullable<WormsRecord['local_species']>): Feedback {
    const key: TranslationKey = match.requires_historical_adoption ? 'speciesCreateHistoricalAdoption' : 'speciesCreateExists';
    return {
      content: renderTemplate(t(key), { name: <em>{match.scientific_name}</em> }),
      action: { kind: 'open', target: adminSpeciesTargetFor(match) },
    };
  }

  function pick(record: WormsRecord, fromReference = false) {
    if (renderedSearchGeneration !== searchGeneration.current) return;
    if (fromReference) {
      if (reference?.kind !== 'record' || !hasDistinctAcceptedName(reference.record)) return;
    } else if (worms.status !== 'results' || worms.generation !== searchGeneration.current
      || worms.query !== normalizeWormsQuery(isChoosing ? query : name) || !worms.results.includes(record)) return;
    // Genus or higher is never attached to a Species as its WoRMS reference.
    if (!canReferenceWormsRecord(record)) return;
    setFeedback(null);
    // Another species of this institution already carries this name or reference.
    const match = record.local_species;
    if (match && !(isEdit && match.institution_species_id === profile.id)) {
      setFeedback(existingFeedback(match));
      return;
    }
    invalidateSearch();
    setName(record.scientific_name);
    setIsChoosing(false);
    setReference({ kind: 'record', record });
  }

  function failureFeedback(error: unknown, sentAphiaId: number | null): Feedback {
    if (!(error instanceof ApiError)) return { content: t(isEdit ? 'speciesEditError' : 'speciesCreateError') };
    if (error.status === 403) return { content: t('speciesCatalogActionForbidden') };
    const failure = getServiceFailure(error.data);
    const code = failure?.code;
    const named = (key: TranslationKey, value?: string) => renderTemplate(t(key), { name: <em>{value ?? ''}</em> });
    if (code === 'worms_reference_unavailable' && sentAphiaId !== null) {
      return { content: t('speciesCreateWormsReferenceUnavailable'), action: { kind: 'without_reference' } };
    }
    if (isWormsReferenceFailure(code)) return { content: t('speciesCreateWormsReferenceRejected') };
    if (error.status === 400) return { content: t('speciesCreateInvalidName') };
    if (error.status === 404) return { content: t('speciesDetailNotFound') };
    if (code && CODE_FAILURE_KEYS[code]) return { content: t(CODE_FAILURE_KEYS[code]) };
    switch (code) {
      case 'stale_revision':
        return { content: t('speciesEditStale'), action: onReload ? { kind: 'reload' } : undefined };
      case 'institution_species_exists':
        return {
          content: named('speciesCreateExists', failure?.scientific_name),
          action: failure?.institution_species_id ? { kind: 'open', target: { kind: 'profile', id: failure.institution_species_id } } : undefined,
        };
      case 'historical_adoption_required':
        return {
          content: named('speciesCreateHistoricalAdoption', failure?.shared_scientific_name),
          action: failure?.species_id ? { kind: 'open', target: { kind: 'historical', id: failure.species_id } } : undefined,
        };
      case 'institution_aphia_id_exists':
        return {
          content: named('speciesCreateAphiaExists', failure?.scientific_name),
          action: failure?.institution_species_id ? { kind: 'open', target: { kind: 'profile', id: failure.institution_species_id } } : undefined,
        };
      case 'institution_species_name_exists':
        return {
          content: t('speciesEditNameExists'),
          action: failure?.institution_species_id ? { kind: 'open', target: { kind: 'profile', id: failure.institution_species_id } } : undefined,
        };
      case 'historical_name_collision':
        return {
          content: t('speciesEditHistoricalNameCollision'),
          action: failure?.species_id ? { kind: 'open', target: { kind: 'historical', id: failure.species_id } } : undefined,
        };
      case 'shared_reference_conflict':
        return {
          content: t('speciesCreateSharedReferenceConflict'),
          action: sentAphiaId !== null ? { kind: 'without_reference' } : undefined,
        };
      default:
        return { content: t(isEdit ? 'speciesEditError' : 'speciesCreateError') };
    }
  }

  // Django verifies any reference with WoRMS. Dropping it is always an explicit choice.
  async function save({ withoutReference = false } = {}) {
    if (withoutReference) setReference(null);
    const sentAphiaId = withoutReference ? null : aphiaId;
    if (!normalizedName || isBusy || (!withoutReference && !canSubmit)) return;
    setFeedback(null);
    await submit(async () => {
      try {
        const saved = isEdit
          ? await apiPatch<InstitutionSpecies>(profilePath(profile.id), buildEditPayload(profile.revision, name, sentAphiaId))
          : await apiPost<InstitutionSpecies>(LIST_PATH, buildRegistrationPayload(name, sentAphiaId));
        if (isMountedRef.current) onSaved(saved);
      } catch (requestError) {
        if (isMountedRef.current) setFeedback(failureFeedback(requestError, sentAphiaId));
      }
    });
  }

  function followUp(action: NonNullable<Feedback['action']>) {
    if (action.kind === 'open') onOpenExisting(action.target);
    else if (action.kind === 'reload') onReload?.();
    else void save({ withoutReference: true });
  }

  const followUpLabel: Record<NonNullable<Feedback['action']>['kind'], TranslationKey> = {
    open: 'speciesCreateShowExisting',
    reload: 'speciesEditReload',
    without_reference: isEdit ? 'speciesEditWithoutReference' : 'speciesCreateWithoutReference',
  };
  const allResults = worms.status === 'results' ? worms.results : [];
    const results = allResults.filter(canReferenceWormsRecord).sort((a, b) => {
      const exact = (item: WormsRecord) => item.scientific_name.toLowerCase() === (worms.status === 'results' ? worms.query.toLowerCase() : '');
      return Number(exact(b)) - Number(exact(a))
        || Number(b.rank.toLowerCase() === 'species') - Number(a.rank.toLowerCase() === 'species')
        || Number(isWormsAccepted(b)) - Number(isWormsAccepted(a));
    });

  const truncatedLimit = worms.status === 'results' && worms.truncated ? worms.limit : null;
  const record = reference?.kind === 'record' ? reference.record : null;

  return (
    <ModalPortal>
      <div className="modal-backdrop box-dialog-backdrop" role="presentation" onClick={close}>
        <section
          ref={dialogRef}
          aria-busy={isBusy}
          className="box-dialog species-form-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
          onClick={(event) => event.stopPropagation()}
        >
          <header className="box-dialog-heading">
            <div><h2 id={titleId}>{t(isEdit ? 'speciesDetailEdit' : 'speciesCatalogNewSpecies')}</h2></div>
            <button className="icon-button box-dialog-close" type="button" aria-label={t('close')} title={t('close')} disabled={isBusy} onClick={close}>
              <PolypbaseIcon name="close" size={19} aria-hidden="true" />
            </button>
          </header>

          <form className="box-dialog-form" onSubmit={handleSearchSubmit} noValidate>
            <div className="box-dialog-body species-form-body">
              {isChoosing ? (
                <div className="species-search-field">
                  <label htmlFor="species-form-search">{t('taxonomyScientificName')}</label>
                  <div className="species-search-input-wrap">
                    <input ref={initialFocusRef} id="species-form-search" className="species-name-input"
                      value={query} maxLength={150} autoComplete="off" spellCheck={false}
                      placeholder={t('speciesCreateNamePlaceholder')} disabled={isBusy}
                      onChange={(event) => { setQuery(event.target.value); setFeedback(null); invalidateSearch(); }} />
                    <button type="submit" className="primary-button species-worms-button"
                      disabled={isBusy || worms.status === 'loading'}>
                      <Search aria-hidden="true" size={16} />
                      {t(worms.status === 'loading' ? 'speciesCreateWormsSearching' : 'speciesCreateWormsSearch')}
                    </button>
                  </div>
                  <p className="species-search-hint">{t(worms.status === 'loading' ? 'speciesCreateWormsSearching' : 'speciesSearchHint')}</p>
                </div>
              ) : (
              <div className="species-name-field">
                <div className="species-final-heading">
                  <span className="species-selection-label">{t(reference ? 'speciesSearchSelected' : 'speciesSearchManualName')}</span>
                  <button type="button" className="species-admin-link species-change-search" disabled={isBusy}
                    onClick={() => { setQuery(name); setIsChoosing(true); setFeedback(null); invalidateSearch(); }}>
                    {t(isEdit ? 'speciesSearchChange' : reference ? 'speciesSearchChangeSpecies' : 'speciesCreateWormsSearch')}
                  </button>
                </div>
                <label htmlFor="species-form-name">{t('taxonomyScientificName')}</label>
                <div className={isEdit ? 'species-name-row is-edit' : 'species-name-row'}>
                  <input
                    ref={initialFocusRef}
                    id="species-form-name"
                    className="species-name-input"
                    maxLength={150}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={isEdit ? undefined : t('speciesCreateNamePlaceholder')}
                    value={name}
                    disabled={isBusy}
                    aria-invalid={!isCompatible}
                    aria-describedby={!isCompatible ? 'species-form-reference-mismatch' : undefined}
                    onChange={(event) => {
                      setName(event.target.value);
                      setFeedback(null);
                      invalidateSearch();
                    }}
                  />
                  {isEdit ? <button
                    className="secondary-button species-worms-button"
                    type="submit"
                    disabled={isBusy || worms.status === 'loading'}
                  >
                    <Search aria-hidden="true" size={16} />
                    {t(worms.status === 'loading' ? 'speciesCreateWormsSearching' : 'speciesEditWormsCheck')}
                  </button> : null}
                </div>

                {reference ? (
                  <div className={isCompatible ? 'species-worms-reference' : 'species-worms-reference is-mismatch'}>
                    <span className="species-worms-reference-main">
                      <a href={(record ? record.url : reference.kind === 'saved' ? reference.url : null) ?? undefined} target="_blank" rel="noreferrer">
                        {t('speciesCreateWormsReference')} {referenceAphiaId(reference)}
                        <ExternalLink aria-hidden="true" size={12} />
                      </a>
                      {record ? <WormsRank record={record} t={t} /> : null}
                      {record ? <WormsStatus record={record} t={t} /> : null}
                      {record?.authority ? <span className="species-worms-authority">{record.authority}</span> : null}
                    </span>
                    <button className="species-admin-link" type="button" disabled={isBusy} onClick={() => setReference(null)}>
                      {t('speciesCreateRemoveReference')}
                    </button>
                    {!isCompatible ? (
                      <p className="species-worms-mismatch" id="species-form-reference-mismatch">{t('speciesWormsReferenceMismatch')}</p>
                    ) : null}
                    {record && isCompatible && hasDistinctAcceptedName(record) ? (
                      <span className="species-worms-accepted">
                        {t('speciesCreateAcceptedName')} <em>{record.valid_name}</em>
                        <button
                          className="species-admin-link"
                          type="button"
                          disabled={isBusy}
                          onClick={() => pick(acceptedRecordFrom(record), true)}
                        >
                          {t('speciesCreateUseAcceptedName')}
                        </button>
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
              )}

              <div className="species-worms-feedback" aria-live="polite">
                {worms.status === 'invalid' ? <p className="species-worms-note">{t(WORMS_QUERY_PROBLEM_KEYS[worms.problem])}</p> : null}
                {worms.status === 'unavailable' ? <p className="species-worms-note">{t('speciesCreateWormsUnavailable')}</p> : null}
                {worms.status === 'results' && !results.length ? <p className="species-worms-note">{t('speciesCreateWormsEmpty')}</p> : null}
                {truncatedLimit !== null ? (
                  <p className="species-worms-note is-truncated">{renderTemplate(t('speciesCreateWormsTruncated'), { count: truncatedLimit })}</p>
                ) : null}
              </div>

              {results.length > 0 ? (
                <section className="species-search-results" aria-labelledby="species-search-results-title">
                  <h3 id="species-search-results-title">{t('speciesCreateWormsResults')}</h3>
                <ul className="species-worms-results" aria-label={t('speciesCreateWormsResults')}>
                  {results.map((item) => {
                    const isSelected = record?.aphia_id === item.aphia_id;
                    // The species being edited is not "already recorded" against itself.
                    const isOtherSpecies = Boolean(item.local_species)
                      && !(isEdit && item.local_species?.institution_species_id === profile.id);
                    const content = (
                      <>
                        <span className="species-worms-result-name">
                          <em>{item.scientific_name}</em>
                          {item.authority ? <span className="species-worms-authority">{item.authority}</span> : null}
                        </span>
                        <span className="species-worms-result-meta">
                          <WormsRank record={item} t={t} />
                          <WormsStatus record={item} t={t} />
                          {hasDistinctAcceptedName(item) ? (
                            <span className="species-worms-result-accepted">
                              {t('speciesCreateAcceptedName')} <em>{item.valid_name}</em>
                            </span>
                          ) : null}
                        </span>
                        {isOtherSpecies ? <span className="species-worms-recorded">{t('speciesCreateAlreadyRecorded')}</span> : null}
                      </>
                    );
                    return (
                      <li key={item.aphia_id}>
                        <button
                            type="button"
                            className={['species-worms-result', isSelected ? 'is-selected' : '', isOtherSpecies ? 'is-recorded' : '']
                              .filter(Boolean).join(' ')}
                            aria-pressed={isSelected}
                            disabled={isBusy}
                            onClick={() => pick(item)}
                          >
                            {content}
                        </button>
                      </li>
                    );
                  })}
                </ul>
                </section>
              ) : null}

              {feedback ? (
                <div className="species-admin-notice is-existing" role="alert">
                  <p>{feedback.content}</p>
                  {feedback.action ? (
                    <button
                      className="secondary-button species-form-follow-up"
                      type="button"
                      disabled={isBusy}
                      onClick={() => { if (feedback.action) followUp(feedback.action); }}
                    >
                      {t(followUpLabel[feedback.action.kind])}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
            <footer className="box-dialog-actions species-form-actions">
              {isChoosing ? (
                <button type="button" className="species-admin-link species-manual-action" disabled={isBusy}
                  onClick={() => { setName(query); setReference(null); setIsChoosing(false); setFeedback(null); invalidateSearch(); }}>
                  {t('speciesSearchManual')}
                </button>
              ) : null}
              <button className="secondary-button" type="button" disabled={isBusy} onClick={close}>{t('cancel')}</button>
              {!isChoosing ? (
                <button className="primary-button species-form-submit" type="button" disabled={!canSubmit} onClick={() => { void save(); }}>
                  {t(isBusy ? (isEdit ? 'saving' : 'createBoxCreating') : isEdit ? 'speciesEditSubmit' : 'speciesCreateSubmit')}
                </button>
              ) : null}
            </footer>
          </form>
        </section>
      </div>
    </ModalPortal>
  );
}

type LocationsState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; locations: CollectionLocation[]; countries: string[] };

type StrainFeedback = {
  content: ReactNode;
  // A recorded location without a country, offered for an explicit link.
  link?: CollectionLocation;
};

const STRAIN_FAILURE_KEYS: Record<string, TranslationKey> = {
  species_code_missing: 'speciesStrainSpeciesCodeMissing',
  species_code_unusable: 'speciesStrainSpeciesCodeUnusable',
  collection_location_code_unusable: 'speciesStrainLocationCodeUnusable',
  strain_number_unavailable: 'speciesStrainNumberUnavailable',
  strain_code_conflict: 'speciesStrainConflict',
  strain_identity_invalid: 'speciesStrainCreateError',
  strain_creation_request_conflict: 'speciesStrainRequestConflict',
};

const ACQUISITION_LABEL_KEYS: Record<AcquisitionType, TranslationKey> = {
  donation: 'strainAcquisitionDonation',
  field_collection: 'strainAcquisitionFieldCollection',
  artificial_fertilization_wild: 'strainAcquisitionFertilizationWild',
  artificial_fertilization_captive: 'strainAcquisitionFertilizationCaptive',
};

const COORDINATE_PROBLEM_KEYS: Record<CoordinateProblem, TranslationKey> = {
  invalid: 'strainGpsInvalid',
  out_of_range: 'strainGpsOutOfRange',
  required_pair: 'strainGpsPair',
};

// Fields in the approved order: country, acquisition type, donor (donation only),
// GPS, then the optional name. The Species is already known.
function StrainCreateDialog({ profile, speciesCode, language, t, onClose, onCreated }: {
  profile: InstitutionSpecies;
  speciesCode: string;
  language: Language;
  t: Translator;
  onClose: () => void;
  onCreated: (created: StrainCreationResponse) => void;
}) {
  const { dialogRef, initialFocusRef, isBusy, close, submit } = useMutationDialog<HTMLSelectElement>(false, onClose);
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);
  const [locations, setLocations] = useState<LocationsState>({ status: 'loading' });
  const [locationsVersion, setLocationsVersion] = useState(0);
  const [country, setCountry] = useState('');
  const [bbbCode, setBbbCode] = useState('');
  const [link, setLink] = useState<CollectionLocation | null>(null);
  const [acquisitionType, setAcquisitionType] = useState<AcquisitionType | ''>('');
  const [donor, setDonor] = useState('');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [name, setName] = useState('');
  const [isValidationShown, setIsValidationShown] = useState(false);
  const [feedback, setFeedback] = useState<StrainFeedback | null>(null);
  // One idempotency key per deliberate intent. A retry of the same payload reuses
  // it (the server replays the recorded Strain); changing the payload starts a new
  // intent. The ref survives re-renders, so an ordinary retry cannot change it.
  const requestRef = useRef<{ signature: string; requestId: string } | null>(null);

  useEffect(() => {
    let isCurrent = true;
    setLocations({ status: 'loading' });
    apiGet<CollectionLocationsResponse>(LOCATIONS_PATH)
      .then((data) => {
        if (isCurrent) setLocations({ status: 'ready', locations: data.collection_locations, countries: data.countries ?? [] });
      })
      .catch(() => { if (isCurrent) setLocations({ status: 'error' }); });
    return () => { isCurrent = false; };
  }, [locationsVersion]);

  const readyLocations = locations.status === 'ready' ? locations.locations : [];
  const countryOptions = useMemo(
    () => sortedCountries(locations.status === 'ready' ? locations.countries : [], language),
    [locations, language],
  );
  // The single BBB already recorded for this country here, if any.
  const mapped = country ? readyLocations.find((item) => item.country === country) ?? null : null;
  const normalizedCode = normalizeLocationCode(bbbCode);
  const countryProblem: TranslationKey | null = country ? null : 'strainCountryRequired';
  const codeProblem: TranslationKey | null = !country || mapped || link || LOCATION_CODE_PATTERN.test(normalizedCode)
    ? null : 'strainCountryCodeInvalid';
  const gpsProblems = coordinateProblems(latitude, longitude);
  const isValid = !countryProblem && !codeProblem && !gpsProblems.latitude && !gpsProblems.longitude;
  const isLocationsReady = locations.status === 'ready';

  function edit(update: () => void) {
    update();
    setFeedback(null);
  }

  function reloadLocations() {
    setLink(null);
    setLocationsVersion((value) => value + 1);
  }

  function failureFeedback(error: unknown): StrainFeedback {
    if (!(error instanceof ApiError)) return { content: t('speciesStrainCreateError') };
    if (error.status === 403) return { content: t('speciesCatalogActionForbidden') };
    if (error.status === 404) return { content: t('speciesDetailNotFound') };
    const failure = getServiceFailure(error.data) as (ReturnType<typeof getServiceFailure> & {
      collection_location?: CollectionLocation;
    }) | null;
    const code = failure?.code;
    const existing = failure?.collection_location;
    if ((code === 'collection_location_code_taken' || code === 'collection_location_name_taken') && existing) {
      if (existing.country) {
        // This code already represents another country: never reused for this one.
        return { content: renderTemplate(t('strainCountryCodeTaken'), {
          name: <strong>{`${countryName(existing.country, language)} (${existing.code})`}</strong>,
        }) };
      }
      const key = code === 'collection_location_code_taken' ? 'strainCountryCodeTaken' : 'strainCountryNameTaken';
      return {
        content: renderTemplate(t(key), { name: <strong>{existing.name}</strong>, code: <strong>{existing.code}</strong> }),
        link: existing,
      };
    }
    if (code === 'country_already_coded') {
      reloadLocations();
      return { content: renderTemplate(t('strainCountryAlreadyCoded'), { code: <strong>{existing?.code ?? ''}</strong> }) };
    }
    if (code === 'collection_location_country_conflict') {
      reloadLocations();
      return { content: t('strainCountryLocationConflict') };
    }
    if (code && STRAIN_FAILURE_KEYS[code]) return { content: t(STRAIN_FAILURE_KEYS[code]) };
    const fieldErrors = error.data as { link_collection_location?: unknown } | undefined;
    if (error.status === 400 && Array.isArray(fieldErrors?.link_collection_location)
      && fieldErrors.link_collection_location.includes('not_found')) {
      reloadLocations();
      return { content: t('speciesStrainLocationNotFound') };
    }
    return { content: t('speciesStrainCreateError') };
  }

  async function save() {
    setIsValidationShown(true);
    if (!isLocationsReady || !isValid || isBusy) return;
    setFeedback(null);
    const payload = buildStrainPayload({
      country, bbbCode: normalizedCode, linkLocationId: link?.id ?? null, acquisitionType, donor, latitude, longitude, name,
    }, { mapped: mapped !== null, language });
    const signature = JSON.stringify(payload);
    if (requestRef.current?.signature !== signature) {
      requestRef.current = { signature, requestId: window.crypto.randomUUID() };
    }
    const requestId = requestRef.current.requestId;
    await submit(async () => {
      try {
        // A successful idempotent replay (200) is handled exactly like a creation.
        const created = await apiPost<StrainCreationResponse>(
          profileStrainsPath(profile.id), { ...payload, request_id: requestId },
        );
        if (isMountedRef.current) onCreated(created);
      } catch (requestError) {
        if (isMountedRef.current) setFeedback(failureFeedback(requestError));
      }
    });
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void save();
  }

  const fieldError = (id: string, key: TranslationKey | null | undefined) => (
    isValidationShown && key ? <small id={id} className="strain-field-error">{t(key)}</small> : null
  );
  const latitudeKey = gpsProblems.latitude ? COORDINATE_PROBLEM_KEYS[gpsProblems.latitude] : null;
  const longitudeKey = gpsProblems.longitude ? COORDINATE_PROBLEM_KEYS[gpsProblems.longitude] : null;

  return (
    <ModalPortal>
      <div className="modal-backdrop box-dialog-backdrop" role="presentation" onClick={close}>
        <section
          ref={dialogRef}
          aria-busy={isBusy}
          className="box-dialog species-form-dialog strain-create-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="strain-create-title"
          aria-describedby="strain-create-species"
          tabIndex={-1}
          onClick={(event) => event.stopPropagation()}
        >
          <header className="box-dialog-heading">
            <div>
              <h2 id="strain-create-title">{t('speciesStrainNew')}</h2>
              <p id="strain-create-species" className="strain-create-species"><em>{profile.scientific_name}</em> ({speciesCode})</p>
            </div>
            <button className="icon-button box-dialog-close" type="button" aria-label={t('close')} title={t('close')} disabled={isBusy} onClick={close}>
              <PolypbaseIcon name="close" size={19} aria-hidden="true" />
            </button>
          </header>

          <form className="box-dialog-form" onSubmit={handleSubmit} noValidate>
            <div className="box-dialog-body species-form-body">
              {locations.status === 'loading' ? <p className="species-search-hint" role="status">{t('loading')}</p> : null}
              {locations.status === 'error' ? (
                <div className="species-admin-notice is-error" role="alert">
                  <p>{t('speciesStrainLocationsError')}</p>
                  <button className="secondary-button" type="button" onClick={() => setLocationsVersion((value) => value + 1)}>
                    {t('taxonomyRetry')}
                  </button>
                </div>
              ) : null}

              <fieldset className="strain-location-fieldset" disabled={isBusy || !isLocationsReady}>
                <label className="strain-location-field strain-country-field">
                  <span>{t('adminCountry')}</span>
                  <select
                    ref={initialFocusRef}
                    value={country}
                    required
                    aria-invalid={isValidationShown && countryProblem !== null}
                    aria-describedby={isValidationShown && countryProblem ? 'strain-country-error' : undefined}
                    onChange={(event) => edit(() => { setCountry(event.target.value); setLink(null); })}
                  >
                    <option value="">{t('strainCountryChoose')}</option>
                    {countryOptions.map((option) => <option key={option.code} value={option.code}>{option.name}</option>)}
                  </select>
                  {fieldError('strain-country-error', countryProblem)}
                </label>

                {country && mapped ? (
                  <p className="species-search-hint strain-country-code" role="status">
                    {renderTemplate(t('strainCountryCodeRecorded'), { code: <strong>{mapped.code}</strong> })}
                  </p>
                ) : null}
                {country && !mapped && link ? (
                  <div className="strain-country-link">
                    <p className="species-search-hint" role="status">
                      {renderTemplate(t('strainCountryLinked'), { code: <strong>{link.code}</strong>, name: <strong>{link.name}</strong> })}
                    </p>
                    <button className="secondary-button" type="button" onClick={() => edit(() => setLink(null))}>
                      {t('strainCountryLinkCancel')}
                    </button>
                  </div>
                ) : null}
                {country && !mapped && !link ? (
                  <label className="strain-location-field strain-location-code-field">
                    <span>{t('strainCountryCodeLabel')}</span>
                    <input
                      value={normalizedCode} maxLength={3} autoComplete="off" spellCheck={false} required
                      className="species-detail-code"
                      aria-invalid={isValidationShown && codeProblem !== null}
                      aria-describedby={isValidationShown && codeProblem ? 'strain-country-code-error' : undefined}
                      onChange={(event) => edit(() => setBbbCode(event.target.value))}
                    />
                    {fieldError('strain-country-code-error', codeProblem)}
                  </label>
                ) : null}

                <label className="strain-location-field">
                  <span>{t('strainAcquisitionType')}</span>
                  <select value={acquisitionType}
                    onChange={(event) => edit(() => setAcquisitionType(event.target.value as AcquisitionType | ''))}>
                    <option value="">{t('strainAcquisitionNone')}</option>
                    {ACQUISITION_TYPES.map((value) => <option key={value} value={value}>{t(ACQUISITION_LABEL_KEYS[value])}</option>)}
                  </select>
                </label>

                {acquisitionType === 'donation' ? (
                  <label className="strain-location-field">
                    <span>{t('strainDonorInstitution')}</span>
                    <input value={donor} maxLength={150} autoComplete="off" onChange={(event) => edit(() => setDonor(event.target.value))} />
                  </label>
                ) : null}

                <fieldset className="strain-location-fieldset strain-gps-fieldset" aria-describedby="strain-gps-hint">
                  <legend>{t('strainGps')}</legend>
                  <small id="strain-gps-hint" className="species-search-hint">{t('strainGpsHint')}</small>
                  <div className="strain-location-new strain-gps-pair">
                    <label className="strain-location-field">
                      <span>{t('strainGpsLatitude')}</span>
                      <input value={latitude} inputMode="decimal" autoComplete="off"
                        aria-invalid={isValidationShown && latitudeKey !== null}
                        aria-describedby={isValidationShown && latitudeKey ? 'strain-latitude-error' : undefined}
                        onChange={(event) => edit(() => setLatitude(event.target.value))} />
                      {fieldError('strain-latitude-error', latitudeKey)}
                    </label>
                    <label className="strain-location-field">
                      <span>{t('strainGpsLongitude')}</span>
                      <input value={longitude} inputMode="decimal" autoComplete="off"
                        aria-invalid={isValidationShown && longitudeKey !== null}
                        aria-describedby={isValidationShown && longitudeKey ? 'strain-longitude-error' : undefined}
                        onChange={(event) => edit(() => setLongitude(event.target.value))} />
                      {fieldError('strain-longitude-error', longitudeKey)}
                    </label>
                  </div>
                </fieldset>

                {/* Optional and last: without a name, the server number is displayed. */}
                <label className="strain-location-field strain-name-field">
                  <span>{t('strainName')}</span>
                  <input value={name} maxLength={150} autoComplete="off" aria-describedby="strain-name-hint"
                    onChange={(event) => edit(() => setName(event.target.value))} />
                  <small id="strain-name-hint" className="species-search-hint">{t('strainNameHint')}</small>
                </label>
              </fieldset>

              {feedback ? (
                <div className="species-admin-notice is-existing" role="alert">
                  <p>{feedback.content}</p>
                  {feedback.link ? (
                    <button className="secondary-button species-form-follow-up" type="button" disabled={isBusy}
                      onClick={() => {
                        const target = feedback.link;
                        if (!target) return;
                        setLink(target);
                        setFeedback(null);
                      }}>
                      {fillTemplate(t('strainCountryLinkOffer'), { code: feedback.link.code })}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
            <footer className="box-dialog-actions">
              <button className="secondary-button" type="button" disabled={isBusy} onClick={close}>{t('cancel')}</button>
              {/* Like the Species dialog, saving is an explicit click, never an implicit Enter. */}
              <button className="primary-button species-form-submit" type="button" disabled={isBusy || !isLocationsReady}
                onClick={() => { void save(); }}>
                {t(isBusy ? 'createBoxCreating' : 'speciesStrainCreateSubmit')}
              </button>
            </footer>
          </form>
        </section>
      </div>
    </ModalPortal>
  );
}
