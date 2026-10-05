import {
  lazy,
  Suspense,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from 'react';

import type {
  BiologicalMeasurement,
  BoxLocation,
  BoxLineage,
  BoxMovement,
  LineageGraph,
} from '../types';
import { createTranslator } from '../i18n';
import { getBiologicalTimelineLabels, type BiologicalTimelineEntry } from '../utils/biologicalTimeline';
import { formatDisplayDate, formatDisplayDateTime } from '../utils/dateFormat';
import PolypbaseIcon from './PolypbaseIcon';
import BoxTrackingChart, { buildLifecycleEvents } from './BoxTrackingChart';
import ModalPortal from './ModalPortal';

const InteractiveLineageGraph = lazy(() => import('./InteractiveLineageGraph'));

export type BoxInsightTab = 'measurements' | 'movements' | 'lineage';

type Language = 'fr' | 'en';

type BoxInsightsLabels = {
  chartEmpty: string;
  chartTitle: string;
  children: string;
  close: string;
  ephyraeFull: string;
  events: string;
  historyButton: string;
  historyAllYears: string;
  historyVisibleCount: (visible: number, total: number) => string;
  historyYearFilter: string;
  historyEnteredBy: string;
  historyHideComment: string;
  historyObservation: string;
  historyReadComment: string;
  historyShowMore: string;
  historyYear: string;
  lineageEmptyGraph: string;
  lineageLoading: string;
  lineageRetry: string;
  lineageTab: string;
  measurementHistory: string;
  measurementsTab: string;
  missingReading: string;
  missingReadingRange: string;
  movementEvent: string;
  movementHistoryTitle: string;
  movementsTab: string;
  movedTo: string;
  noComment: string;
  noMeasurementHistory: string;
  noMovementHistory: string;
  oneMonth: string;
  oneYear: string;
  parents: string;
  polyps: string;
  salinityFull: string;
  sixMonths: string;
  subcultureEvent: string;
  temperature: string;
  temperatureNoData: string;
  threeMonths: string;
};

export default function BoxInsights({
  activeTab,
  biologicalTimeline,
  graph,
  graphError,
  isGraphLoading,
  labels,
  language,
  lineage,
  measurements,
  movements,
  locations,
  onLoadLineageGraph,
  onOpenHistory,
  onSelectBox,
  onSelectTab,
}: {
  activeTab: BoxInsightTab;
  biologicalTimeline?: readonly BiologicalTimelineEntry[];
  graph: LineageGraph | null;
  graphError: string | null;
  isGraphLoading: boolean;
  labels: BoxInsightsLabels;
  language: Language;
  lineage: BoxLineage;
  measurements: BiologicalMeasurement[];
  movements: BoxMovement[];
  locations: BoxLocation[];
  onLoadLineageGraph: () => void;
  onOpenHistory: () => void;
  onSelectBox: (boxId: number, globalCode: string) => void;
  onSelectTab: (tab: BoxInsightTab) => void;
}) {
  const tabs: Array<{ id: BoxInsightTab; label: string }> = [
    { id: 'measurements', label: labels.measurementsTab },
    { id: 'movements', label: labels.movementsTab },
    { id: 'lineage', label: labels.lineageTab },
  ];

  const lifecycleEvents = useMemo(
    () => buildLifecycleEvents(lineage, movements, labels),
    [lineage, movements, labels],
  );
  const insightPanelRef = useRef<HTMLDivElement>(null);
  const [reservedPanelHeight, setReservedPanelHeight] = useState(0);
  const insightPanelStyle = reservedPanelHeight ? { minHeight: reservedPanelHeight } : undefined;

  function selectTab(tab: BoxInsightTab) {
    // All tabs share the analytical height, but long histories must not inflate the charts.
    if (activeTab !== 'movements') {
      const currentHeight = insightPanelRef.current?.getBoundingClientRect().height ?? 0;
      setReservedPanelHeight((height) => Math.max(height, Math.ceil(currentHeight)));
    }
    onSelectTab(tab);
  }

  return (
    <div className="box-insights">
      <div className="insight-tabs" role="tablist" aria-label={labels.chartTitle}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? 'is-active' : ''}
            role="tab"
            type="button"
            aria-selected={activeTab === tab.id}
            onClick={() => selectTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'measurements' ? (
        <div ref={insightPanelRef} className="insight-panel" style={insightPanelStyle}>
          <BoxTrackingChart
            biologicalTimeline={biologicalTimeline}
            events={lifecycleEvents}
            labels={labels}
            language={language}
            locations={locations}
            measurements={measurements}
            onOpenHistory={onOpenHistory}
          />
        </div>
      ) : null}

      {activeTab === 'movements' ? (
        <div className="insight-panel insight-panel--movements" style={insightPanelStyle}>
          <div className="insight-heading">
            <h2>{labels.movementHistoryTitle}</h2>
          </div>
          <MovementTimeline movements={movements} labels={labels} />
        </div>
      ) : null}

      {activeTab === 'lineage' ? (
        <div ref={insightPanelRef} className="insight-panel" style={insightPanelStyle}>
          <div className="insight-heading">
            <h2>{labels.lineageTab}</h2>
          </div>
          {isGraphLoading ? <p className="lineage-inline-status">{labels.lineageLoading}</p> : null}
          {graphError ? (
            <div className="lineage-inline-status is-error">
              <p>{graphError}</p>
              <button type="button" onClick={onLoadLineageGraph}>{labels.lineageRetry}</button>
            </div>
          ) : null}
          {graph ? (
            <Suspense fallback={<p className="lineage-inline-status">{labels.lineageLoading}</p>}>
              <InteractiveLineageGraph
                graph={graph}
                language={language}
                onSelectBox={onSelectBox}
              />
            </Suspense>
          ) : null}
          {!graph && !isGraphLoading && !graphError ? (
            <div className="lineage-preview">
              <Metric label={labels.parents} value={String(lineage.parents.length)} />
              <Metric label={labels.children} value={String(lineage.children.length)} />
              <p>{labels.lineageEmptyGraph}</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function MovementTimeline({
  labels,
  movements,
}: {
  labels: BoxInsightsLabels;
  movements: BoxMovement[];
}) {
  const sortedMovements = [...movements]
    .sort((left, right) => right.moved_at.localeCompare(left.moved_at));

  if (!sortedMovements.length) {
    return <p className="muted compact-text movement-empty">{labels.noMovementHistory}</p>;
  }

  return (
    <ol className="movement-timeline" role="list">
      {sortedMovements.map((movement) => (
        <li key={movement.id}>
          <time dateTime={movement.moved_at}>{formatDisplayDateTime(movement.moved_at)}</time>
          <div className="movement-detail">
            <span className="movement-event">{labels.movementEvent}</span>
            <strong className="movement-locations">
              {movement.from_thermal_zone ? (
                <>
                  <span>{movement.from_thermal_zone.name}</span>
                  {' '}
                  <span className="movement-arrow">→</span>
                  {' '}
                  <span>{movement.to_thermal_zone.name}</span>
                </>
              ) : (
                <>
                  <span className="movement-destination-label">{labels.movedTo}</span>
                  {' '}
                  <span>{movement.to_thermal_zone.name}</span>
                </>
              )}
            </strong>
            {movement.user ? <small>{movement.user}</small> : null}
            {movement.notes ? <p>{movement.notes}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function MeasurementHistoryModal({
  biologicalTimeline,
  boxCode,
  labels,
  language = 'fr',
  measurements,
  onClose,
}: {
  biologicalTimeline?: readonly BiologicalTimelineEntry[];
  boxCode: string;
  labels: BoxInsightsLabels;
  language?: Language;
  measurements: BiologicalMeasurement[];
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [selectedYear, setSelectedYear] = useState('all');
  const [visibleCount, setVisibleCount] = useState(24);
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(() => new Set());

  const sortedEntries = useMemo(
    () => buildHistoryEntries(measurements, biologicalTimeline),
    [measurements, biologicalTimeline],
  );
  const availableYears = useMemo(
    () => Array.from(new Set<string>(sortedEntries.map((entry) => entry.effective_date.slice(0, 4))))
      .filter(Boolean)
      .sort((left, right) => right.localeCompare(left)),
    [sortedEntries],
  );
  const filteredEntries = useMemo(
    () => selectedYear === 'all'
      ? sortedEntries
      : sortedEntries.filter((entry) => entry.effective_date.startsWith(selectedYear)),
    [selectedYear, sortedEntries],
  );
  const visibleEntries = filteredEntries.slice(0, visibleCount);
  const remainingCount = Math.max(0, filteredEntries.length - visibleEntries.length);

  useLayoutEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const backdrop = dialogRef.current?.parentElement;
    const background = Array.from(document.body.children)
      .filter((element): element is HTMLElement => element instanceof HTMLElement && element !== backdrop);
    const previousInert = background.map((element) => element.inert);
    background.forEach((element) => { element.inert = true; });
    titleRef.current?.focus({ preventScroll: true });

    return () => {
      background.forEach((element, index) => { element.inert = previousInert[index]; });
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => element.getClientRects().length > 0);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!first || !last) {
        event.preventDefault();
        titleRef.current?.focus();
      } else if (event.shiftKey && (active === first || active === titleRef.current || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  function selectYear(year: string) {
    setSelectedYear(year);
    setVisibleCount(24);
    if (listRef.current) listRef.current.scrollTop = 0;
  }

  function toggleNote(identity: string) {
    setExpandedNotes((current) => {
      const next = new Set(current);
      if (next.has(identity)) next.delete(identity);
      else next.add(identity);
      return next;
    });
  }

  return (
    <ModalPortal>
      <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        ref={dialogRef}
        className="history-modal measurement-history-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="measurement-history-heading">
          <div>
            <span className="measurement-history-box-code">{boxCode}</span>
            <h2 id={titleId} ref={titleRef} tabIndex={-1}>{labels.measurementHistory}</h2>
          </div>
          <button className="icon-button" type="button" aria-label={labels.close} onClick={onClose}>
            <PolypbaseIcon name="close" size={19} />
          </button>
        </header>

        <div className="measurement-history-toolbar">
          <label>
            <span>{labels.historyYearFilter}</span>
            <select value={selectedYear} onChange={(event) => selectYear(event.target.value)}>
              <option value="all">{labels.historyAllYears}</option>
              {availableYears.map((year) => <option key={year} value={year}>{year}</option>)}
            </select>
          </label>
          <span role="status">
            {labels.historyVisibleCount(visibleEntries.length, filteredEntries.length)}
          </span>
        </div>

        <MeasurementHistoryList
          listRef={listRef}
          expandedNotes={expandedNotes}
          labels={labels}
          language={language}
          entries={visibleEntries}
          onToggleNote={toggleNote}
        />

        {remainingCount > 0 ? (
          <footer className="measurement-history-footer">
            <button
              className="secondary-button compact-button"
              type="button"
              onClick={(event) => {
                // The last batch removes this control; keep focus inside the reading area.
                if (remainingCount <= 24) listRef.current?.focus({ preventScroll: true });
                else event.currentTarget.focus({ preventScroll: true });
                setVisibleCount((current) => current + 24);
              }}
            >
              {labels.historyShowMore} ({Math.min(24, remainingCount)})
            </button>
          </footer>
        ) : null}
        </section>
      </div>
    </ModalPortal>
  );
}

type HistoryEntry = {
  identity: string;
  effective_date: string;
  timestamp: string | null;
  state_sequence?: number | null;
} & (
  | { kind: 'measurement'; measurement: BiologicalMeasurement }
  | { kind: 'subculture' | 'subculture_initialization'; event: BiologicalTimelineEntry }
);

function buildHistoryEntries(measurements: BiologicalMeasurement[], timeline?: readonly BiologicalTimelineEntry[]): HistoryEntry[] {
  const entries = new Map<string, HistoryEntry>();
  measurements.forEach((measurement) => {
    const identity = `measurement:${measurement.id}`;
    entries.set(identity, {
      identity,
      kind: 'measurement',
      effective_date: measurement.measured_on,
      timestamp: measurement.created_at,
      measurement,
    });
  });
  timeline?.forEach((entry) => {
    const identity = entry.identity ?? `${entry.kind}:${entry.id}`;
    if (entry.kind === 'measurement') {
      const existing = entries.get(`measurement:${entry.id}`);
      const measurement = entry.measurement ?? (existing?.kind === 'measurement' ? existing.measurement : undefined);
      if (!measurement) return;
      // Keep the original reading payload; operation snapshots never replace its counts.
      entries.delete(`measurement:${entry.id}`);
      entries.set(identity, {
        identity, kind: entry.kind, measurement,
        effective_date: measurement.measured_on,
        timestamp: measurement.created_at,
        state_sequence: entry.state_sequence,
      });
    } else {
      entries.set(identity, {
        identity, kind: entry.kind, event: entry,
        effective_date: entry.effective_date,
        timestamp: entry.timestamp ?? null,
        state_sequence: entry.state_sequence,
      });
    }
  });
  return [...entries.values()].sort((left, right) => (
    right.effective_date.localeCompare(left.effective_date)
    || (right.state_sequence ?? 0) - (left.state_sequence ?? 0)
    || (right.timestamp ?? '').localeCompare(left.timestamp ?? '')
  ));
}

function MeasurementHistoryList({
  listRef,
  expandedNotes,
  labels,
  language,
  entries,
  onToggleNote,
}: {
  listRef: RefObject<HTMLDivElement>;
  expandedNotes: Set<string>;
  labels: BoxInsightsLabels;
  language: Language;
  entries: HistoryEntry[];
  onToggleNote: (identity: string) => void;
}) {
  const t = createTranslator(language);
  const timelineLabels = getBiologicalTimelineLabels(t);
  return (
    <div ref={listRef} className="measurement-history-table" role="table" tabIndex={0} aria-label={labels.measurementHistory}>
      <div className="measurement-history-columns" role="row">
        <span role="columnheader">{labels.historyYear}</span>
        <span role="columnheader">{labels.polyps}</span>
        <span role="columnheader">{labels.ephyraeFull}</span>
        <span role="columnheader">PSU</span>
        <span role="columnheader">{labels.historyEnteredBy}</span>
        <span role="columnheader">{labels.historyObservation}</span>
      </div>

      {!entries.length ? (
        <div className="measurement-history-empty">{labels.noMeasurementHistory}</div>
      ) : null}

      {entries.map((entry) => {
        const measurement = entry.kind === 'measurement' ? entry.measurement : null;
        const event = entry.kind === 'measurement' ? null : entry.event;
        const note = (measurement?.notes ?? event?.notes)?.trim() ?? '';
        const isLongNote = note.length > 140;
        const isExpanded = expandedNotes.has(entry.identity);
        const typeLabel = measurement ? t('auditObjectMeasurement')
          : entry.kind === 'subculture_initialization'
            ? `${timelineLabels.subcultureEvent} — ${t('auditMetaInitialPolypCounts')}`
            : timelineLabels.subcultureEvent;
        const allocations = [...(event?.allocations ?? [])].sort((left, right) => left.position - right.position);

        return (
          <article key={entry.identity} className={`measurement-history-entry${event ? ' measurement-history-entry--operation' : ''}`} role="row" data-entry-kind={entry.kind}>
            <div className="measurement-history-date" role="cell">
              <small aria-hidden="true">{labels.historyYear}</small>
              <time dateTime={entry.effective_date}>{formatDisplayDate(entry.effective_date)}</time>
              <span className="measurement-history-type">{typeLabel}</span>
              {event && entry.timestamp ? (
                <time className="measurement-history-timestamp" dateTime={entry.timestamp}>{formatDisplayDateTime(entry.timestamp)}</time>
              ) : null}
            </div>
            <div className="measurement-history-value" role="cell">
              <small aria-hidden="true">{labels.polyps}</small>
              <strong>{measurement ? measurement.polyp_count : event?.polyp_count_after ?? '—'}</strong>
            </div>
            <div className="measurement-history-value" role="cell">
              <small aria-hidden="true">{labels.ephyraeFull}</small>
              <strong>{measurement ? measurement.ephyrae_count : '—'}</strong>
            </div>
            <div className="measurement-history-value" role="cell">
              <small aria-hidden="true">PSU</small>
              <strong className={measurement?.salinity_psu == null ? 'is-missing' : ''}>
                {measurement?.salinity_psu == null ? '—' : formatDecimal(measurement.salinity_psu)}
              </strong>
            </div>
            <div className="measurement-history-user" role="cell">
              <small aria-hidden="true">{labels.historyEnteredBy}</small>
              <span>{measurement ? measurement.user ?? '—' : event?.author?.username ?? '—'}</span>
            </div>
            <div className="measurement-history-note" role="cell">
              <small aria-hidden="true">{labels.historyObservation}</small>
              {event ? (
                <dl className="measurement-history-operation-details">
                  {entry.kind === 'subculture' ? (
                    <div>
                      <dt>{timelineLabels.parentBox}</dt>
                      <dd>
                        {t('auditMetaBefore')}: {event.polyp_count_before ?? '—'}
                        {' → '}{t('auditMetaAfter')}: {event.polyp_count_after ?? '—'}
                      </dd>
                    </div>
                  ) : event.parent ? (
                    <div><dt>{timelineLabels.parentBox}</dt><dd>{event.parent.global_code}</dd></div>
                  ) : null}
                  <div><dt>{timelineLabels.allocatedPolyps}</dt><dd>{event.allocated_polyps ?? '—'}</dd></div>
                  {allocations.length || event.children?.length ? (
                    <div>
                      <dt>{timelineLabels.childBoxes}</dt>
                      <dd>
                        <ul>
                          {allocations.length ? allocations.map((allocation) => (
                            <li key={allocation.child_box_id}>{allocation.child_global_code}: {allocation.allocated_polyps ?? t('subcultureUnknown')}</li>
                          )) : event.children?.map((child) => <li key={child.id}>{child.global_code}</li>)}
                        </ul>
                      </dd>
                    </div>
                  ) : null}
                </dl>
              ) : null}
              <p className={isLongNote && !isExpanded ? 'is-collapsed' : ''}>{note || '—'}</p>
              {isLongNote ? (
                <button type="button" aria-expanded={isExpanded} onClick={() => onToggleNote(entry.identity)}>
                  {isExpanded ? labels.historyHideComment : labels.historyReadComment}
                </button>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="metric">
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function formatDecimal(value: string | number) {
  const numeric = typeof value === 'number' ? value : Number.parseFloat(value);
  if (!Number.isFinite(numeric)) return String(value);
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1);
}
