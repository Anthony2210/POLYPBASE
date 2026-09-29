import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from 'react';
import { scaleLinear, scaleTime } from 'd3-scale';
import { line } from 'd3-shape';

import { formatDisplayDate } from '../utils/dateFormat';
import {
  chartBiologicalValues,
  dismissPinnedChartDetail,
  placeChartDetail,
  resolveChartDetail,
  selectedVisibleReading,
  splitMeasurementsOnGaps,
  toggleChartSeries,
} from '../utils/chartBiology';
import {
  findChartLocationAtDate,
  resolveChartLocationPeriods,
  type ChartLocation,
} from '../utils/chartLocations';

export type TrendMeasurement = {
  id: number | string;
  date: string;
  polypCount: number;
  ephyraeCount: number;
  salinity?: string | number | null;
  enteredBy?: string | null;
  note?: string | null;
};

export type TrendLocation = ChartLocation;

export type TrendEvent = {
  id: number | string;
  date: string;
  title: string;
  detail?: string;
  kind?: 'movement' | 'subculture';
};

type TrendLabels = {
  chartTitle: string;
  closeDetail?: string;
  empty: string;
  ephyrae: string;
  enteredBy?: string;
  location: string;
  missingReading: string;
  movement?: string;
  observation?: string;
  polyps: string;
  salinity?: string;
  selectReading?: string;
  selectedReading?: string;
};

type ActiveDetail = {
  date?: string;
  id: string;
  left: number;

  top: number;
  title: string;
  lines: DetailLine[];
};

type DetailLine = {
  kind: 'ephyrae' | 'location' | 'note' | 'polyps' | 'salinity' | 'user';
  label: string;
  value: string;
};

type LocationBand = {
  endDate: string;
  id: number | string;
  name: string;
  startDate: string;
  width: number;
  x1: number;
};

type TimeTick = {
  date: string;
  label: string;
  x: number;
};

const BIOLOGICAL_COUNT_AXIS_MAX = 1000;

export default function BiologicalTrendChart({
  compact = false,
  endDate,
  events = [],
  labels,
  locations = [],
  measurements,
  selectionScope,
  startDate,
}: {
  compact?: boolean;
  endDate: string;
  events?: TrendEvent[];
  labels: TrendLabels;
  locations?: TrendLocation[];
  measurements: TrendMeasurement[];
  selectionScope?: number | string;
  startDate: string;
}) {
  const [pinnedDetail, setPinnedDetail] = useState<ActiveDetail | null>(null);
  const [hoveredDetail, setHoveredDetail] = useState<ActiveDetail | null>(null);
  const [focusedDetail, setFocusedDetail] = useState<ActiveDetail | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [visibleSeries, setVisibleSeries] = useState({ polyps: true, ephyrae: true });
  const geometry = useMemo(
    () => buildGeometry(measurements, locations, events, startDate, endDate, compact),
    [compact, endDate, events, locations, measurements, startDate],
  );

  useEffect(() => {
    setPinnedDetail(null);
    setHoveredDetail(null);
    setFocusedDetail(null);
    setVisibleSeries({ polyps: true, ephyrae: true });
  }, [selectionScope]);

  useEffect(() => {
    setPinnedDetail((current) => current?.id.startsWith('measurement-')
      && measurements.some((measurement) => (
        `measurement-${measurement.id}` === current.id
        && measurement.date >= startDate && measurement.date <= endDate
      )) ? current : null);
    setHoveredDetail(null);
    setFocusedDetail(null);
  }, [startDate, endDate]);

  function toggleDetail(detail: ActiveDetail) {
    setPinnedDetail((current) => current?.id === detail.id ? null : detail);
  }

  function dismissPinnedDetail(event: KeyboardEvent<SVGGElement>) {
    const result = dismissPinnedChartDetail(event.key, pinnedDetail);
    if (!result.handled) return false;
    event.preventDefault();
    setPinnedDetail(result.pinned);
    return true;
  }

  function handleDetailKey(event: KeyboardEvent<SVGGElement>, detail: ActiveDetail) {
    if (dismissPinnedDetail(event)) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggleDetail(detail);
  }

  function stopAndToggle(event: MouseEvent<SVGGElement>, detail: ActiveDetail) {
    event.stopPropagation();
    toggleDetail(detail);
  }

  function selectMeasurement(event: MouseEvent<SVGGElement>, detail: ActiveDetail) {
    event.stopPropagation();
    setPinnedDetail(detail);
  }

  const {
    countHeight,
    countLine,
    chartAreaHeight,
    end,
    eventPoints,
    locationBands,
    maxCount,
    measurementSegments,
    padding,
    plotHeight,
    plotTop,
    plottedMeasurements,
    start,
    timeTicks,
    width,
    xPosition,
    yCount,
    zoneBandHeight,
  } = geometry;
  const plottedReadingDetails = plottedMeasurements.map((measurement) => {
    const x = xPosition(measurement.date);
    const topY = Math.min(
      visibleSeries.polyps ? yCount(measurement.polypCount) : Infinity,
      visibleSeries.ephyrae ? yCount(measurement.ephyraeCount) : Infinity,
    );
    const locationName = findChartLocationAtDate(locations, measurement.date);
    const detail: ActiveDetail = {
      id: `measurement-${measurement.id}`,
      date: measurement.date,
      left: (x / width) * 100,
      top: topY,
      title: formatDisplayDate(measurement.date),
      lines: buildMeasurementDetailLines(measurement, labels, locations, locationName),
    };
    return { detail, measurement, x };
  });
  const selectedDetail = selectedVisibleReading(plottedReadingDetails.map(({ detail }) => detail), pinnedDetail?.id ?? null);
  const selectedReading = plottedReadingDetails.find(({ detail }) => detail.id === selectedDetail?.id) ?? null;
  const pinnedVisibleDetail = pinnedDetail?.id.startsWith('measurement-') ? selectedDetail : pinnedDetail;
  const visibleDetail = resolveChartDetail(pinnedVisibleDetail, focusedDetail, hoveredDetail);
  const hasOverflow = plottedMeasurements.some((measurement) => (
    (visibleSeries.polyps && measurement.polypCount > maxCount)
    || (visibleSeries.ephyrae && measurement.ephyraeCount > maxCount)
  ));
  function handleMeasurementKey(
    event: KeyboardEvent<SVGGElement>,
    detail: ActiveDetail,
    measurementIndex: number,
  ) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setPinnedDetail(detail);
      return;
    }
    if (dismissPinnedDetail(event)) return;

    const targetIndex = event.key === 'ArrowLeft'
      ? measurementIndex - 1
      : event.key === 'ArrowRight'
        ? measurementIndex + 1
        : event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? plottedReadingDetails.length - 1
            : null;
    if (targetIndex == null) return;

    event.preventDefault();
    const nextIndex = Math.max(0, Math.min(plottedReadingDetails.length - 1, targetIndex));
    const nextReading = plottedReadingDetails[nextIndex];
    if (!nextReading) return;
    event.currentTarget.ownerSVGElement
      ?.querySelector<SVGGElement>(`[data-measurement-index="${nextIndex}"]`)
      ?.focus();
  }

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    const svg = svgRef.current;
    const panel = tooltipRef.current;
    if (!visibleDetail || !canvas || !svg || !panel) return;

    function positionDetail() {
      if (!canvas || !svg || !panel || !visibleDetail) return;
      const matrix = svg.getScreenCTM();
      const offsetParent = panel.offsetParent;
      if (!matrix || !offsetParent) return;
      const bounds = canvas.getBoundingClientRect();
      const point = new DOMPoint(visibleDetail.left / 100 * width, visibleDetail.top).matrixTransform(matrix);
      const anchor = { x: point.x - bounds.left, y: point.y - bounds.top };
      if (anchor.x < 0 || anchor.x > bounds.width || anchor.y < 0 || anchor.y > bounds.height) {
        panel.style.visibility = 'hidden';
        return;
      }
      panel.style.maxWidth = `${Math.max(0, bounds.width - 16)}px`;
      const naturalHeight = Math.max(panel.scrollHeight + panel.offsetHeight - panel.clientHeight, panel.offsetHeight);
      const position = placeChartDetail(anchor, bounds, { width: panel.offsetWidth, height: naturalHeight });
      panel.style.maxHeight = `${position.maxHeight}px`;
      const parentBounds = offsetParent.getBoundingClientRect();
      panel.style.left = `${bounds.left - parentBounds.left + position.left}px`;
      panel.style.top = `${bounds.top - parentBounds.top + position.top}px`;
      panel.style.visibility = 'visible';
    }

    positionDetail();
    const observer = new ResizeObserver(positionDetail);
    observer.observe(canvas);
    observer.observe(svg);
    observer.observe(panel);
    canvas.addEventListener('scroll', positionDetail, { passive: true });
    return () => {
      observer.disconnect();
      canvas.removeEventListener('scroll', positionDetail);
    };
  });

  return (
    <div className={compact ? 'bio-trend is-compact' : 'bio-trend'}>
      <div ref={canvasRef} className="bio-trend-canvas">
        <svg
          ref={svgRef}
          className="bio-trend-svg"
          viewBox={`0 0 ${width} ${countHeight}`}
          role="group"
          aria-label={labels.chartTitle}
          onClick={() => setPinnedDetail(null)}
        >
          {locationBands.map((band) => {
            const detail: ActiveDetail = {
              id: `location-${band.id}`,
              left: ((band.x1 + band.width / 2) / width) * 100,
              top: padding.top + zoneBandHeight,
              title: band.name,
              lines: [{ kind: 'location', label: labels.location, value: `${formatDisplayDate(band.startDate)} - ${formatDisplayDate(band.endDate)}` }],
            };
            return <g key={band.id} className="bio-trend-location-band" role="button" tabIndex={0}
              aria-label={`${band.name}, ${formatDisplayDate(band.startDate)} - ${formatDisplayDate(band.endDate)}`}
              onFocus={() => setFocusedDetail(detail)}
              onBlur={() => setFocusedDetail(null)}
              onMouseEnter={() => setHoveredDetail(detail)}
              onMouseLeave={() => setHoveredDetail(null)}
              onClick={(event) => stopAndToggle(event, detail)}
              onKeyDown={(event) => handleDetailKey(event, detail)}
            >
              <line className="bio-trend-location-band-baseline" x1={band.x1} x2={band.x1 + band.width} y1={padding.top + zoneBandHeight} y2={padding.top + zoneBandHeight} />
              <line className="bio-trend-location-hit" x1={band.x1 - (band.width < 20 ? 10 : 0)} x2={band.x1 + band.width + (band.width < 20 ? 10 : 0)} y1={padding.top + zoneBandHeight} y2={padding.top + zoneBandHeight} />
              {band.width >= (compact ? 72 : 92) ? (
                <text x={band.x1 + 4} y={padding.top + 9}>{band.name}</text>
              ) : null}
              {band.x1 > padding.left + 1 ? (
                <line className="bio-trend-location-change" x1={band.x1} x2={band.x1} y1={padding.top + 2} y2={padding.top + zoneBandHeight + 3} />
              ) : null}
            </g>;
          })}

          {timeTicks.map((tick) => (
            <g key={tick.date} className="bio-trend-time-tick">
              <line
                x1={tick.x}
                x2={tick.x}
                y1={plotTop}
                y2={countHeight - padding.bottom}
              />
              <text
                x={tick.x}
                y={countHeight - 9}
                textAnchor="middle"
              >
                {tick.label}
              </text>
            </g>
          ))}

          {[0.25, 0.5, 0.75].map((ratio) => {
            const y = plotTop + ratio * plotHeight;
            return <line key={ratio} className="bio-trend-grid" x1={padding.left} y1={y} x2={width - padding.right} y2={y} />;
          })}
          <line className="bio-trend-axis" x1={padding.left} y1={countHeight - padding.bottom} x2={width - padding.right} y2={countHeight - padding.bottom} />
          <line className="bio-trend-axis" x1={padding.left} y1={plotTop} x2={padding.left} y2={countHeight - padding.bottom} />

          {selectedReading ? (
            <line
              className="bio-trend-selection-guide"
              x1={selectedReading.x}
              x2={selectedReading.x}
              y1={plotTop}
              y2={countHeight - padding.bottom}
            />
          ) : null}

          {visibleSeries.polyps && measurementSegments.map((segment, index) => (
            <path key={`polyps-${index}`} className="bio-trend-line is-polyps" d={countLine((point) => point.polypCount)(segment) ?? ''} />
          ))}
          {visibleSeries.ephyrae && measurementSegments.map((segment, index) => (
            <path key={`ephyrae-${index}`} className="bio-trend-line is-ephyrae" d={countLine((point) => point.ephyraeCount)(segment) ?? ''} />
          ))}

          {eventPoints.map(({ event, x }) => {
            const eventTitle = event.title || labels.movement || 'Transfert';
            const detail = {
              id: `event-${event.id}`,
              left: (x / width) * 100,
              top: plotTop + 6,
              title: `${eventTitle} - ${formatDisplayDate(event.date)}`,
              lines: event.detail
                ? [{ kind: 'location' as const, label: labels.location, value: event.detail }]
                : [],
            };
            return (
              <g
                key={event.id}
                className={`bio-trend-event is-${event.kind ?? 'movement'}`}
                role="button"
                tabIndex={0}
                aria-label={`${eventTitle} ${formatDisplayDate(event.date)}`}
                transform={`translate(${x} ${padding.top})`}
                onClick={(clickEvent) => stopAndToggle(clickEvent, detail)}
                onKeyDown={(keyEvent) => handleDetailKey(keyEvent, detail)}
                onMouseEnter={() => setHoveredDetail(detail)}
                onMouseLeave={() => setHoveredDetail(null)}
                onFocus={() => setFocusedDetail(detail)}
                onBlur={() => setFocusedDetail(null)}
              >
                <line
                  className="bio-trend-event-hit-area"
                  x1={0}
                  y1={event.kind === 'movement' ? 0 : plotTop - padding.top}
                  x2={0}
                  y2={chartAreaHeight}
                />
                <line
                  className="bio-trend-event-line"
                  x1={0}
                  y1={event.kind === 'movement' ? 0 : plotTop - padding.top}
                  x2={0}
                  y2={chartAreaHeight}
                />
                {event.kind !== 'movement' ? <path d="M0 0 L6 6 L0 12 L-6 6 Z" /> : null}
                {event.kind === 'movement' ? (
                  <text
                    className="bio-trend-event-label"
                    x={-chartAreaHeight / 2}
                    y={-8}
                    textAnchor="middle"
                    transform="rotate(-90)"
                  >
                    {(labels.movement ?? 'Transfert').toLocaleUpperCase('fr-FR')}
                  </text>
                ) : null}
              </g>
            );
          })}

          {plottedReadingDetails.map(({ detail, measurement, x }, measurementIndex) => {
            return (
              <g
                key={measurement.id}
                className={`bio-trend-measurement${selectedReading?.detail.id === detail.id ? ' is-selected' : ''}`}
                data-measurement-index={measurementIndex}
                role="button"
                tabIndex={0}
                aria-pressed={selectedReading?.detail.id === detail.id}
                aria-label={`${formatDisplayDate(measurement.date)}, ${detail.lines.map((item) => `${item.label}: ${item.value}`).join(', ')}`}
                onClick={(clickEvent) => selectMeasurement(clickEvent, detail)}
                onMouseEnter={() => setHoveredDetail(detail)}
                onMouseLeave={() => setHoveredDetail(null)}
                onFocus={() => setFocusedDetail(detail)}
                onBlur={() => setFocusedDetail(null)}
                onKeyDown={(keyEvent) => handleMeasurementKey(keyEvent, detail, measurementIndex)}
              >
                {visibleSeries.polyps ? <circle className={`bio-trend-dot is-polyps${visibleSeries.ephyrae && measurement.polypCount === measurement.ephyraeCount ? ' is-overlapping' : ''}`} cx={x} cy={yCount(measurement.polypCount)} r={measurement.polypCount === 0 ? 2.8 : 2.15} /> : null}
                {visibleSeries.ephyrae ? <circle className={`bio-trend-dot is-ephyrae${measurement.ephyraeCount === 0 ? ' is-zero' : ''}${visibleSeries.polyps && measurement.polypCount === measurement.ephyraeCount ? ' is-overlapping' : ''}`} cx={x} cy={yCount(measurement.ephyraeCount)} r={measurement.ephyraeCount === 0 ? 2.8 : 2.15} /> : null}
                {visibleSeries.polyps && measurement.polypCount > maxCount ? (
                  <path
                    className="bio-trend-overflow is-polyps"
                    d={`M${x - 4} ${plotTop + 8} L${x} ${plotTop + 1} L${x + 4} ${plotTop + 8} Z`}
                  />
                ) : null}
                {visibleSeries.ephyrae && measurement.ephyraeCount > maxCount ? (
                  <path
                    className="bio-trend-overflow is-ephyrae"
                    d={`M${x - 4} ${plotTop + 14} L${x} ${plotTop + 7} L${x + 4} ${plotTop + 14} Z`}
                  />
                ) : null}
                <rect className="bio-trend-hit-area" x={x - 13} y={plotTop} width={26} height={plotHeight} />
              </g>
            );
          })}

          {!plottedMeasurements.length ? (
            <text className="bio-trend-empty" x={width / 2} y={plotTop + plotHeight / 2}>{labels.empty}</text>
          ) : null}

          <text className="bio-trend-label" x={padding.left} y={countHeight - 9}>{formatDisplayDate(start)}</text>
          <text className="bio-trend-label is-end" x={width - padding.right} y={countHeight - 9}>{formatDisplayDate(end)}</text>
          {[maxCount, 750, 500, 250, 0].map((value) => (
            <text
              key={value}
              className="bio-trend-y-label"
              x={padding.left - 8}
              y={yCount(value) + 4}
            >
              {value}
            </text>
          ))}
        </svg>

      </div>

      {visibleDetail ? (
        <div ref={tooltipRef} className={`bio-trend-tooltip${pinnedVisibleDetail?.id === visibleDetail.id ? ' is-pinned' : ''}`} role="group" aria-label={visibleDetail.title}>
          <div className="bio-trend-tooltip-heading">
            <strong>{visibleDetail.title}</strong>
            {pinnedVisibleDetail?.id === visibleDetail.id ? (
              <button type="button" aria-label={labels.closeDetail} onClick={() => {
                setPinnedDetail(null);
                const index = selectedReading ? plottedReadingDetails.indexOf(selectedReading) : -1;
                if (index >= 0) {
                  svgRef.current?.querySelector<SVGGElement>(`[data-measurement-index="${index}"]`)?.focus();
                }
              }}>×</button>
            ) : null}
          </div>
          {visibleDetail.id.startsWith('measurement-') ? (
            <>
              <div className="bio-trend-tooltip-values">
                {visibleDetail.lines.filter((item) => item.kind === 'polyps' || item.kind === 'ephyrae' || item.kind === 'salinity').map((item) => (
                  <span key={item.kind} className={`is-${item.kind}`}>
                    <small>{item.label}</small><strong>{item.value}</strong>
                  </span>
                ))}
              </div>
              {visibleDetail.lines.some((item) => item.kind === 'location' || item.kind === 'user') ? (
                <div className="bio-trend-tooltip-metadata">
                  {visibleDetail.lines.filter((item) => item.kind === 'location' || item.kind === 'user').map((item) => (
                    <span className="bio-trend-tooltip-context" key={item.kind}>
                      <small>{item.label}</small><strong>{item.value}</strong>
                    </span>
                  ))}
                </div>
              ) : null}
              {visibleDetail.lines.filter((item) => item.kind === 'note').map((item) => (
                <span className="bio-trend-tooltip-note" key={item.kind}>
                  <small>{item.label}</small><strong>{item.value}</strong>
                </span>
              ))}
            </>
          ) : visibleDetail.lines.map((item) => (
            <span className="bio-trend-tooltip-context" key={item.kind}>
              <small>{item.label}</small><strong>{item.value}</strong>
            </span>
          ))}
        </div>
      ) : null}

      <div className="bio-trend-legend bio-trend-legend--footer" role="group" aria-label={labels.chartTitle}>
        <button type="button" className={`is-polyps${visibleSeries.polyps ? '' : ' is-hidden'}`} aria-pressed={visibleSeries.polyps}
          onClick={() => setVisibleSeries((current) => toggleChartSeries(current, 'polyps'))}>{labels.polyps}</button>
        <button type="button" className={`is-ephyrae${visibleSeries.ephyrae ? '' : ' is-hidden'}`} aria-pressed={visibleSeries.ephyrae}
          onClick={() => setVisibleSeries((current) => toggleChartSeries(current, 'ephyrae'))}>{labels.ephyrae}</button>
        {hasOverflow ? <span className="is-overflow">&gt; {maxCount}</span> : null}
      </div>
    </div>
  );
}

function buildMeasurementDetailLines(
  measurement: TrendMeasurement,
  labels: TrendLabels,
  locations: TrendLocation[],
  knownLocationName?: string,
): DetailLine[] {
  const locationName = knownLocationName ?? findChartLocationAtDate(locations, measurement.date);
  const lines: DetailLine[] = chartBiologicalValues(measurement, labels);

  if (locationName) {
    lines.push({ kind: 'location', label: labels.location, value: locationName });
  }

  if (labels.enteredBy && measurement.enteredBy?.trim()) {
    lines.push({ kind: 'user', label: labels.enteredBy, value: measurement.enteredBy.trim() });
  }

  if (labels.observation && measurement.note?.trim()) {
    lines.push({
      kind: 'note',
      label: labels.observation,
      value: measurement.note.trim(),
    });
  }

  return lines;
}

function buildGeometry(
  measurements: TrendMeasurement[],
  locations: TrendLocation[],
  events: TrendEvent[],
  startDate: string,
  endDate: string,
  compact: boolean,
) {
  // The overview still needs enough drawing space to remain readable inside a
  // two-column card layout. CSS scales this wider canvas without crushing text.
  const width = compact ? 640 : 860;
  const countHeight = compact ? 238 : 260;
  const padding = compact
    ? { top: 6, right: 22, bottom: 34, left: 44 }
    : { top: 8, right: 26, bottom: 34, left: 44 };
  const start = normalizeDate(startDate);
  const requestedEnd = normalizeDate(endDate);
  const end = requestedEnd <= start ? addDays(start, 1) : requestedEnd;
  const startText = toDateString(start);
  const endText = toDateString(end);
  const plottedMeasurements = [...measurements]
    .filter((point) => point.date >= startText && point.date <= endText)
    .sort((left, right) => left.date.localeCompare(right.date));
  const plottedEvents = [...events]
    .filter((event) => event.date >= startText && event.date <= endText)
    .sort((left, right) => left.date.localeCompare(right.date));
  const xScale = scaleTime().domain([start, end]).range([padding.left, width - padding.right]);
  const xPosition = (date: string) => xScale(normalizeDate(date));
  const measurementDates = measurements
    .map((measurement) => measurement.date)
    .sort();
  const latestMeasurementDate = measurementDates[measurementDates.length - 1] ?? '';
  const locationBands = buildLocationBands(
    locations,
    startText,
    endText,
    latestMeasurementDate,
    xPosition,
  );
  const zoneBandHeight = locationBands.length ? (compact ? 20 : 22) : 0;
  const zoneBandGap = locationBands.length ? 4 : 0;
  const plotTop = padding.top + zoneBandHeight + zoneBandGap;
  const plotHeight = countHeight - padding.bottom - plotTop;
  const chartAreaHeight = countHeight - padding.bottom - padding.top;
  const maxCount = BIOLOGICAL_COUNT_AXIS_MAX;
  const yCount = scaleLinear()
    .domain([0, maxCount])
    .range([countHeight - padding.bottom, plotTop])
    .clamp(true);
  const countLine = (selector: (point: TrendMeasurement) => number) => line<TrendMeasurement>()
    .x((point) => xPosition(point.date))
    .y((point) => yCount(selector(point)));

  const timeTicks = buildTimeTicks(start, end, xPosition, padding, width, compact);
  const explicitEventPoints = plottedEvents.map((event) => ({
    event,
    x: resolveEventX(event, locationBands, xPosition),
  }));
  const generatedTransferPoints = buildZoneTransitions(locationBands)
    .filter((transition) => !explicitEventPoints.some(({ event, x }) => (
      event.kind === 'movement' && Math.abs(x - transition.x) <= 2
    )))
    .map((transition) => ({
      event: {
        id: `zone-transition-${transition.id}`,
        date: transition.date,
        detail: `${transition.from} -> ${transition.to}`,
        kind: 'movement' as const,
        title: '',
      },
      x: transition.x,
    }));

  return {
    countHeight,
    countLine,
    chartAreaHeight,
    end: endText,
    eventPoints: [...explicitEventPoints, ...generatedTransferPoints]
      .sort((left, right) => left.x - right.x),
    locationBands,
    maxCount,
    measurementSegments: splitMeasurementsOnGaps(plottedMeasurements),
    padding,
    plotHeight,
    plotTop,
    plottedMeasurements,
    start: startText,
    timeTicks,
    width,
    xPosition,
    yCount,
    zoneBandHeight,
  };
}

function buildTimeTicks(
  start: Date,
  end: Date,
  xPosition: (date: string) => number,
  padding: { left: number; right: number },
  width: number,
  compact: boolean,
): TimeTick[] {
  const candidates: Date[] = [];
  const cursor = new Date(start.getFullYear(), start.getMonth() + 1, 1);
  while (cursor < end) {
    candidates.push(new Date(cursor));
    cursor.setMonth(cursor.getMonth() + 1);
  }

  const maxTicks = compact ? 3 : 6;
  const step = Math.max(1, Math.ceil(candidates.length / maxTicks));
  const edgeClearance = compact ? 104 : 92;
  return candidates
    .filter((_, index) => index % step === 0)
    .map((date) => {
      const dateText = toDateString(date);
      return {
        date: dateText,
        label: `${String(date.getMonth() + 1).padStart(2, '0')}/${date.getFullYear()}`,
        x: xPosition(dateText),
      };
    })
    .filter((tick) => (
      tick.x >= padding.left + edgeClearance
      && tick.x <= width - padding.right - edgeClearance
    ));
}

function buildLocationBands(
  locations: TrendLocation[],
  startDate: string,
  endDate: string,
  latestMeasurementDate: string,
  xPosition: (date: string) => number,
): LocationBand[] {
  return resolveChartLocationPeriods(locations, endDate, latestMeasurementDate)
    .filter((location) => (
      location.startDate <= endDate && location.endDate >= startDate
    ))
    .map((location) => {
      const clippedStart = location.startDate < startDate ? startDate : location.startDate;
      const clippedEnd = location.endDate > endDate ? endDate : location.endDate;
      const x1 = xPosition(clippedStart);
      const x2 = xPosition(clippedEnd);
      return {
        endDate: clippedEnd,
        id: location.id,
        name: location.name,
        startDate: clippedStart,
        width: Math.max(2, x2 - x1),
        x1,
      };
    });
}

function buildZoneTransitions(locationBands: LocationBand[]) {
  return locationBands.flatMap((band, index) => {
    const previousBand = locationBands[index - 1];
    if (!previousBand || previousBand.name === band.name || daysBetween(previousBand.endDate, band.startDate) > 1) return [];

    return [{
      date: band.startDate,
      from: previousBand.name,
      id: `${previousBand.id}-${band.id}`,
      to: band.name,
      x: band.x1,
    }];
  });
}

function resolveEventX(
  event: TrendEvent,
  locationBands: LocationBand[],
  xPosition: (date: string) => number,
) {
  const eventX = xPosition(event.date);
  if (event.kind !== 'movement') return eventX;

  const eventDay = event.date.slice(0, 10);
  const boundaries = locationBands.flatMap((band) => [
    { date: band.startDate, x: band.x1 },
    { date: band.endDate, x: band.x1 + band.width },
  ]);
  const exactBoundary = boundaries.find((boundary) => boundary.date === eventDay);
  if (exactBoundary) return exactBoundary.x;

  const closestBoundary = boundaries.reduce<{ distance: number; x: number } | null>((closest, boundary) => {
    const distance = Math.abs(boundary.x - eventX);
    if (!closest || distance < closest.distance) {
      return { distance, x: boundary.x };
    }
    return closest;
  }, null);

  return closestBoundary && closestBoundary.distance <= 18 ? closestBoundary.x : eventX;
}



function normalizeDate(value: string) {
  return new Date(`${value.slice(0, 10)}T00:00:00`);
}

function addDays(date: Date, days: number) {
  const result = new Date(date);
  result.setDate(result.getDate() + days);
  return result;
}

function toDateString(date: Date) {
  const result = new Date(date);
  result.setMinutes(result.getMinutes() - result.getTimezoneOffset());
  return result.toISOString().slice(0, 10);
}

function daysBetween(first: string, second: string) {
  return Math.round((normalizeDate(second).getTime() - normalizeDate(first).getTime()) / 86_400_000);
}
