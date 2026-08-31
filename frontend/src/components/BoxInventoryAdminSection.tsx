import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';

import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleOff,
  FilterX,
  Search,
  X,
} from 'lucide-react';

import { apiGet } from '../api/client';
import { getBoxInventoryCounters } from '../api/boxInventory';
import { getBoxStatusPresentation } from '../boxStatus';
import type { Language, Translator } from '../i18n';
import type {
  BoxActivatePayload,
  BoxDeactivatePayload,
  BoxInitialLocationPayload,
  BoxInventoryBatchQualifyPayload,
  BoxInventoryBatchResult,
  BoxInventoryItem,
  BoxInventoryResponse,
  BoxQualifyPayload,
  ThermalZone,
} from '../types';
import { formatDisplayDate } from '../utils/dateFormat';
import { getErrorMessage } from '../utils/errors';
import BoxLifecycleModal, {
  type BoxLifecycleAction,
  type BoxLifecycleSubmission,
} from './BoxLifecycleModal';
import BoxInventoryBatchModal, { type BoxInventoryBatchAction } from './BoxInventoryBatchModal';
import BoxInventoryRowMenu from './BoxInventoryRowMenu';
import BoxTrackingPreview from './BoxTrackingPreview';
import SkeletonRows from './SkeletonRows';

const INVENTORY_PAGE_SIZE = 24;
// Keep the inventory shortcuts available for a future reactivation.
const SHOW_INVENTORY_SUMMARY = false;

type InventoryLifecycleState = {
  action: BoxLifecycleAction;
  box: BoxInventoryItem;
};

type InventoryBatchState = {
  action: BoxInventoryBatchAction;
  boxes: BoxInventoryItem[];
};

export default function BoxInventoryAdminSection({
  language,
  onAssignLocation,
  onBatchQualify,
  onDeactivate,
  onOpenBox,
  onOpenZone,
  onQualify,
  onReactivate,
  t,
  zones,
}: {
  language: Language;
  onAssignLocation: (boxId: number, payload: BoxInitialLocationPayload) => Promise<void>;
  onBatchQualify: (payload: BoxInventoryBatchQualifyPayload) => Promise<BoxInventoryBatchResult>;
  onDeactivate: (boxId: number, payload: BoxDeactivatePayload) => Promise<void>;
  onOpenBox: (boxId: number, code: string) => void;
  onOpenZone: (zoneId: number) => void;
  onQualify: (boxId: number, payload: BoxQualifyPayload) => Promise<void>;
  onReactivate: (boxId: number, payload: BoxActivatePayload) => Promise<void>;
  t: Translator;
  zones: ThermalZone[];
}) {
  const [response, setResponse] = useState<BoxInventoryResponse | null>(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [locationFilter, setLocationFilter] = useState('');
  const [search, setSearch] = useState('');
  const deferredSearch = useDeferredValue(search.trim());
  const [offset, setOffset] = useState(0);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [lifecycleState, setLifecycleState] = useState<InventoryLifecycleState | null>(null);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [selectedBoxes, setSelectedBoxes] = useState<Map<number, BoxInventoryItem>>(() => new Map());
  const [batchState, setBatchState] = useState<InventoryBatchState | null>(null);
  const [batchResult, setBatchResult] = useState<BoxInventoryBatchResult | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [isBatchSaving, setIsBatchSaving] = useState(false);
  const pageSelectionRef = useRef<HTMLInputElement>(null);
  const activeZones = useMemo(
    () => zones.filter((zone) => zone.is_active).sort((first, second) => (
      first.name.localeCompare(second.name, 'fr', { numeric: true, sensitivity: 'base' })
    )),
    [zones],
  );
  const pendingBoxesOnPage = useMemo(
    () => response?.results.filter((box) => box.status === 'pending_review') ?? [],
    [response],
  );
  const selectedOnPageCount = pendingBoxesOnPage.filter((box) => selectedBoxes.has(box.id)).length;
  const allPendingOnPageAreSelected = pendingBoxesOnPage.length > 0
    && selectedOnPageCount === pendingBoxesOnPage.length;

  useEffect(() => {
    if (!pageSelectionRef.current) return;
    pageSelectionRef.current.indeterminate = selectedOnPageCount > 0 && !allPendingOnPageAreSelected;
  }, [allPendingOnPageAreSelected, selectedOnPageCount]);

  useEffect(() => {
    let isCurrent = true;
    const params = new URLSearchParams({
      limit: String(INVENTORY_PAGE_SIZE),
      offset: String(offset),
    });
    if (statusFilter) params.set('status', statusFilter);
    if (locationFilter) params.set('location', locationFilter);
    if (deferredSearch) params.set('q', deferredSearch);

    setIsLoading(true);
    setLoadError(null);
    void apiGet<BoxInventoryResponse>(`/api/admin/box-inventory/?${params.toString()}`)
      .then(async (nextResponse) => {
        if (!isCurrent) return;
        if (offset > 0 && nextResponse.results.length === 0 && nextResponse.count > 0) {
          const lastOffset = Math.floor((nextResponse.count - 1) / INVENTORY_PAGE_SIZE) * INVENTORY_PAGE_SIZE;
          setOffset(lastOffset);
          return;
        }
        const counters = await getBoxInventoryCounters(nextResponse);
        if (!isCurrent) return;
        setResponse({ ...nextResponse, summary: { ...nextResponse.summary, ...counters } });
      })
      .catch((requestError) => {
        if (isCurrent) setLoadError(getErrorMessage(requestError));
      })
      .finally(() => {
        if (isCurrent) setIsLoading(false);
      });

    return () => {
      isCurrent = false;
    };
  }, [deferredSearch, locationFilter, offset, refreshVersion, statusFilter]);

  function updateStatusFilter(value: string) {
    setStatusFilter(value);
    setOffset(0);
    setMessage(null);
  }

  function updateLocationFilter(value: string) {
    setLocationFilter(value);
    setOffset(0);
    setMessage(null);
  }

  function applySummaryFilter(status: string, location: string) {
    setStatusFilter(status);
    setLocationFilter(location);
    setSearch('');
    setOffset(0);
    setMessage(null);
  }

  function openLifecycleAction(action: BoxLifecycleAction, box: BoxInventoryItem) {
    setLifecycleError(null);
    setMessage(null);
    setLifecycleState({ action, box });
  }

  function toggleBoxSelection(box: BoxInventoryItem, checked: boolean) {
    if (box.status !== 'pending_review') return;
    setSelectedBoxes((current) => {
      const next = new Map(current);
      if (checked) next.set(box.id, box);
      else next.delete(box.id);
      return next;
    });
    setMessage(null);
  }

  function togglePageSelection(checked: boolean) {
    setSelectedBoxes((current) => {
      const next = new Map(current);
      pendingBoxesOnPage.forEach((box) => {
        if (checked) next.set(box.id, box);
        else next.delete(box.id);
      });
      return next;
    });
    setMessage(null);
  }

  function openBatchAction(action: BoxInventoryBatchAction) {
    const boxes = Array.from(selectedBoxes.values());
    if (!boxes.length) return;
    setBatchError(null);
    setBatchResult(null);
    setBatchState({ action, boxes });
  }

  async function handleBatchConfirm() {
    if (!batchState || isBatchSaving || batchResult) return;
    setIsBatchSaving(true);
    setBatchError(null);

    try {
      const result = await onBatchQualify({
        box_ids: batchState.boxes.map((box) => box.id),
        target_status: batchState.action,
        ...(batchState.action === 'inactive'
          ? { reason_missing_from_history: true }
          : {}),
      });
      setBatchResult(result);
      setSelectedBoxes(new Map());
      setRefreshVersion((current) => current + 1);
      setMessage(t('boxInventoryBatchInventoryRefreshed'));
    } catch (requestError) {
      setBatchError(getErrorMessage(requestError));
    } finally {
      setIsBatchSaving(false);
    }
  }

  async function handleLifecycleSubmit(submission: BoxLifecycleSubmission) {
    if (!lifecycleState || isSaving) return;
    setIsSaving(true);
    setLifecycleError(null);

    try {
      if (submission.action === 'qualify') {
        await onQualify(lifecycleState.box.id, submission.payload);
      } else if (submission.action === 'deactivate') {
        await onDeactivate(lifecycleState.box.id, submission.payload);
      } else if (submission.action === 'reactivate') {
        await onReactivate(lifecycleState.box.id, submission.payload);
      } else {
        await onAssignLocation(lifecycleState.box.id, submission.payload);
      }
      setSelectedBoxes((current) => {
        if (!current.has(lifecycleState.box.id)) return current;
        const next = new Map(current);
        next.delete(lifecycleState.box.id);
        return next;
      });
      setLifecycleState(null);
      setMessage(t('boxInventoryActionSaved'));
      setRefreshVersion((current) => current + 1);
    } catch (requestError) {
      setLifecycleError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  const totalCount = response?.count ?? 0;
  const startResult = totalCount ? offset + 1 : 0;
  const endResult = Math.min(offset + (response?.results.length ?? 0), totalCount);
  const currentPage = Math.floor(offset / INVENTORY_PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(totalCount / INVENTORY_PAGE_SIZE));
  const hasFilters = Boolean(statusFilter || locationFilter || search);
  const summaryCards = [
    { key: 'pending_review_count', label: 'boxInventoryStatusPending', status: 'pending_review', location: '', tone: 'is-soon' },
    { key: 'active_without_location_count', label: 'boxInventoryActiveWithoutLocation', status: 'active', location: 'none', tone: 'is-unlocated' },
  ] as const;

  return (
    <section className="admin-section box-inventory-section" id="admin-inventory">
      <header className="box-inventory-heading">
        <div>
          <h2>{t('boxInventoryTitle')}</h2>
        </div>
        <strong>{totalCount} {t('boxInventoryBoxes')}</strong>
      </header>

      {SHOW_INVENTORY_SUMMARY ? (
        <div className="box-inventory-summary" role="group" aria-label={t('boxInventorySummary')} aria-busy={isLoading}>
          {summaryCards.map((card) => {
            const isActive = statusFilter === card.status && locationFilter === card.location && !search.trim();
            return (
              <button
                key={card.key}
                type="button"
                className={`${card.tone}${isActive ? ' is-active' : ''}`}
                aria-pressed={isActive}
                onClick={() => applySummaryFilter(card.status, card.location)}
              >
                <span>{t(card.label)}</span>
                <strong>{response?.summary?.[card.key] ?? (isLoading ? (
                  <span className="loader-block box-inventory-count-loading" aria-label={t('loading')} />
                ) : null)}</strong>
              </button>
            );
          })}
          <button type="button" disabled className="is-unavailable">
            <span>{t('boxInventorySuspectedInactive')}</span>
            <small>{t('adminWorkInProgress')}</small>
          </button>
        </div>
      ) : null}

      <div className="box-inventory-filters">
        <label className="box-inventory-search">
          <span>{t('boxInventorySearch')}</span>
          <span className="box-inventory-search-control">
            <Search aria-hidden="true" size={17} />
            <input
              type="search"
              value={search}
              placeholder={t('boxInventorySearchPlaceholder')}
              onChange={(event) => {
                setSearch(event.target.value);
                setOffset(0);
              }}
            />
          </span>
        </label>

        <label className="box-inventory-status-filter">
          <span>{t('boxInventoryStatus')}</span>
          <select value={statusFilter} onChange={(event) => updateStatusFilter(event.target.value)}>
            <option value="">{t('boxInventoryStatusAll')}</option>
            <option value="pending_review">{t('boxInventoryStatusPending')}</option>
            <option value="active">{t('boxInventoryStatusActive')}</option>
            <option value="inactive">{t('boxInventoryStatusInactive')}</option>
          </select>
        </label>

        <label className="box-inventory-location-filter">
          <span>{t('boxInventoryLocation')}</span>
          <select value={locationFilter} onChange={(event) => updateLocationFilter(event.target.value)}>
            <option value="">{t('boxInventoryLocationAll')}</option>
            <option value="none">{t('boxInventoryWithoutLocation')}</option>
            {activeZones.map((zone) => (
              <option key={zone.id} value={zone.id}>{zone.name}</option>
            ))}
          </select>
        </label>
        <div className="box-inventory-reset-slot">
          {hasFilters ? (
            <button
              type="button"
              aria-label={t('overviewResetFilters')}
              title={t('overviewResetFilters')}
              onClick={() => applySummaryFilter('', '')}
            >
              <FilterX size={17} aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </div>

      {message ? <p className="inline-success box-inventory-feedback">{message}</p> : null}
      {loadError ? <p className="inline-error box-inventory-feedback">{loadError}</p> : null}

      {pendingBoxesOnPage.length > 0 || selectedBoxes.size > 0 ? (
        <aside className="box-inventory-selection-toolbar" aria-label={t('boxInventoryBatchSelectionTitle')}>
          {pendingBoxesOnPage.length > 0 ? (
            <label className="box-inventory-page-selection">
              <input
                ref={pageSelectionRef}
                type="checkbox"
                checked={allPendingOnPageAreSelected}
                disabled={isLoading}
                onChange={(event) => togglePageSelection(event.target.checked)}
              />
              <span>
                {t('boxInventorySelectVisiblePage')}
                <small>{pendingBoxesOnPage.length} {t('boxInventoryBatchPendingOnPage')}</small>
              </span>
            </label>
          ) : null}
          <div className="box-inventory-selection-summary">
            <span aria-live="polite">
              <strong>{selectedBoxes.size}</strong>
              {t(selectedBoxes.size === 1 ? 'boxInventoryBatchSelectedOne' : 'boxInventoryBatchSelected')}
            </span>
            {selectedBoxes.size > 0 ? (
              <button
                type="button"
                aria-label={t('boxInventoryBatchClearSelection')}
                title={t('boxInventoryBatchClearSelection')}
                onClick={() => setSelectedBoxes(new Map())}
              >
                <X aria-hidden="true" size={17} />
              </button>
            ) : null}
          </div>
          <div className="box-inventory-batch-actions">
            <button type="button" className="is-active-action" disabled={!selectedBoxes.size} onClick={() => openBatchAction('active')}>
              <CheckCircle2 aria-hidden="true" size={17} />
              {t('boxInventoryBatchMakeActive')}
            </button>
            <button type="button" className="is-inactive-action" disabled={!selectedBoxes.size} onClick={() => openBatchAction('inactive')}>
              <CircleOff aria-hidden="true" size={17} />
              {t('boxInventoryBatchMakeInactive')}
            </button>
          </div>
          {selectedBoxes.size > 0 ? (
            <ul className="box-inventory-selected-boxes" aria-label={t('boxInventoryBatchAffectedBoxes')}>
              {Array.from(selectedBoxes.values()).map((box) => (
                <li key={box.id}>{box.global_code}</li>
              ))}
            </ul>
          ) : null}
        </aside>
      ) : null}

      <div className={`box-inventory-list${pendingBoxesOnPage.length ? ' has-selection-column' : ''}`} aria-busy={isLoading}>
        <div className="box-inventory-table-head" aria-hidden="true">
          {pendingBoxesOnPage.length ? <span /> : null}
          <span>{t('boxInventoryBox')}</span>
          <span>{t('boxInventoryLocation')}</span>
          <span>{t('boxInventoryDates')}</span>
          <span>{t('boxInventoryLatestCounts')}</span>
          <span />
        </div>

        {isLoading && !response ? (
          <div className="box-inventory-loading"><SkeletonRows count={8} /></div>
        ) : response?.results.length ? (
          response.results.map((box) => {
            const status = getBoxStatusPresentation(box.status, language);
            const isActiveWithoutLocation = box.status === 'active' && box.thermal_zone == null;
            const currentZone = box.status === 'inactive' ? null : box.thermal_zone;
            const measurement = box.latest_measurement;
            const isSelected = selectedBoxes.has(box.id);
            return (
              <article
                className={[
                  'box-inventory-row',
                  `is-${status.tone}`,
                  isSelected ? 'is-selected' : '',
                ].filter(Boolean).join(' ')}
                key={box.id}
                aria-label={`${box.global_code}, ${status.label}`}
              >
                {box.status !== 'active' ? <span className="box-inventory-row-status">{box.status === 'pending_review' ? t('boxInventoryReviewMarker') : status.label}</span> : null}
                {pendingBoxesOnPage.length ? (
                <div className="box-inventory-cell box-inventory-selection-cell" data-label={t('boxInventoryBatchSelectionColumn')}>
                  {box.status === 'pending_review' ? (
                    <label className="box-inventory-row-selection">
                      <input
                        type="checkbox"
                        disabled={isLoading}
                        checked={isSelected}
                        aria-label={`${t('boxInventoryBatchSelectBox')} ${box.global_code}`}
                        onChange={(event) => toggleBoxSelection(box, event.target.checked)}
                      />
                    </label>
                  ) : null}
                </div>
                ) : null}
                <div className="box-inventory-cell box-inventory-identity" data-label={t('boxInventoryBox')}>
                  <BoxTrackingPreview boxId={box.id} code={box.global_code} speciesName={box.species.scientific_name} language={language} onOpenBox={onOpenBox} t={t} />
                  <span className="box-inventory-species">{box.species.scientific_name}</span>
                </div>
                <div className="box-inventory-cell box-inventory-location" data-label={t('boxInventoryLocation')}>
                  {currentZone ? (
                    <a
                      href={`/zones/${currentZone.id}`}
                      onClick={(event) => {
                        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                        event.preventDefault();
                        onOpenZone(currentZone.id);
                      }}
                    >{currentZone.name}</a>
                  ) : isActiveWithoutLocation ? (
                    <button className="box-inventory-assign" type="button" disabled={isLoading} onClick={() => openLifecycleAction('assign', box)}>
                      {t('boxInventoryAssignLocation')}
                    </button>
                  ) : <span className="is-muted">{t('boxInventoryWithoutLocation')}</span>}
                </div>
                <div className="box-inventory-cell box-inventory-dates" data-label={t('boxInventoryDates')}>
                  <span>
                    <small>{t('boxInventoryCreatedOn')}</small>
                    <strong>{formatDisplayDate(box.created_on)}</strong>
                  </span>
                  <span>
                    <small>{t('boxInventoryLastMeasurement')}</small>
                    <strong>{measurement ? formatDisplayDate(measurement.measured_on) : t('boxInventoryNoData')}</strong>
                  </span>
                </div>
                <div className="box-inventory-cell box-inventory-counts" data-label={t('boxInventoryLatestCounts')}>
                  {measurement ? (
                    <>
                      <span><strong>{measurement.polyp_count}</strong><small>{t('polyps')}</small></span>
                      <span><strong>{measurement.ephyrae_count}</strong><small>{t('ephyraeFull')}</small></span>
                    </>
                  ) : (
                    <span className="box-inventory-no-measurement">{t('boxInventoryNoMeasurement')}</span>
                  )}
                </div>
                <div className="box-inventory-cell box-inventory-actions" data-label={t('boxInventoryActions')}>
                  <BoxInventoryRowMenu box={box} disabled={isLoading} onAction={(action) => openLifecycleAction(action, box)} t={t} />
                </div>
              </article>
            );
          })
        ) : !isLoading ? (
          <div className="box-inventory-empty">
            <strong>{t('boxInventoryEmptyTitle')}</strong>
            <p>{t('boxInventoryEmptyText')}</p>
          </div>
        ) : null}
      </div>

      {response && totalCount > 0 ? (
        <nav className="box-inventory-pagination" aria-label={t('adminPaginationLabel')}>
          <span>{startResult}-{endResult} {t('boxInventoryOf')} {totalCount}</span>
          <div>
            <button
              type="button"
              aria-label={t('adminPreviousPage')}
              title={t('adminPreviousPage')}
              disabled={!response.previous || isLoading}
              onClick={() => setOffset(Math.max(0, offset - INVENTORY_PAGE_SIZE))}
            >
              <ChevronLeft aria-hidden="true" size={18} />
            </button>
            <strong>{t('adminPage')} {currentPage} / {totalPages}</strong>
            <button
              type="button"
              aria-label={t('adminNextPage')}
              title={t('adminNextPage')}
              disabled={!response.next || isLoading}
              onClick={() => setOffset(offset + INVENTORY_PAGE_SIZE)}
            >
              <ChevronRight aria-hidden="true" size={18} />
            </button>
          </div>
        </nav>
      ) : null}

      {lifecycleState ? (
        <BoxLifecycleModal
          action={lifecycleState.action}
          box={lifecycleState.box}
          error={lifecycleError}
          isSaving={isSaving}
          onClose={() => {
            if (!isSaving) setLifecycleState(null);
          }}
          onSubmit={handleLifecycleSubmit}
          t={t}
          zones={activeZones}
        />
      ) : null}

      {batchState ? (
        <BoxInventoryBatchModal
          action={batchState.action}
          error={batchError}
          isSaving={isBatchSaving}
          onClose={() => {
            if (!isBatchSaving) {
              setBatchState(null);
              setBatchResult(null);
              setBatchError(null);
            }
          }}
          onConfirm={handleBatchConfirm}
          result={batchResult}
          selectedBoxes={batchState.boxes}
          t={t}
        />
      ) : null}
    </section>
  );
}
