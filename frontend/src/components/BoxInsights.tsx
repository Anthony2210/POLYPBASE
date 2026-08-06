import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { scaleLinear, scaleTime } from 'd3-scale';
import { line } from 'd3-shape';

import type {
  BiologicalMeasurement,
  BoxLineage,
  BoxMovement,
  BoxTemperaturePoint,
  LineageGraph,
} from '../types';
import { formatDisplayDate } from '../utils/dateFormat';

const InteractiveLineageGraph = lazy(() => import('./InteractiveLineageGraph'));

export type BoxInsightTab = 'measurements' | 'movements' | 'lineage';

type Language = 'fr' | 'en';
type PeriodId = '1m' | '3m' | '6m' | '12m';
type SeriesId = 'polyps' | 'ephyrae' | 'temperature' | 'events';

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

type LifecycleEvent = {
  id: string;
  date: string;
  type: 'movement' | 'subculture';
  title: string;
  detail: string;
};

type ChartTooltip = {
  left: number;
  top: number;
  title: string;
  lines: string[];
};

const PERIODS: Array<{ id: PeriodId; days: number | null; labelKey: keyof BoxInsightsLabels }> = [
  { id: '1m', days: 31, labelKey: 'oneMonth' },
  { id: '3m', days: 92, labelKey: 'threeMonths' },
  { id: '6m', days: 184, labelKey: 'sixMonths' },
  { id: '12m', days: 365, labelKey: 'oneYear' },
];

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
  temperatureHistory,
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
  temperatureHistory: BoxTemperaturePoint[];
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
          <div className="insight-heading">
            <h2>{labels.chartTitle}</h2>
            <button type="button" onClick={onOpenHistory}>{labels.historyButton}</button>
          </div>
          <MeasurementTrendChart
            events={lifecycleEvents}
            labels={labels}
            measurements={measurements}
            temperatureHistory={temperatureHistory}
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

function MeasurementTrendChart({
  events,
  labels,
  measurements,
  temperatureHistory,
}: {
  events: LifecycleEvent[];
  labels: BoxInsightsLabels;
  measurements: BiologicalMeasurement[];
  temperatureHistory: BoxTemperaturePoint[];
}) {
  const [period, setPeriod] = useState<PeriodId>('6m');
  const [visibleSeries, setVisibleSeries] = useState<Record<SeriesId, boolean>>({
    polyps: true,
    ephyrae: true,
    temperature: true,
    events: true,
  });
  const [tooltip, setTooltip] = useState<ChartTooltip | null>(null);

  const preparedData = useMemo(
    () => prepareChartData(measurements, temperatureHistory, events, period),
    [measurements, temperatureHistory, events, period],
  );

  const chartGeometry = useMemo(() => {
    const width = 860;
    const countHeight = 250;
    const countPadding = { top: 30, right: 58, bottom: 34, left: 44 };
    const xScale = scaleTime()
      .domain([preparedData.startDate, preparedData.endDate])
      .range([countPadding.left, width - countPadding.right]);
    const maxCount = Math.max(
      1,
      ...preparedData.measurements.flatMap((measurement) => [
        measurement.polyp_count,
        measurement.ephyrae_count,
      ]),
    );
    const yCount = scaleLinear()
      .domain([0, maxCount])
      .nice()
      .range([countHeight - countPadding.bottom, countPadding.top]);
    const temperatureValues = preparedData.temperatures.map((point) => point.average_temperature_c);
    const minTemperature = temperatureValues.length ? Math.min(...temperatureValues) : 0;
    const maxTemperature = temperatureValues.length ? Math.max(...temperatureValues) : 1;
    const temperatureDomainMin = Math.floor(minTemperature - 0.5);
    const temperatureDomainMax = Math.ceil(maxTemperature + 0.5);
    const yTemperature = scaleLinear()
      .domain([temperatureDomainMin, temperatureDomainMax])
      .range([countHeight - countPadding.bottom, countPadding.top]);
    const xPosition = (date: string) => xScale(parseChartDate(date));
    const measurementSegments = splitMeasurementsOnGaps(preparedData.measurements);
    const missingRanges = buildMissingRanges(preparedData.measurements);
    const hasMeasurementData = preparedData.measurements.length > 0;
    const hasTemperaturePoints = preparedData.temperatures.length > 0;
    const canDrawTemperatureLine = preparedData.temperatures.length >= 2;
    const hasEventData = preparedData.events.length > 0;
    const countLine = (selector: (measurement: BiologicalMeasurement) => number) =>
      line<BiologicalMeasurement>()
        .x((measurement) => xPosition(measurement.measured_on))
        .y((measurement) => yCount(selector(measurement)));
    const temperatureLine = line<BoxTemperaturePoint>()
      .x((point) => xPosition(point.date))
      .y((point) => yTemperature(point.average_temperature_c));

    return {
      canDrawTemperatureLine,
      countHeight,
      countLine,
      countPadding,
      hasAnyData: hasMeasurementData || hasTemperaturePoints || hasEventData,
      hasTemperaturePoints,
      maxCount,
      measurementSegments,
      missingRanges,
      temperatureDomainMax,
      temperatureDomainMin,
      temperatureLine,
      width,
      xPosition,
      yCount,
      yTemperature,
    };
  }, [preparedData]);
  const {
    canDrawTemperatureLine,
    countHeight,
    countLine,
    countPadding,
    hasAnyData,
    hasTemperaturePoints,
    maxCount,
    measurementSegments,
    missingRanges,
    temperatureDomainMax,
    temperatureDomainMin,
    temperatureLine,
    width,
    xPosition,
    yCount,
    yTemperature,
  } = chartGeometry;

  function toggleSeries(series: SeriesId) {
    setVisibleSeries((current) => ({ ...current, [series]: !current[series] }));
  }

  return (
    <div className="measurement-chart activity-chart" aria-label={labels.chartTitle}>
      <div className="chart-toolbar">
        <div className="chart-periods" aria-label="Periode">
          {PERIODS.map((item) => (
            <button
              key={item.id}
              className={period === item.id ? 'is-active' : ''}
              type="button"
              onClick={() => setPeriod(item.id)}
            >
              {labels[item.labelKey]}
            </button>
          ))}
        </div>
        <div className="chart-series-controls">
          <ToggleButton active={visibleSeries.polyps} label={labels.polyps} onClick={() => toggleSeries('polyps')} />
          <ToggleButton active={visibleSeries.ephyrae} label={labels.ephyraeFull} onClick={() => toggleSeries('ephyrae')} />
          <ToggleButton active={visibleSeries.temperature} label={labels.temperature} onClick={() => toggleSeries('temperature')} />
          <ToggleButton active={visibleSeries.events} label={labels.events} onClick={() => toggleSeries('events')} />
        </div>
      </div>

      <div className="activity-chart-canvas" onPointerLeave={() => setTooltip(null)}>
        <svg className="activity-count-chart" viewBox={`0 0 ${width} ${countHeight}`} role="img">
          <line className="chart-axis" x1={countPadding.left} y1={countHeight - countPadding.bottom} x2={width - countPadding.right} y2={countHeight - countPadding.bottom} />
          <line className="chart-axis" x1={countPadding.left} y1={countPadding.top} x2={countPadding.left} y2={countHeight - countPadding.bottom} />
          {visibleSeries.temperature ? (
            <line
              className="chart-axis is-temperature-axis"
              x1={width - countPadding.right}
              y1={countPadding.top}
              x2={width - countPadding.right}
              y2={countHeight - countPadding.bottom}
            />
          ) : null}
          {[0.25, 0.5, 0.75].map((ratio) => {
            const y = countPadding.top + ratio * (countHeight - countPadding.top - countPadding.bottom);
            return <line key={ratio} className="chart-grid-line" x1={countPadding.left} y1={y} x2={width - countPadding.right} y2={y} />;
          })}

          {!hasAnyData ? (
            <text
              className="chart-empty-label"
              x={width / 2}
              y={countPadding.top + (countHeight - countPadding.top - countPadding.bottom) / 2}
            >
              {labels.chartEmpty}
            </text>
          ) : null}

          {missingRanges.map((range) => {
            const x1 = xPosition(range.start);
            const x2 = xPosition(range.end);
            return (
              <g
                key={`${range.start}-${range.end}`}
                className="chart-missing-range"
                onPointerEnter={() => setTooltip({
                  left: ((x1 + x2) / 2 / width) * 100,
                  top: 24,
                  title: labels.missingReading,
                  lines: [`${formatDisplayDate(range.start)} - ${formatDisplayDate(range.end)}`],
                })}
              >
                <rect
                  x={x1}
                  y={countPadding.top}
                  width={Math.max(3, x2 - x1)}
                  height={countHeight - countPadding.top - countPadding.bottom}
                />
              </g>
            );
          })}

          {visibleSeries.polyps ? measurementSegments.map((segment, index) => (
            <path
              key={`polyps-${index}`}
              className="chart-line is-polyps"
              d={countLine((measurement) => measurement.polyp_count)(segment) ?? ''}
            />
          )) : null}
          {visibleSeries.ephyrae ? measurementSegments.map((segment, index) => (
            <path
              key={`ephyrae-${index}`}
              className="chart-line is-ephyrae"
              d={countLine((measurement) => measurement.ephyrae_count)(segment) ?? ''}
            />
          )) : null}
          {visibleSeries.temperature && canDrawTemperatureLine ? (
            <path className="chart-line is-temperature" d={temperatureLine(preparedData.temperatures) ?? ''} />
          ) : null}

          {visibleSeries.events ? preparedData.events.map((event) => {
            const x = xPosition(event.date);
            return (
              <g
                key={event.id}
                className={`chart-event-marker is-${event.type}`}
                transform={`translate(${x} ${countPadding.top - 3})`}
                onPointerEnter={() => setTooltip({
                  left: (x / width) * 100,
                  top: 9,
                  title: event.title,
                  lines: [formatDisplayDate(event.date), event.detail].filter(Boolean),
                })}
              >
                <line x1={0} y1={9} x2={0} y2={countHeight - countPadding.top - countPadding.bottom + 3} />
                <path d="M0 0 L6 6 L0 12 L-6 6 Z" />
              </g>
            );
          }) : null}

          {preparedData.measurements.map((measurement) => {
            const x = xPosition(measurement.measured_on);
            return (
              <g
                key={measurement.id}
                className="chart-measurement-point"
                onPointerEnter={() => setTooltip({
                  left: (x / width) * 100,
                  top: Math.max(
                    12,
                    (Math.min(
                      yCount(measurement.polyp_count),
                      yCount(measurement.ephyrae_count),
                    ) / countHeight) * 100 - 5,
                  ),
                  title: formatDisplayDate(measurement.measured_on),
                  lines: [
                    `${labels.polyps} : ${measurement.polyp_count}`,
                    `${labels.ephyraeFull} : ${measurement.ephyrae_count}`,
                    measurement.salinity_psu ? `${labels.salinityFull} : ${formatDecimal(measurement.salinity_psu)}` : '',
                  ].filter(Boolean),
                })}
              >
                {visibleSeries.polyps ? (
                  <circle
                    className="chart-dot is-polyps"
                    cx={x}
                    cy={yCount(measurement.polyp_count)}
                    r={measurement.polyp_count === 0 ? 5 : 4}
                  />
                ) : null}
                {visibleSeries.ephyrae ? (
                  <circle
                    className="chart-dot is-ephyrae"
                    cx={x}
                    cy={yCount(measurement.ephyrae_count)}
                    r={measurement.ephyrae_count === 0 ? 5 : 4}
                  />
                ) : null}
                <rect
                  className="chart-hit-area"
                  x={x - 12}
                  y={countPadding.top}
                  width={24}
                  height={countHeight - countPadding.top - countPadding.bottom}
                />
              </g>
            );
          })}

          {visibleSeries.temperature && !hasTemperaturePoints ? (
            <text
              className="chart-empty-label is-small"
              x={width - countPadding.right - 76}
              y={countPadding.top + 18}
            >
              {labels.temperatureNoData}
            </text>
          ) : null}

          {visibleSeries.temperature ? preparedData.temperatures.map((point) => {
            const x = xPosition(point.date);
            const y = yTemperature(point.average_temperature_c);
            return (
              <g
                key={`${point.date}-${point.zone_id}`}
                className="chart-temperature-point"
                onPointerEnter={() => setTooltip({
                  left: (x / width) * 100,
                  top: Math.max(12, (y / countHeight) * 100 - 5),
                  title: formatDisplayDate(point.date),
                  lines: [
                    `${labels.temperature} : ${point.average_temperature_c.toFixed(1)}\u00b0C`,
                    point.zone_name,
                  ],
                })}
              >
                <circle className="chart-dot is-temperature" cx={x} cy={y} r={3.5} />
                <rect
                  className="chart-hit-area"
                  x={x - 10}
                  y={countPadding.top}
                  width={20}
                  height={countHeight - countPadding.top - countPadding.bottom}
                />
              </g>
            );
          }) : null}

          <text className="chart-label" x={countPadding.left} y={countHeight - 10}>
            {formatDisplayDate(toDateString(preparedData.startDate))}
          </text>
          <text className="chart-label is-end" x={width - countPadding.right} y={countHeight - 10}>
            {formatDisplayDate(toDateString(preparedData.endDate))}
          </text>
          <text className="chart-y-label" x={countPadding.left - 8} y={countPadding.top + 4}>{maxCount}</text>
          <text className="chart-y-label" x={countPadding.left - 8} y={countHeight - countPadding.bottom + 4}>0</text>
          {visibleSeries.temperature && hasTemperaturePoints ? (
            <>
              <text className="chart-temperature-label" x={width - countPadding.right + 8} y={countPadding.top + 4}>
                {`${temperatureDomainMax}\u00b0C`}
              </text>
              <text className="chart-temperature-label" x={width - countPadding.right + 8} y={countHeight - countPadding.bottom + 4}>
                {`${temperatureDomainMin}\u00b0C`}
              </text>
            </>
          ) : null}
        </svg>

        {tooltip ? (
          <div
            className="chart-tooltip"
            style={{ left: `${tooltip.left}%`, top: `${tooltip.top}%` }}
          >
            <strong>{tooltip.title}</strong>
            {tooltip.lines.map((lineText) => (
              <span key={lineText}>{lineText}</span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="chart-legend">
        <span className="is-polyps">{labels.polyps}</span>
        <span className="is-ephyrae">{labels.ephyraeFull}</span>
        <span className="is-temperature">{labels.temperature}</span>
        <span className="is-missing">{labels.missingReading}</span>
      </div>
    </div>
  );
}

function ToggleButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className={active ? 'is-active' : ''}
      type="button"
      aria-pressed={active}
      onClick={onClick}
    >
      {label}
    </button>
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

  return createPortal(
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
            <span aria-hidden="true">×</span>
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
    </div>,
    document.body,
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

function prepareChartData(
  measurements: BiologicalMeasurement[],
  temperatures: BoxTemperaturePoint[],
  events: LifecycleEvent[],
  period: PeriodId,
) {
  const sortedMeasurements = [...measurements].sort((left, right) => left.measured_on.localeCompare(right.measured_on));
  const sortedTemperatures = [...temperatures].sort((left, right) => left.date.localeCompare(right.date));
  const sortedEvents = [...events].sort((left, right) => left.date.localeCompare(right.date));
  const lastDate = getLastDate(sortedMeasurements, sortedTemperatures, sortedEvents);
  const periodConfig = PERIODS.find((item) => item.id === period);
  const startDate = periodConfig?.days ? addDays(lastDate, -periodConfig.days) : getFirstDate(sortedMeasurements, sortedTemperatures, sortedEvents);
  const endDate = addDays(lastDate, 1);
  const startText = toDateString(startDate);
  const endText = toDateString(endDate);

  return {
    startDate,
    endDate,
    measurements: sortedMeasurements.filter((measurement) => measurement.measured_on >= startText && measurement.measured_on <= endText),
    temperatures: sortedTemperatures.filter((point) => point.date >= startText && point.date <= endText),
    events: sortedEvents.filter((event) => event.date >= startText && event.date <= endText),
  };
}

function splitMeasurementsOnGaps(measurements: BiologicalMeasurement[]) {
  const segments: BiologicalMeasurement[][] = [];
  let currentSegment: BiologicalMeasurement[] = [];

  measurements.forEach((measurement, index) => {
    const previous = measurements[index - 1];
    if (previous && getDaysBetween(previous.measured_on, measurement.measured_on) > 10) {
      if (currentSegment.length) segments.push(currentSegment);
      currentSegment = [];
    }
    currentSegment.push(measurement);
  });

  if (currentSegment.length) segments.push(currentSegment);
  return segments;
}

function buildMissingRanges(measurements: BiologicalMeasurement[]) {
  return measurements.flatMap((measurement, index) => {
    const previous = measurements[index - 1];
    if (!previous) return [];
    const gap = getDaysBetween(previous.measured_on, measurement.measured_on);
    if (gap <= 10) return [];

    return [{
      start: toDateString(addDays(parseChartDate(previous.measured_on), 1)),
      end: toDateString(addDays(parseChartDate(measurement.measured_on), -1)),
    }];
  });
}

function buildLifecycleEvents(
  lineage: BoxLineage,
  movements: BoxMovement[],
  labels: BoxInsightsLabels,
) {
  const events = new Map<string, LifecycleEvent>();

  movements.forEach((movement) => {
    const date = movement.moved_at.slice(0, 10);
    events.set(`move-${movement.id}`, {
      id: `move-${movement.id}`,
      date,
      type: 'movement',
      title: labels.movementEvent,
      detail: movement.from_thermal_zone
        ? `${movement.from_thermal_zone.name} -> ${movement.to_thermal_zone.name}`
        : movement.to_thermal_zone.name,
    });
  });

  lineage.parents.forEach((relation) => {
    if (!relation.event) return;
    events.set(`subculture-parent-${relation.event.id}`, {
      id: `subculture-parent-${relation.event.id}`,
      date: relation.event.event_date,
      type: 'subculture',
      title: labels.subcultureEvent,
      detail: relation.box.global_code,
    });
  });

  lineage.children.forEach((relation) => {
    if (!relation.event) return;
    events.set(`subculture-child-${relation.event.id}-${relation.box.id}`, {
      id: `subculture-child-${relation.event.id}-${relation.box.id}`,
      date: relation.event.event_date,
      type: 'subculture',
      title: labels.subcultureEvent,
      detail: relation.box.global_code,
    });
  });

  return [...events.values()].sort((left, right) => left.date.localeCompare(right.date));
}

function getFirstDate(
  measurements: BiologicalMeasurement[],
  temperatures: BoxTemperaturePoint[],
  events: LifecycleEvent[],
) {
  const dates = [
    measurements[0]?.measured_on,
    temperatures[0]?.date,
    events[0]?.date,
  ].filter(Boolean) as string[];
  return dates.length ? parseChartDate(dates.sort()[0]) : addDays(new Date(), -30);
}

function getLastDate(
  measurements: BiologicalMeasurement[],
  temperatures: BoxTemperaturePoint[],
  events: LifecycleEvent[],
) {
  const dates = [
    measurements.length ? measurements[measurements.length - 1]?.measured_on : undefined,
    temperatures.length ? temperatures[temperatures.length - 1]?.date : undefined,
    events.length ? events[events.length - 1]?.date : undefined,
  ].filter(Boolean) as string[];
  const sortedDates = dates.sort();
  return sortedDates.length ? parseChartDate(sortedDates[sortedDates.length - 1]) : new Date();
}

function getDaysBetween(firstDate: string, secondDate: string) {
  const diff = parseChartDate(secondDate).getTime() - parseChartDate(firstDate).getTime();
  return Math.round(diff / 86_400_000);
}

function parseChartDate(value: string) {
  return new Date(`${value.slice(0, 10)}T00:00:00`);
}

function addDays(date: Date, days: number) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
}

function toDateString(date: Date) {
  const copy = new Date(date);
  copy.setMinutes(copy.getMinutes() - copy.getTimezoneOffset());
  return copy.toISOString().slice(0, 10);
}

function formatDecimal(value: string | number) {
  const numeric = typeof value === 'number' ? value : Number.parseFloat(value);
  if (!Number.isFinite(numeric)) return String(value);
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1);
}
