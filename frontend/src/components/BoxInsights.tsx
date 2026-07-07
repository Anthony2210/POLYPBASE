import { useState } from 'react';
import { scaleLinear, scalePoint } from 'd3-scale';
import { line } from 'd3-shape';

import type {
  BiologicalMeasurement,
  BoxLineage,
  BoxMovement,
  LineageGraph,
} from '../types';
import InteractiveLineageGraph from './InteractiveLineageGraph';
import { formatDisplayDate } from '../utils/dateFormat';

export type BoxInsightTab = 'measurements' | 'movements' | 'lineage';

type Language = 'fr' | 'en';

type BoxInsightsLabels = {
  chartEmpty: string;
  chartTitle: string;
  children: string;
  close: string;
  ephyraeFull: string;
  historyButton: string;
  lineageEmptyGraph: string;
  lineageLoading: string;
  lineageRetry: string;
  lineageTab: string;
  measurementHistory: string;
  measurementsTab: string;
  movedTo: string;
  movementHistoryTitle: string;
  movementsTab: string;
  noComment: string;
  noMeasurementHistory: string;
  noMovementHistory: string;
  parents: string;
  polyps: string;
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
          <MeasurementTrendChart measurements={measurements} labels={labels} />
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
            <InteractiveLineageGraph
              graph={graph}
              language={language}
              onSelectBox={onSelectBox}
            />
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
  labels,
  measurements,
}: {
  labels: BoxInsightsLabels;
  measurements: BiologicalMeasurement[];
}) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const chartMeasurements = [...measurements]
    .sort((left, right) => left.measured_on.localeCompare(right.measured_on))
    .slice(-12);

  if (chartMeasurements.length < 2) {
    return <p className="muted compact-text chart-empty">{labels.chartEmpty}</p>;
  }

  const width = 720;
  const height = 250;
  const padding = { top: 20, right: 28, bottom: 38, left: 42 };
  const maxValue = Math.max(
    1,
    ...chartMeasurements.flatMap((measurement) => [
      measurement.polyp_count,
      measurement.ephyrae_count,
    ]),
  );
  // D3 scales + line generator (React still renders the SVG).
  const indices = chartMeasurements.map((_, index) => index);
  const xScale = scalePoint<number>()
    .domain(indices)
    .range([padding.left, width - padding.right]);
  const yScale = scaleLinear().domain([0, maxValue]).range([height - padding.bottom, padding.top]);
  const xStep = xScale.step();
  const xPosition = (index: number) => xScale(index) ?? padding.left;
  const buildLinePath = (selector: (measurement: BiologicalMeasurement) => number) =>
    line<number>()
      .x((index) => xPosition(index))
      .y((index) => yScale(selector(chartMeasurements[index])))(indices) ?? '';
  const firstDate = chartMeasurements[0].measured_on;
  const lastDate = chartMeasurements[chartMeasurements.length - 1].measured_on;
  const hoveredMeasurement = hoveredIndex != null ? chartMeasurements[hoveredIndex] : null;
  const hoverX = hoveredIndex != null ? xPosition(hoveredIndex) : null;
  const hoverTop = hoveredMeasurement
    ? Math.min(yScale(hoveredMeasurement.polyp_count), yScale(hoveredMeasurement.ephyrae_count))
    : null;

  return (
    <div className="measurement-chart" aria-label={labels.chartTitle} onPointerLeave={() => setHoveredIndex(null)}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        <line className="chart-axis" x1={padding.left} y1={height - padding.bottom} x2={width - padding.right} y2={height - padding.bottom} />
        <line className="chart-axis" x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} />
        {[0.25, 0.5, 0.75].map((ratio) => {
          const y = padding.top + ratio * (height - padding.top - padding.bottom);
          return <line key={ratio} className="chart-grid-line" x1={padding.left} y1={y} x2={width - padding.right} y2={y} />;
        })}
        <path className="chart-line is-polyps" d={buildLinePath((measurement) => measurement.polyp_count)} />
        <path className="chart-line is-ephyrae" d={buildLinePath((measurement) => measurement.ephyrae_count)} />
        {hoveredMeasurement && hoverX != null ? (
          <line
            className="chart-hover-line"
            x1={hoverX}
            y1={padding.top}
            x2={hoverX}
            y2={height - padding.bottom}
          />
        ) : null}
        {chartMeasurements.map((measurement, index) => (
          <g key={measurement.id}>
            <circle
              className={hoveredIndex === index ? 'chart-dot is-polyps is-active' : 'chart-dot is-polyps'}
              cx={xPosition(index)}
              cy={yScale(measurement.polyp_count)}
              r={hoveredIndex === index ? '5' : '3.5'}
            />
            <circle
              className={hoveredIndex === index ? 'chart-dot is-ephyrae is-active' : 'chart-dot is-ephyrae'}
              cx={xPosition(index)}
              cy={yScale(measurement.ephyrae_count)}
              r={hoveredIndex === index ? '5' : '3.5'}
            />
          </g>
        ))}
        {chartMeasurements.map((measurement, index) => {
          const x = xPosition(index);
          const hitWidth = Math.max(26, xStep * 0.82);

          return (
            <rect
              key={`hit-${measurement.id}`}
              className="chart-hit-area"
              x={x - hitWidth / 2}
              y={padding.top}
              width={hitWidth}
              height={height - padding.top - padding.bottom}
              tabIndex={0}
              onBlur={() => setHoveredIndex(null)}
              onFocus={() => setHoveredIndex(index)}
              onPointerEnter={() => setHoveredIndex(index)}
            />
          );
        })}
        <text className="chart-label" x={padding.left} y={height - 12}>{formatDisplayDate(firstDate)}</text>
        <text className="chart-label is-end" x={width - padding.right} y={height - 12}>{formatDisplayDate(lastDate)}</text>
        <text className="chart-y-label" x={padding.left - 8} y={padding.top + 4}>{maxValue}</text>
        <text className="chart-y-label" x={padding.left - 8} y={height - padding.bottom + 4}>0</text>
      </svg>

      {hoveredMeasurement && hoverX != null && hoverTop != null ? (
        <div
          className="chart-tooltip"
          style={{
            left: `${(hoverX / width) * 100}%`,
            top: `${Math.max(8, (hoverTop / height) * 100 - 8)}%`,
          }}
        >
          <strong>{formatDisplayDate(hoveredMeasurement.measured_on)}</strong>
          <span>{labels.polyps} : {hoveredMeasurement.polyp_count}</span>
          <span>{labels.ephyraeFull} : {hoveredMeasurement.ephyrae_count}</span>
        </div>
      ) : null}

      <div className="chart-legend">
        <span className="is-polyps">{labels.polyps}</span>
        <span className="is-ephyrae">{labels.ephyraeFull}</span>
      </div>
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
  labels,
  measurements,
  onClose,
}: {
  labels: BoxInsightsLabels;
  measurements: BiologicalMeasurement[];
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="history-modal"
        role="dialog"
        aria-modal="true"
        aria-label={labels.measurementHistory}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <h2>{labels.measurementHistory}</h2>
            <span>{measurements.length}</span>
          </div>
          <button type="button" onClick={onClose}>
            {labels.close}
          </button>
        </div>

        <MeasurementHistoryList measurements={measurements} labels={labels} />
      </section>
    </div>
  );
}

function MeasurementHistoryList({
  labels,
  measurements,
}: {
  labels: BoxInsightsLabels;
  measurements: BiologicalMeasurement[];
}) {
  return (
    <div className="measurement-history">
      {!measurements.length ? <p className="muted compact-text">{labels.noMeasurementHistory}</p> : null}

      {measurements.map((measurement) => (
        <article key={measurement.id} className="measurement-row">
          <div>
            <strong>{formatDisplayDate(measurement.measured_on)}</strong>
            <small>{measurement.user ?? '-'}</small>
          </div>
          <span>
            <strong>{measurement.polyp_count}</strong>
            <small>{labels.polyps}</small>
          </span>
          <span>
            <strong>{measurement.ephyrae_count}</strong>
            <small>{labels.ephyraeFull}</small>
          </span>
          <p>{measurement.notes?.trim() || labels.noComment}</p>
        </article>
      ))}
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
