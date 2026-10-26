import { useEffect, useMemo, useRef, useState } from 'react';

import type { BoxDetail, OverviewBox, OverviewMeasurementPoint } from '../types';
import { createOverviewHistory, overviewDefaultRange, scopeOverviewTemperatures, type OverviewHistoryState } from '../utils/overviewHistory';
import BiologicalTrendChart from './BiologicalTrendChart';
import ChartWindowControls from './ChartWindowControls';
import PageLoader from './PageLoader';
import PolypbaseIcon from './PolypbaseIcon';
import { useTemperatureHistory } from '../hooks/useTemperatureHistory';
import TemperatureHistoryStatus from './TemperatureHistoryStatus';
import { useHistoricalTemperatures } from '../hooks/useHistoricalTemperatures';
import { loadHistoricalTemperatures, weeklyTrendTemperatures } from '../utils/historicalTemperature';
import HistoricalTemperatureStatus from './HistoricalTemperatureStatus';
import { useTemperatureChartActivation } from '../hooks/useTemperatureChartActivation';
import { loadTemperatureHistory } from '../utils/temperatureHistory';

import { createTemperatureLoadQueue } from '../utils/temperatureLoadQueue';

type OverviewTemperatureLoaders = {
  daily: typeof loadTemperatureHistory;
  historical: typeof loadHistoricalTemperatures;
};

type Language = 'fr' | 'en';
type TFunction = (key: string) => string;

type WeeklyStatus = 'due' | 'soon' | 'ok';
type OverviewSortOrder = 'oldest' | 'newest';
type OverviewFocusFilter = 'all' | 'done' | 'due' | 'soon';
type OverviewEntry = {
  box: OverviewBox;
  latest: OverviewMeasurementPoint | undefined;
  daysSince: number | null;
  status: WeeklyStatus;
  zoneName: string;
  searchText: string;
};

type OverviewZoneSummary = {
  zoneName: string;
  total: number;
  done: number;
  due: number;
  soon: number;
};

export default function OverviewView({
  loadHistory,
  boxes,
  isLoading,
  language,
  onSelectBox,
  onOpenZone,
  t,
}: {
  loadHistory: (boxId: number) => Promise<BoxDetail>;
  boxes: OverviewBox[] | null;
  isLoading: boolean;
  language: Language;
  onSelectBox: (id: number) => void;
  onOpenZone: (zoneId: number) => void;
  t: TFunction;
}) {
  const [speciesFilter, setSpeciesFilter] = useState('');
  const [zoneFilter, setZoneFilter] = useState('');
  const [sortOrder, setSortOrder] = useState<OverviewSortOrder>('oldest');
  const [focusFilter, setFocusFilter] = useState<OverviewFocusFilter>('all');
  const [query, setQuery] = useState('');
  const [visibleCount, setVisibleCount] = useState(12);
  const temperatureLoaders = useMemo<OverviewTemperatureLoaders>(() => {
    // Share two slots across all cards and both sources, not two slots per card.
    const queue = createTemperatureLoadQueue(2);
    return {
      daily: (boxId, range, signal) => queue.run(signal, () => loadTemperatureHistory(boxId, range, signal)),
      historical: (boxId, range, signal) => queue.run(signal, () => loadHistoricalTemperatures(boxId, range, signal)),
    };
  }, []);
  const overviewBoxes = boxes ?? [];
  const noZoneLabel = t('noZone');
  const trackedEntries = useMemo<OverviewEntry[]>(
    () => overviewBoxes
      .map((box) => {
        const latest = getLastItem(box.measurements);
        const daysSince = latest ? getDaysSinceDate(latest.date) : null;
        const status = getWeeklyStatus(daysSince);
        const zoneName = box.thermal_zone?.name ?? noZoneLabel;

        return {
          box,
          latest,
          daysSince,
          status,
          zoneName,
          searchText: [box.global_code, box.species_name, box.strain_code, zoneName]
            .join(' ')
            .toLocaleLowerCase(),
        };
      }),
    [noZoneLabel, overviewBoxes],
  );
  const speciesOptions = useMemo(
    () => Array.from(new Set(trackedEntries.map((entry) => entry.box.species_name))).sort(),
    [trackedEntries],
  );
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filteredEntries = useMemo(
    () => trackedEntries.filter((entry) => {
      if (focusFilter === 'done' && entry.status !== 'ok') return false;
      if (focusFilter === 'due' && entry.status !== 'due') return false;
      if (focusFilter === 'soon' && entry.status !== 'soon') return false;
      if (speciesFilter && entry.box.species_name !== speciesFilter) return false;
      if (zoneFilter && entry.zoneName !== zoneFilter) return false;
      return !normalizedQuery || entry.searchText.includes(normalizedQuery);
    }).sort((first, second) => sortOverviewEntries(first, second, sortOrder)),
    [focusFilter, normalizedQuery, sortOrder, speciesFilter, trackedEntries, zoneFilter],
  );
  const visibleEntries = filteredEntries.slice(0, visibleCount);
  const doneCount = trackedEntries.filter((entry) => entry.status === 'ok').length;
  const dueCount = trackedEntries.filter((entry) => entry.status === 'due').length;
  const soonCount = trackedEntries.filter((entry) => entry.status === 'soon').length;
  const zoneSummaries = useMemo(() => buildOverviewZoneSummaries(trackedEntries), [trackedEntries]);
  const toggleFocusFilter = (targetFilter: Exclude<OverviewFocusFilter, 'all'>) => {
    setFocusFilter((currentFilter) => (currentFilter === targetFilter ? 'all' : targetFilter));
  };
  const toggleZoneFilter = (targetZoneName: string) => {
    setZoneFilter((currentZoneName) => (currentZoneName === targetZoneName ? '' : targetZoneName));
  };
  const hasCustomizedOverview = Boolean(
    focusFilter !== 'all'
    || speciesFilter
    || zoneFilter
    || normalizedQuery
    || sortOrder !== 'oldest',
  );
  const resetOverview = () => {
    setFocusFilter('all');
    setSpeciesFilter('');
    setZoneFilter('');
    setQuery('');
    setSortOrder('oldest');
  };

  useEffect(() => {
    setVisibleCount(12);
  }, [focusFilter, normalizedQuery, sortOrder, speciesFilter, zoneFilter]);

  if (isLoading) {
    return <PageLoader variant="overview" label={t('overviewTitle')} />;
  }

  return (
    <section className="overview-page">
      <header className="overview-intro overview-intro-priority">
        <div className="overview-summary-actions" aria-label={t('overviewFilters')}>
          <button
            type="button"
            aria-pressed={focusFilter === 'done'}
            aria-label={`${t('overviewRecordedBoxes')} : ${doneCount} ${t('boxes')}`}
            className={focusFilter === 'done' ? 'is-active is-done' : 'is-done'}
            onClick={() => toggleFocusFilter('done')}
          >
            <span>{t('overviewRecordedBoxes')}</span>
            <strong>{doneCount}</strong>
          </button>
          <button
            type="button"
            aria-pressed={focusFilter === 'soon'}
            aria-label={`${t('weeklyDueSoon')} : ${soonCount} ${t('boxes')}`}
            className={focusFilter === 'soon' ? 'is-active is-soon' : 'is-soon'}
            onClick={() => toggleFocusFilter('soon')}
          >
            <span>{t('weeklyDueSoon')}</span>
            <strong>{soonCount}</strong>
          </button>
          <button
            type="button"
            aria-pressed={focusFilter === 'due'}
            aria-label={`${t('weeklyDueNow')} : ${dueCount} ${t('boxes')}`}
            className={focusFilter === 'due' ? 'is-active is-due' : 'is-due'}
            onClick={() => toggleFocusFilter('due')}
          >
            <span>{t('weeklyDueNow')}</span>
            <strong>{dueCount}</strong>
          </button>
        </div>
      </header>

      {zoneSummaries.length ? (
        <section className="overview-zone-progress" aria-label={t('overviewByZone')}>
          <header>
            <h2>{t('overviewByZone')}</h2>
          </header>
          <div
            className="overview-zone-progress-list"
            data-layout={zoneSummaries.length > 5 ? 'many' : zoneSummaries.length}
          >
            {zoneSummaries.map((summary) => {
              const isZoneActive = zoneFilter === summary.zoneName;

              return (
                <button
                  type="button"
                  key={summary.zoneName}
                  aria-pressed={isZoneActive}
                  className={`overview-zone-progress-card ${summary.due ? 'is-due' : 'is-ok'} ${isZoneActive ? 'is-active' : ''}`}
                  onClick={() => toggleZoneFilter(summary.zoneName)}
                >
                  <span className="overview-zone-progress-copy">
                    <strong>{summary.zoneName}</strong>
                    <small>
                      {summary.due
                        ? `${summary.due} ${t('overviewZoneRemaining')}`
                        : t('overviewZoneUpToDate')}
                    </small>
                  </span>
                  <em className="overview-zone-progress-count">
                    <strong>{summary.done}</strong>
                    <span>/{summary.total}</span>
                  </em>
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      <section className="overview-filters overview-filters-priority" aria-label={t('overviewFilters')}>
        <header className="overview-filters-header">
          <div>
            <strong>{t('overviewRefineList')}</strong>
            <span>
              <b>{visibleEntries.length}</b>/{filteredEntries.length} {t('overviewShowing')}
            </span>
          </div>
          {hasCustomizedOverview ? (
            <button type="button" onClick={resetOverview}>
              <span className="button-icon-label">
                <PolypbaseIcon name="reset-filter" size={15} />
                {t('overviewResetFilters')}
              </span>
            </button>
          ) : null}
        </header>
        <div className="overview-filter-fields">
          <label>
            <span>{t('overviewSearch')}</span>
            <input
              type="search"
              placeholder={t('overviewSearchPlaceholder')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label>
            <span>{t('speciesLabel')}</span>
            <select value={speciesFilter} onChange={(event) => setSpeciesFilter(event.target.value)}>
              <option value="">{t('overviewFilterAllSpecies')}</option>
              {speciesOptions.map((speciesName) => (
                <option key={speciesName} value={speciesName}>{speciesName}</option>
              ))}
            </select>
          </label>
          <div className="overview-sort-control">
            <span>{t('overviewSort')}</span>
            <div className="overview-sort-buttons">
              <button
                type="button"
                className={sortOrder === 'oldest' ? 'is-active' : ''}
                onClick={() => setSortOrder('oldest')}
              >
                {t('overviewSortOldest')}
              </button>
              <button
                type="button"
                className={sortOrder === 'newest' ? 'is-active' : ''}
                onClick={() => setSortOrder('newest')}
              >
                {t('overviewSortNewest')}
              </button>
            </div>
          </div>
        </div>
      </section>

      {trackedEntries.length > 0 && filteredEntries.length > 0 ? (
        <div className="overview-list">
          <div className="overview-box-list">
            {visibleEntries.map((entry) => (
              <article
                className={`overview-box-summary overview-box-summary-priority is-${entry.status}`}
                key={entry.box.id}
              >
                <header>
                  <div
                    className="overview-reading-age"
                    aria-label={`${entry.daysSince == null ? t('weeklyNoRecentReading') : t('weeklyLastReading')} ${entry.daysSince ?? ''}`.trim()}
                  >
                    <strong>
                      {entry.daysSince == null
                        ? '—'
                        : `${entry.daysSince} ${t(entry.daysSince === 1 ? 'weeklyDay' : 'weeklyDays')}`}
                    </strong>
                  </div>
                  <button
                    type="button"
                    className="overview-box-identity"
                    onClick={() => onSelectBox(entry.box.id)}
                  >
                    <strong>{entry.box.global_code}</strong>
                    <span>{entry.box.species_name}</span>
                  </button>
                  <div className="overview-zone-context">
                    {entry.box.thermal_zone ? (
                      <button
                        type="button"
                        className="overview-zone-button"
                        onClick={() => onOpenZone(entry.box.thermal_zone!.id)}
                      >
                        {entry.zoneName}
                      </button>
                    ) : (
                      <small className="overview-zone-label">{entry.zoneName}</small>
                    )}
                  </div>
                </header>

                <OverviewMiniChart box={entry.box} loadHistory={loadHistory} temperatureLoaders={temperatureLoaders} language={language} t={t} />
              </article>
            ))}
          </div>
          {visibleEntries.length < filteredEntries.length ? (
            <button
              type="button"
              className="overview-show-more"
              onClick={() => setVisibleCount((count) => count + 12)}
            >
              {t('overviewShowMore')}
            </button>
          ) : null}
        </div>
      ) : (
        <p className="muted compact-text">
          {trackedEntries.length ? t('overviewEmpty') : t('weeklyNoActiveBoxes')}
        </p>
      )}
    </section>
  );
}

function buildOverviewZoneSummaries(entries: OverviewEntry[]) {
  const summaries = new Map<string, OverviewZoneSummary>();

  entries.forEach((entry) => {
    const currentSummary = summaries.get(entry.zoneName) ?? {
      zoneName: entry.zoneName,
      total: 0,
      done: 0,
      due: 0,
      soon: 0,
    };

    currentSummary.total += 1;

    if (entry.status === 'due') {
      currentSummary.due += 1;
    } else if (entry.status === 'soon') {
      currentSummary.soon += 1;
    } else {
      currentSummary.done += 1;
    }

    summaries.set(entry.zoneName, currentSummary);
  });

  return Array.from(summaries.values()).sort((first, second) => (
    getOverviewZoneTemperature(first.zoneName) - getOverviewZoneTemperature(second.zoneName)
    || first.zoneName.localeCompare(second.zoneName)
  ));
}

function getOverviewZoneTemperature(zoneName: string) {
  const temperatureMatch = zoneName.match(/-?\d+(?:[.,]\d+)?/);
  if (!temperatureMatch) return Number.POSITIVE_INFINITY;

  const temperature = Number.parseFloat(temperatureMatch[0].replace(',', '.'));
  return Number.isFinite(temperature) ? temperature : Number.POSITIVE_INFINITY;
}

function sortOverviewEntries(first: OverviewEntry, second: OverviewEntry, order: OverviewSortOrder) {
  const firstDays = first.daysSince ?? 9999;
  const secondDays = second.daysSince ?? 9999;
  const dayDiff = order === 'oldest' ? secondDays - firstDays : firstDays - secondDays;
  return dayDiff || first.box.global_code.localeCompare(second.box.global_code);
}

function getWeeklyStatus(daysSince: number | null): WeeklyStatus {
  if (daysSince === null || daysSince >= 7) return 'due';
  if (daysSince >= 5) return 'soon';
  return 'ok';
}

function getDaysSinceDate(date: string) {
  const parsedDate = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsedDate.getTime())) return null;
  const today = new Date();
  const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.max(0, Math.floor((todayStart.getTime() - parsedDate.getTime()) / 86_400_000));
}

function OverviewMiniChart({
  box,
  loadHistory,
  temperatureLoaders,
  language,
  t,
}: {
  box: OverviewBox;
  loadHistory: (boxId: number) => Promise<BoxDetail>;
  temperatureLoaders: OverviewTemperatureLoaders;
  language: Language;
  t: TFunction;
}) {
  const recentWindow = useMemo(() => overviewDefaultRange(new Date(`${box.history_end_date}T00:00:00`)), [box]);
  const earliestDate = box.earliest_biological_measurement_on;
  // The whole recorded history is the default view; the extent never shrinks below 3 months.
  const defaultWindow = useMemo(() => ({
    startDate: earliestDate && earliestDate < recentWindow.startDate ? earliestDate : recentWindow.startDate,
    endDate: recentWindow.endDate,
  }), [earliestDate, recentWindow]);
  const initialState = useMemo<OverviewHistoryState>(() => ({
    range: defaultWindow, measurements: box.measurements, locations: box.locations ?? [],
    temperatures: scopeOverviewTemperatures(box),
    complete: box.history_complete === true, loading: false, failedRange: null,
  }), [box, defaultWindow]);
  const [history, setHistory] = useState(initialState);
  const controller = useRef<ReturnType<typeof createOverviewHistory> | null>(null);
  useEffect(() => {
    setHistory(initialState);
    const current = createOverviewHistory(initialState, box.history_start_date,
      () => loadHistory(box.id), setHistory);
    controller.current = current;
    // The default range is the full history: load it at once if the payload was partial.
    void current.select(initialState.range);
    return () => { current.dispose(); controller.current = null; };
  }, [box, initialState, loadHistory]);
  const orderedMeasurements = useMemo(() => history.measurements.filter((point) => point.duplicate_of == null)
    .sort((left, right) => left.date.localeCompare(right.date)), [history.measurements]);
  const extentEnd = defaultWindow.endDate;
  const extentStart = defaultWindow.startDate;
  const { startDate, endDate } = history.range;
  const temperatureActivation = useTemperatureChartActivation(box.id);
  const temperatureBoxId = temperatureActivation.active ? box.id : undefined;
  const temperatureHistory = useTemperatureHistory(temperatureBoxId, startDate, endDate, box, temperatureLoaders.daily);
  const historicalTemperatures = useHistoricalTemperatures(temperatureBoxId, startDate, endDate, box, temperatureLoaders.historical);
  const chartTemperatures = useMemo(
    () => [...temperatureHistory.points, ...weeklyTrendTemperatures(historicalTemperatures.points)],
    [temperatureHistory.points, historicalTemperatures.points],
  );

  const locations = history.locations.map((location) => ({
    id: location.id,
    name: location.thermal_zone.name,
    startsAt: location.starts_at,
    endsAt: location.ends_at,
    endDateUnknown: location.end_date_unknown,
  }));

  return (
    <div ref={temperatureActivation.ref} className="overview-mini-chart">
      <ChartWindowControls
        compact
        notifyUnchanged
        endDate={endDate}
        extentEnd={extentEnd}
        extentStart={extentStart}
        language={language}
        onChange={(nextStart, nextEnd) => { void controller.current?.select({ startDate: nextStart, endDate: nextEnd }); }}
        startDate={startDate}
      />
      {history.loading ? <p className="sr-only" role="status">{t('overviewHistoryLoading')}</p> : null}
      {history.failedRange ? (
        <div role="alert">
          <p>{t('overviewHistoryFailed')}</p>
          <button type="button" className="secondary-button compact-button"
            onClick={() => { if (history.failedRange) void controller.current?.select(history.failedRange); }}>
            {t('overviewHistoryRetry')}
          </button>
        </div>
      ) : null}
      <BiologicalTrendChart
        compact
        startDate={startDate}
        endDate={endDate}
        measurements={orderedMeasurements.map((point) => ({
          id: point.date,
          date: point.date,
          polypCount: point.polyp_count,
          ephyraeCount: point.ephyrae_count,
          salinity: point.salinity_psu,
          environmentLocations: point.environment_locations === undefined && point.environment_source_box != null
            ? []
            : point.environment_locations?.map((location) => ({
              id: location.id, name: location.thermal_zone.name,
              startsAt: location.starts_at, endsAt: location.ends_at,
              endDateUnknown: location.end_date_unknown,
            })) ?? null,
        }))}
        locations={locations}
        temperatures={chartTemperatures}
        temperatureStatus={<>
          <TemperatureHistoryStatus {...temperatureHistory} language={language} />
          <HistoricalTemperatureStatus {...historicalTemperatures} language={language} />
        </>}
        selectionScope={box.id}
        labels={{
          chartTitle: t('overviewChartTitle'),
          closeDetail: t('close'),
          empty: t(!earliestDate && !orderedMeasurements.length ? 'overviewNoHistoryEver' : 'overviewNoHistory'),
          ephyrae: t('ephyraeFull'),
          location: language === 'fr' ? 'Emplacement' : 'Location',
          missingReading: t('chartMissingReading'),
          locationChange: t('locationPeriodChange'),
          locationChangeNote: t('locationPeriodChangeNote'),
          movement: t('movementEvent'),
          movementAtBoundaryNote: t('movementAtPeriodBoundaryNote'),
          polyps: t('polyps'),
          salinity: 'PSU',
          temperature: t('temperature'),
          dailyTemperature: t('dailyTemperatureAggregate'),
          historicalTemperature: t('temperature'),
          historicalWeek: t('historicalTemperatureWeek'),
          historicalPeriod: t('historicalTemperaturePeriod'),
          sourceLocation: t('recordedOriginalLocation'),
          temperatureOutsideScale: t('temperatureOutsideScale'),
          selectedReading: language === 'fr' ? 'Relevé sélectionné' : 'Selected reading',
        }}
      />
    </div>
  );
}

function getLastItem<T>(items: T[]): T | undefined {
  return items.length ? items[items.length - 1] : undefined;
}
