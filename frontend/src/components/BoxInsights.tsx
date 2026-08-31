import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useState,
} from 'react';

import type {
  BiologicalMeasurement,
  BoxLocation,
  BoxLineage,
  BoxMovement,
  LineageGraph,
} from '../types';
import { formatDisplayDate } from '../utils/dateFormat';
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
  historyCountLabel: string;
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
            onClick={() => onSelectTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'measurements' ? (
        <div className="insight-panel">
          <BoxTrackingChart
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
        <div className="insight-panel">
          <div className="insight-heading">
            <h2>{labels.movementHistoryTitle}</h2>
          </div>
          <MovementTimeline movements={movements} labels={labels} />
        </div>
      ) : null}

      {activeTab === 'lineage' ? (
        <div className="insight-panel">
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
    <div className="movement-timeline">
      {sortedMovements.map((movement) => (
        <article key={movement.id}>
          <time>{formatDisplayDate(movement.moved_at)}</time>
          <div>
            <strong>
              {movement.from_thermal_zone
                ? `${movement.from_thermal_zone.name} -> ${movement.to_thermal_zone.name}`
                : `${labels.movedTo} ${movement.to_thermal_zone.name}`}
            </strong>
            {movement.user ? <small>{movement.user}</small> : null}
            {movement.notes ? <p>{movement.notes}</p> : null}
          </div>
        </article>
      ))}
    </div>
  );
}

export function MeasurementHistoryModal({
  boxCode,
  labels,
  measurements,
  onClose,
}: {
  boxCode: string;
  labels: BoxInsightsLabels;
  measurements: BiologicalMeasurement[];
  onClose: () => void;
}) {
  const [selectedYear, setSelectedYear] = useState('all');
  const [visibleCount, setVisibleCount] = useState(24);
  const [expandedNotes, setExpandedNotes] = useState<Set<number>>(() => new Set());

  const sortedMeasurements = useMemo(
    () => [...measurements].sort((left, right) => (
      right.measured_on.localeCompare(left.measured_on)
      || right.created_at.localeCompare(left.created_at)
    )),
    [measurements],
  );
  const availableYears = useMemo(
    () => Array.from(new Set<string>(sortedMeasurements.map((measurement) => measurement.measured_on.slice(0, 4))))
      .filter(Boolean)
      .sort((left, right) => right.localeCompare(left)),
    [sortedMeasurements],
  );
  const filteredMeasurements = useMemo(
    () => selectedYear === 'all'
      ? sortedMeasurements
      : sortedMeasurements.filter((measurement) => measurement.measured_on.startsWith(selectedYear)),
    [selectedYear, sortedMeasurements],
  );
  const visibleMeasurements = filteredMeasurements.slice(0, visibleCount);
  const remainingCount = Math.max(0, filteredMeasurements.length - visibleMeasurements.length);

  useEffect(() => {
    setVisibleCount(24);
  }, [selectedYear]);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }

    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  function toggleNote(measurementId: number) {
    setExpandedNotes((current) => {
      const next = new Set(current);
      if (next.has(measurementId)) next.delete(measurementId);
      else next.add(measurementId);
      return next;
    });
  }

  return (
    <ModalPortal>
      <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="history-modal measurement-history-modal"
        role="dialog"
        aria-modal="true"
        aria-label={labels.measurementHistory}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="measurement-history-heading">
          <div>
            <span className="measurement-history-box-code">{boxCode}</span>
            <h2>{labels.measurementHistory}</h2>
            <p>{labels.historyCountLabel} : <strong>{filteredMeasurements.length}</strong></p>
          </div>
          <button className="measurement-history-close" type="button" aria-label={labels.close} onClick={onClose}>
            <PolypbaseIcon name="close" size={19} />
          </button>
        </header>

        <div className="measurement-history-toolbar">
          <label>
            <span>{labels.historyYear}</span>
            <select value={selectedYear} onChange={(event) => setSelectedYear(event.target.value)}>
              <option value="all">{labels.historyAllYears}</option>
              {availableYears.map((year) => <option key={year} value={year}>{year}</option>)}
            </select>
          </label>
          <span aria-live="polite">
            {visibleMeasurements.length} / {filteredMeasurements.length}
          </span>
        </div>

        <MeasurementHistoryList
          expandedNotes={expandedNotes}
          labels={labels}
          measurements={visibleMeasurements}
          onToggleNote={toggleNote}
        />

        {remainingCount > 0 ? (
          <footer className="measurement-history-footer">
            <button type="button" onClick={() => setVisibleCount((current) => current + 24)}>
              {labels.historyShowMore} ({Math.min(24, remainingCount)})
            </button>
          </footer>
        ) : null}
        </section>
      </div>
    </ModalPortal>
  );
}

function MeasurementHistoryList({
  expandedNotes,
  labels,
  measurements,
  onToggleNote,
}: {
  expandedNotes: Set<number>;
  labels: BoxInsightsLabels;
  measurements: BiologicalMeasurement[];
  onToggleNote: (measurementId: number) => void;
}) {
  return (
    <div className="measurement-history-table" role="table" aria-label={labels.measurementHistory}>
      <div className="measurement-history-columns" role="row">
        <span role="columnheader">{labels.historyYear}</span>
        <span role="columnheader">{labels.polyps}</span>
        <span role="columnheader">{labels.ephyraeFull}</span>
        <span role="columnheader">PSU</span>
        <span role="columnheader">{labels.historyEnteredBy}</span>
        <span role="columnheader">{labels.historyObservation}</span>
      </div>

      {!measurements.length ? (
        <div className="measurement-history-empty">{labels.noMeasurementHistory}</div>
      ) : null}

      {measurements.map((measurement) => {
        const note = measurement.notes?.trim() ?? '';
        const isLongNote = note.length > 140;
        const isExpanded = expandedNotes.has(measurement.id);

        return (
          <article key={measurement.id} className="measurement-history-entry" role="row">
            <div className="measurement-history-date" role="cell">
              <small>{labels.historyYear}</small>
              <time dateTime={measurement.measured_on}>{formatDisplayDate(measurement.measured_on)}</time>
            </div>
            <div className="measurement-history-value" role="cell">
              <small>{labels.polyps}</small>
              <strong>{measurement.polyp_count}</strong>
            </div>
            <div className="measurement-history-value" role="cell">
              <small>{labels.ephyraeFull}</small>
              <strong>{measurement.ephyrae_count}</strong>
            </div>
            <div className="measurement-history-value" role="cell">
              <small>PSU</small>
              <strong className={measurement.salinity_psu === null ? 'is-missing' : ''}>
                {measurement.salinity_psu === null ? '—' : formatDecimal(measurement.salinity_psu)}
              </strong>
            </div>
            <div className="measurement-history-user" role="cell">
              <small>{labels.historyEnteredBy}</small>
              <span>{measurement.user ?? '—'}</span>
            </div>
            <div className="measurement-history-note" role="cell">
              <small>{labels.historyObservation}</small>
              <p className={isExpanded ? 'is-expanded' : ''}>{note || '—'}</p>
              {isLongNote ? (
                <button type="button" onClick={() => onToggleNote(measurement.id)}>
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
