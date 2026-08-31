import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';

import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleOff,
  MapPinOff,
  Search,
  X,
} from 'lucide-react';

import { apiGet } from '../api/client';
import { getBoxStatusPresentation } from '../boxStatus';
import type { Language, Translator } from '../i18n';
import type {
  BoxActivatePayload,
  BoxDeactivatePayload,
  BoxInitialLocationPayload,
  BoxInventoryBatchQualifyPayload,
  BoxInventoryBatchResult,
  BoxInventoryItem,
  BoxQualifyPayload,
  PaginatedResponse,
  ThermalZone,
} from '../types';
import { formatDisplayDate } from '../utils/dateFormat';
import { getErrorMessage } from '../utils/errors';
import BoxLifecycleModal, {
  type BoxLifecycleAction,
  type BoxLifecycleSubmission,
} from './BoxLifecycleModal';
import BoxInventoryBatchModal, { type BoxInventoryBatchAction } from './BoxInventoryBatchModal';
import SkeletonRows from './SkeletonRows';

const INVENTORY_PAGE_SIZE = 24;

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
  onQualify,
  onReactivate,
  t,
  zones,
}: {
  language: Language;
  onAssignLocation: (boxId: number, payload: BoxInitialLocationPayload) => Promise<void>;
  onBatchQualify: (payload: BoxInventoryBatchQualifyPayload) => Promise<BoxInventoryBatchResult>;
  onDeactivate: (boxId: number, payload: BoxDeactivatePayload) => Promise<void>;
  onQualify: (boxId: number, payload: BoxQualifyPayload) => Promise<void>;
  onReactivate: (boxId: number, payload: BoxActivatePayload) => Promise<void>;
  t: Translator;
  zones: ThermalZone[];
}) {
  const [response, setResponse] = useState<PaginatedResponse<BoxInventoryItem> | null>(null);
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
    void apiGet<PaginatedResponse<BoxInventoryItem>>(`/api/admin/box-inventory/?${params.toString()}`)
      .then((nextResponse) => {
        if (!isCurrent) return;
        if (offset > 0 && nextResponse.results.length === 0 && nextResponse.count > 0) {
          const lastOffset = Math.floor((nextResponse.count - 1) / INVENTORY_PAGE_SIZE) * INVENTORY_PAGE_SIZE;
          setOffset(lastOffset);
          return;
        }
        setResponse(nextResponse);
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

  function showActiveWithoutLocation() {
    setStatusFilter('active');
    setLocationFilter('none');
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
  const shortcutIsActive = statusFilter === 'active' && locationFilter === 'none';

  return (
    <section className="admin-section box-inventory-section" id="admin-inventory">
      <header className="box-inventory-heading">
        <div>
          <h2>{t('boxInventoryTitle')}</h2>
          <p>{t('boxInventorySubtitle')}</p>
        </div>
        <strong>{totalCount} {t('boxInventoryBoxes')}</strong>
      </header>

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

        <fieldset className="box-inventory-status-filter">
          <legend>{t('boxInventoryStatus')}</legend>
          {[
            ['', 'boxInventoryStatusAll'],
            ['pending_review', 'boxInventoryStatusPending'],
            ['active', 'boxInventoryStatusActive'],
            ['inactive', 'boxInventoryStatusInactive'],
          ].map(([value, key]) => (
            <button
              type="button"
              className={statusFilter === value ? 'is-active' : ''}
              aria-pressed={statusFilter === value}
              key={value || 'all'}
              onClick={() => updateStatusFilter(value)}
            >
              {t(key as Parameters<Translator>[0])}
            </button>
          ))}
        </fieldset>

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

        <button
          type="button"
          className={shortcutIsActive ? 'box-inventory-anomaly-shortcut is-active' : 'box-inventory-anomaly-shortcut'}
          aria-pressed={shortcutIsActive}
          onClick={showActiveWithoutLocation}
        >
          <MapPinOff aria-hidden="true" size={18} />
          <span>{t('boxInventoryActiveWithoutLocation')}</span>
        </button>
      </div>

      {message ? <p className="inline-success box-inventory-feedback">{message}</p> : null}
      {loadError ? <p className="inline-error box-inventory-feedback">{loadError}</p> : null}

      {selectedBoxes.size > 0 ? (
        <aside className="box-inventory-selection-toolbar" aria-label={t('boxInventoryBatchSelectionTitle')}>
          <div className="box-inventory-selection-summary">
            <span>
              <strong>{selectedBoxes.size}</strong>
              {t(selectedBoxes.size === 1 ? 'boxInventoryBatchSelectedOne' : 'boxInventoryBatchSelected')}
            </span>
            <button
              type="button"
              aria-label={t('boxInventoryBatchClearSelection')}
              title={t('boxInventoryBatchClearSelection')}
              onClick={() => setSelectedBoxes(new Map())}
            >
              <X aria-hidden="true" size={17} />
            </button>
          </div>
          <ul className="box-inventory-selected-boxes">
            {Array.from(selectedBoxes.values()).map((box) => (
              <li key={box.id}>{box.global_code}</li>
            ))}
          </ul>
          <div className="box-inventory-batch-actions">
            <button type="button" className="is-active-action" onClick={() => openBatchAction('active')}>
              <CheckCircle2 aria-hidden="true" size={17} />
              {t('boxInventoryBatchMakeActive')}
            </button>
            <button type="button" className="is-inactive-action" onClick={() => openBatchAction('inactive')}>
              <CircleOff aria-hidden="true" size={17} />
              {t('boxInventoryBatchMakeInactive')}
            </button>
          </div>
        </aside>
      ) : null}

      {pendingBoxesOnPage.length ? (
        <label className="box-inventory-page-selection">
          <input
            ref={pageSelectionRef}
            type="checkbox"
            checked={allPendingOnPageAreSelected}
            onChange={(event) => togglePageSelection(event.target.checked)}
          />
          <span>
            {t('boxInventoryBatchSelectPage')}
            <small>{pendingBoxesOnPage.length} {t('boxInventoryBatchPendingOnPage')}</small>
          </span>
        </label>
      ) : null}

      <div className="box-inventory-list" aria-busy={isLoading}>
        <div className="box-inventory-table-head" aria-hidden="true">
          <span>{t('boxInventoryBatchSelectionColumn')}</span>
          <span>{t('boxInventoryBox')}</span>
          <span>{t('boxInventorySpecies')}</span>
          <span>{t('boxInventoryStatus')}</span>
          <span>{t('boxInventoryLocation')}</span>
          <span>{t('boxInventoryDates')}</span>
          <span>{t('boxInventoryLatestCounts')}</span>
          <span>{t('boxInventoryActions')}</span>
        </div>

        {isLoading && !response ? (
          <div className="box-inventory-loading"><SkeletonRows count={8} /></div>
        ) : response?.results.length ? (
          response.results.map((box) => {
            const status = getBoxStatusPresentation(box.status, language);
            const isActiveWithoutLocation = box.status === 'active' && box.thermal_zone == null;
            const measurement = box.latest_measurement;
            const isSelected = selectedBoxes.has(box.id);
            return (
              <article
                className={[
                  'box-inventory-row',
                  isActiveWithoutLocation ? 'has-location-anomaly' : '',
                  isSelected ? 'is-selected' : '',
                ].filter(Boolean).join(' ')}
                key={box.id}
              >
                <div className="box-inventory-cell box-inventory-selection-cell" data-label={t('boxInventoryBatchSelectionColumn')}>
                  {box.status === 'pending_review' ? (
                    <input
                      type="checkbox"
                      checked={isSelected}
                      aria-label={`${t('boxInventoryBatchSelectBox')} ${box.global_code}`}
                      onChange={(event) => toggleBoxSelection(box, event.target.checked)}
                    />
                  ) : null}
                </div>
                <div className="box-inventory-cell box-inventory-identity" data-label={t('boxInventoryBox')}>
                  <strong>{box.global_code}</strong>
                  {box.local_code ? <small>{box.local_code}</small> : null}
                </div>
                <div className="box-inventory-cell box-inventory-species" data-label={t('boxInventorySpecies')}>
                  <span>{box.species.scientific_name}</span>
                </div>
                <div className="box-inventory-cell" data-label={t('boxInventoryStatus')}>
                  <span className={`box-inventory-status is-${status.tone}`}>{status.label}</span>
                </div>
                <div className="box-inventory-cell box-inventory-location" data-label={t('boxInventoryLocation')}>
                  {box.thermal_zone ? (
                    <strong>{box.thermal_zone.name}</strong>
                  ) : (
                    <span className={isActiveWithoutLocation ? 'is-anomaly' : 'is-muted'}>
                      {isActiveWithoutLocation ? <AlertTriangle aria-hidden="true" size={16} /> : null}
                      {t('boxInventoryWithoutLocation')}
                    </span>
                  )}
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
                  {box.status === 'pending_review' ? (
                    <button type="button" onClick={() => openLifecycleAction('qualify', box)}>
                      {t('boxInventoryQualify')}
                    </button>
                  ) : null}
                  {isActiveWithoutLocation ? (
                    <button className="is-primary" type="button" onClick={() => openLifecycleAction('assign', box)}>
                      {t('boxInventoryAssignLocation')}
                    </button>
                  ) : null}
                  {box.status === 'active' ? (
                    <button type="button" onClick={() => openLifecycleAction('deactivate', box)}>
                      {t('boxInventoryDeactivate')}
                    </button>
                  ) : null}
                  {box.status === 'inactive' ? (
                    <button className="is-primary" type="button" onClick={() => openLifecycleAction('reactivate', box)}>
                      {t('boxInventoryReactivate')}
                    </button>
                  ) : null}
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
