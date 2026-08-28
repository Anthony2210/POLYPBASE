import { useDeferredValue, useEffect, useMemo, useState } from 'react';

import { AlertTriangle, ChevronLeft, ChevronRight, MapPinOff, Search } from 'lucide-react';

import { apiGet } from '../api/client';
import { getBoxStatusPresentation } from '../boxStatus';
import type { Language, Translator } from '../i18n';
import type {
  BoxActivatePayload,
  BoxDeactivatePayload,
  BoxInitialLocationPayload,
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
import SkeletonRows from './SkeletonRows';

const INVENTORY_PAGE_SIZE = 24;

type InventoryLifecycleState = {
  action: BoxLifecycleAction;
  box: BoxInventoryItem;
};

export default function BoxInventoryAdminSection({
  language,
  onAssignLocation,
  onDeactivate,
  onQualify,
  onReactivate,
  t,
  zones,
}: {
  language: Language;
  onAssignLocation: (boxId: number, payload: BoxInitialLocationPayload) => Promise<void>;
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
  const activeZones = useMemo(
    () => zones.filter((zone) => zone.is_active).sort((first, second) => (
      first.name.localeCompare(second.name, 'fr', { numeric: true, sensitivity: 'base' })
    )),
    [zones],
  );

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

      <div className="box-inventory-list" aria-busy={isLoading}>
        <div className="box-inventory-table-head" aria-hidden="true">
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
            return (
              <article
                className={isActiveWithoutLocation ? 'box-inventory-row has-location-anomaly' : 'box-inventory-row'}
                key={box.id}
              >
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
    </section>
  );
}
